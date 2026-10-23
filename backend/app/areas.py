"""Areas: the app loads the road map around a location the first time it is used there.

Each area is a circle of AREA_RADIUS_M around a point. It is downloaded once from
OpenStreetMap (20-90 s), saved in data/areas/, and loaded instantly after that.
"""
import asyncio, json, math, random
from pathlib import Path

import networkx as nx
import osmnx as ox

from .graph import download_graph, load_graph, save_graph
from .places import download_places
from .routing import make_cost_at, set_vmax
from .traffic import TrafficSim

DATA = Path(__file__).resolve().parent.parent / 'data'
AREAS_DIR = DATA / 'areas'
INDEX = AREAS_DIR / 'index.json'
AREA_RADIUS_M = 6000            # size of each downloaded area
EDGE_MARGIN_M = 1500            # a point this close to an area's edge gets a new area instead


def dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * 111320, (lon1 - lon2) * 111320 * math.cos(math.radians(lat1)))


def _nodes(area, items):
    for p in items or []:
        p['node'] = area.snap(p['lat'], p['lon'])


MIN_AMBULANCES = 8          # an area always gets at least this many ambulances


class Area:
    def __init__(self, key, center, radius, G, places):
        self.key, self.center, self.radius, self.G = key, center, radius, G
        self.hospitals, self.police = places['hospitals'], places['police']
        # Snap places only to junctions in the main strongly connected part of the road network.
        # (OSM maps have one-way dead ends near the edge: an ambulance can drive in but never out,
        # which used to leave it stuck "with patient" because no hospital could be reached.)
        core = max(nx.strongly_connected_components(G), key=len)
        self._ids = [n for n in G.nodes if n in core]
        self._ys = [G.nodes[n]['y'] for n in self._ids]
        self._xs = [G.nodes[n]['x'] for n in self._ids]
        self._kx = math.cos(math.radians(center[0]))
        _nodes(self, self.hospitals)
        _nodes(self, self.police)
        # Standby ambulances: an area always has at least MIN_AMBULANCES, even when few (or no) hospitals
        # and police stations could be downloaded for it.
        self.standby = []
        missing = MIN_AMBULANCES - len(self.hospitals) - len(self.police)
        for i in range(max(0, missing)):
            ang = 2 * math.pi * i / max(1, missing)
            r_m = radius * (0.25 if i % 2 else 0.55)
            lat = center[0] + r_m * math.sin(ang) / 111320
            lon = center[1] + r_m * math.cos(ang) / (111320 * self._kx)
            node = self.snap(lat, lon)
            self.standby.append({'name': f'Standby point {i + 1}', 'lat': G.nodes[node]['y'],
                                 'lon': G.nodes[node]['x'], 'node': node})
        set_vmax(G)
        self.sim = TrafficSim(G, seed=random.randrange(10**6))
        self.predicted = {}
        self.cost_at = make_cost_at(G, self.sim.density, self.predicted, self.sim.impair)
        for _ in range(12):
            self.sim.step(5)                    # one minute of warm-up traffic

    def snap(self, lat, lon):
        """Nearest junction (in the connected core) to a point."""
        kx, best, bd = self._kx, None, float('inf')
        for n, y, x in zip(self._ids, self._ys, self._xs):
            d = (y - lat) ** 2 + ((x - lon) * kx) ** 2
            if d < bd:
                best, bd = n, d
        return best

    def info(self):
        return {'key': self.key, 'center': list(self.center), 'radius_m': self.radius,
                'hospitals': len(self.hospitals), 'police': len(self.police), 'standby': len(self.standby)}


