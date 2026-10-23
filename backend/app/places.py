"""Hospitals and police stations from OpenStreetMap, and hospital selection."""
import random
import osmnx as ox
from .routing import dynamic_astar, dist_m

SPECIALTIES = ['Cardiac', 'Trauma', 'Neuro', 'Burns']


def _features(center, radius_m, tags, default_name):
    try:
        gdf = ox.features_from_point(center, tags=tags, dist=radius_m)
    except Exception:                       # none mapped here, or the download failed
        return []
    out = []
    for _, row in gdf.iterrows():
        name = row.get('name')
        p = row.geometry.representative_point()
        out.append({'name': name if isinstance(name, str) else default_name, 'lat': p.y, 'lon': p.x})
    return out


def download_places(center, radius_m, seed=7):
    """Hospitals get simulated capacity; edit the saved JSON to put in real values."""
    rng = random.Random(seed)
    hospitals = [h for h in _features(center, radius_m, {'amenity': 'hospital'}, None) if h['name']]
    for h in hospitals:
        h['beds_free'] = rng.randint(0, 8)
        h['er_wait_min'] = rng.randint(5, 40)
        h['specialties'] = ['General'] + rng.sample(SPECIALTIES, rng.randint(0, 2))
    police = _features(center, radius_m, {'amenity': 'police'}, 'Police station')
    return {'hospitals': hospitals, 'police': police}


def rank_hospitals(G, start, hospitals, cost_at, condition, t0=0.0, top=5):
    near = sorted(hospitals, key=lambda h: dist_m(G, start, h['node']))[:top]
    ranked = []
    for h in near:
        try:
            path, sec = dynamic_astar(G, start, h['node'], cost_at, t0)
        except ValueError:
            continue                        # no route right now
        specs = h.get('specialties') or ['General']
        match = condition == 'General' or condition in specs
        beds = h.get('beds_free')
        score = (sec / 60 + 0.5 * (h.get('er_wait_min') or 15)
                 + (0 if match else 12) + (30 if beds is not None and beds <= 0 else 0))
        ranked.append({'hospital': h, 'path': path, 'eta_s': sec, 'score': score, 'match': match})
    return sorted(ranked, key=lambda r: r['score'])


def pick_hospital(G, start, hospitals, cost_at, condition, t0=0.0):
    ranked = rank_hospitals(G, start, hospitals, cost_at, condition, t0)
    return ranked[0] if ranked else None