class AreaManager:
    def __init__(self):
        AREAS_DIR.mkdir(parents=True, exist_ok=True)
        self.index = json.loads(INDEX.read_text()) if INDEX.exists() else []
        self.areas = {}                         # key -> Area (loaded in memory)
        self.pending = {}                       # key -> (center, asyncio.Task)
        self._migrate_v1_nagpur()

    def _migrate_v1_nagpur(self):
        """Reuse the Nagpur map downloaded in version 1 so it is not downloaded again."""
        old_graph, old_hosp = DATA / 'nagpur.graphml', DATA / 'hospitals.json'
        if old_graph.exists() and not any(a['key'] == 'nagpur' for a in self.index):
            (AREAS_DIR / 'nagpur.graphml').write_bytes(old_graph.read_bytes())
            if old_hosp.exists():
                hosp = json.loads(old_hosp.read_text())
                for h in hosp:
                    h.pop('node', None)
                (AREAS_DIR / 'nagpur.v1hospitals.json').write_text(json.dumps(hosp))
            self.index.append({'key': 'nagpur', 'lat': 21.1458, 'lon': 79.0882, 'radius': 5000})
            self._save_index()

    def _save_index(self):
        INDEX.write_text(json.dumps(self.index, indent=2))

    def find(self, lat, lon):
        """Key of a known area that covers this point (with margin), or None."""
        candidates = [(a['key'], a['lat'], a['lon'], a['radius']) for a in self.index]
        candidates += [(k, c[0], c[1], AREA_RADIUS_M) for k, (c, _) in self.pending.items()]
        best = None
        for key, alat, alon, r in candidates:
            d = dist_m(lat, lon, alat, alon)
            if d <= r - EDGE_MARGIN_M and (best is None or d < best[1]):
                best = (key, d)
        return best[0] if best else None

    def area_at(self, lat, lon):
        """Loaded area covering this point, or None (does not start a download)."""
        key = self.find(lat, lon)
        return self.areas.get(key) if key else None

    async def get(self, lat, lon):
        """Area covering this point; loads or downloads it if needed. Raises ValueError if impossible."""
        key = self.find(lat, lon)
        if key in self.areas:
            return self.areas[key]
        if key in self.pending:
            return await self.pending[key][1]
        if key is None:
            key, center, radius = f'{lat:.3f}_{lon:.3f}', (lat, lon), AREA_RADIUS_M
        else:
            entry = next(a for a in self.index if a['key'] == key)
            center, radius = (entry['lat'], entry['lon']), entry['radius']
        task = asyncio.ensure_future(asyncio.to_thread(self._load_or_download, key, center, radius))
        self.pending[key] = (center, task)
        try:
            area = await task
        finally:
            self.pending.pop(key, None)
        self.areas[key] = area
        if not any(a['key'] == key for a in self.index):
            self.index.append({'key': key, 'lat': center[0], 'lon': center[1], 'radius': radius})
            self._save_index()
        return area

    def _load_or_download(self, key, center, radius):
        gpath, ppath = AREAS_DIR / f'{key}.graphml', AREAS_DIR / f'{key}.places.json'
        if gpath.exists():
            G = load_graph(gpath)
        else:
            try:
                G = download_graph(center, radius)
            except Exception as e:
                raise ValueError(f'Could not load roads for this location ({e.__class__.__name__}). '
                                 'Check the internet connection, or choose a place with roads nearby.')
            save_graph(G, gpath)
        places = json.loads(ppath.read_text()) if ppath.exists() else {'hospitals': [], 'police': []}
        if not places['hospitals'] and not places['police']:      # never downloaded, or the download failed
            for _ in range(2):                                     # the free map server sometimes times out
                places = download_places(center, radius)
                if places['hospitals'] or places['police']:
                    break
            v1 = AREAS_DIR / f'{key}.v1hospitals.json'
            if v1.exists():                     # keep the hospital data edited in version 1
                places['hospitals'] = json.loads(v1.read_text())
            if places['hospitals'] or places['police']:
                ppath.write_text(json.dumps(places, indent=2))   # an empty result is not saved: retried next time
        for p in places['hospitals'] + places['police']:
            p.pop('node', None)
        return Area(key, center, radius, G, places)
