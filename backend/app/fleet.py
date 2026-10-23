"""The ambulance fleet: one ambulance at every hospital and police station, many trips at once.

Ambulance life cycle:
  available -> to_patient -> at_scene -> to_hospital -> at_hospital -> returning -> available
"""
import asyncio, math, random, time, traceback, uuid

from .places import pick_hospital, rank_hospitals
from .routing import dynamic_astar
from .trip import Trip

SCENE_S = 120          # simulated seconds the crew spends with the patient
HANDOVER_S = 180       # simulated seconds to hand over the patient at the hospital
CANDIDATES = 5         # nearest available ambulances compared by real driving time
REROUTE_BANNER_S = 60  # real seconds the 'Rerouting' banner shows; the red old route stays until this leg ends

# Demo crew details. These are generated, not real people; phone numbers are masked on purpose.
FIRST = ['Ramesh', 'Suresh', 'Amit', 'Rahul', 'Vijay', 'Sanjay', 'Prakash', 'Anil', 'Mahesh', 'Nitin',
         'Sachin', 'Rajesh', 'Deepak', 'Manoj', 'Ajay', 'Sunil', 'Pravin', 'Ganesh', 'Kiran', 'Vinod']
LAST = ['Patil', 'Deshmukh', 'Wankhede', 'Thakre', 'Bhoyar', 'Gawande', 'Raut', 'Meshram', 'Kale', 'Pawar',
        'Jadhav', 'Shinde', 'Kulkarni', 'Bhagat', 'Chavan', 'Ingle', 'Dongre', 'Nagpure', 'Sharma', 'Yadav']
MEDIC_FIRST = ['Priya', 'Sneha', 'Pooja', 'Anita', 'Kavita', 'Neha', 'Rohit', 'Akash', 'Swati', 'Meena']


def make_crew(aid, base):
    r = random.Random(aid)
    advanced = base['kind'] == 'hospital'             # hospital ambulances carry a paramedic
    letters = ''.join(r.choice('ABCDEFGHJKLMNPRSTUVWXYZ') for _ in range(2))
    return {
        'vehicle_no': f'MH-31-{letters}-{r.randint(1000, 9999)}',
        'type': 'Advanced Life Support (ALS)' if advanced else 'Basic Life Support (BLS)',
        'driver': f'{r.choice(FIRST)} {r.choice(LAST)}',
        'driver_phone': f'+91 9{r.randint(0, 9)}XXX X{r.randint(1000, 9999)}',
        'paramedic': f'{r.choice(MEDIC_FIRST)} {r.choice(LAST)}' if advanced else None,
    }

STATUS_TEXT = {
    'preparing': 'Loading the map for your area (first time only, about 1 minute)…',
    'waiting': 'All nearby ambulances are busy. Waiting for the next free one…',
    'to_patient': 'Ambulance is on the way to you',
    'at_scene': 'Ambulance has reached you. Crew is attending to the patient',
    'to_hospital': 'Taking the patient to hospital',
    'arrived': 'Patient has reached the hospital',
    'cancelled': 'Request cancelled',
    'failed': 'Could not arrange an ambulance',
}
ACTIVE = {'preparing', 'waiting', 'to_patient', 'at_scene', 'to_hospital'}


def road_name(G, a, b):
    name = G.edges[a, b, next(iter(G[a][b]))].get('name', 'an unnamed road')
    return name[0] if isinstance(name, list) else name


def coords(G, nodes):
    return [[G.nodes[n]['y'], G.nodes[n]['x']] for n in nodes]


class Ambulance:
    def __init__(self, aid, area, base):
        self.id, self.area, self.base = aid, area, base
        self.node, self.status = base['node'], 'available'
        self.trip, self.request, self.wait_until, self.planning = None, None, 0, False
        self.crew = make_crew(aid, base)

    def position(self):
        t, G = self.trip, self.area.G
        if t and not t.arrived():
            a, b = G.nodes[t.path[t.i]], G.nodes[t.path[t.i + 1]]
            return [a['y'] + (b['y'] - a['y']) * t.progress, a['x'] + (b['x'] - a['x']) * t.progress]
        n = G.nodes[self.node]
        return [n['y'], n['x']]

    def admin(self):
        return {'id': self.id, 'status': self.status, 'position': self.position(), 'area': self.area.key,
                'base': self.base['name'], 'base_kind': self.base['kind'], **self.crew,
                'request': self.request.id if self.request else None}

    def card(self):
        return {'id': self.id, 'base': self.base['name'], 'base_kind': self.base['kind'],
                'position': self.position(), **self.crew}


class Request:
    def __init__(self, lat, lon, condition, phone, ip, patient_name=None, landmark=None):
        self.id = uuid.uuid4().hex[:8]
        self.lat, self.lon, self.condition, self.phone, self.ip = lat, lon, condition, phone, ip
        self.patient_name, self.landmark = patient_name, landmark
        self.created = time.time()
        self.status, self.ambulance, self.hospital, self.area = 'preparing', None, None, None
        self.patient_node, self.reroutes, self.events = None, 0, []
        self.demo_count, self.since_demo, self.next_retry = 0, 0, 0
        self.forced_legs = set()                # legs that already had their demo-mode accident
        self.error = None

    def log(self, kind, text, public=True):
        self.events.append({'t': round(time.time() - self.created), 'type': kind, 'text': text, 'public': public})
        del self.events[:-80]

    def leg_trip(self):
        a = self.ambulance
        if a and a.request is self and a.status in ('to_patient', 'to_hospital') and a.trip:
            return a.trip
        return None

    def public(self, admin=False, marks=()):
        t = self.leg_trip()
        eta = t.eta() if t else None
        a = self.ambulance
        has_old = bool(t and t.old_route)
        return {
            'id': self.id, 'status': self.status, 'status_text': self.error or STATUS_TEXT.get(self.status, self.status),
            'condition': self.condition, 'patient': [self.lat, self.lon],
            'patient_name': self.patient_name, 'landmark': self.landmark, 'phone': self.phone,
            'ambulance': a and a.card(),
            'route': coords(self.area.G, t.path[t.i:]) if t else [],
            'old_route': coords(self.area.G, t.old_route) if has_old else [],    # shown in red after a reroute
            'rerouting': has_old and time.time() - t.old_route_at < REROUTE_BANNER_S,
            'marks': list(marks),
            'eta_s': None if eta is None or math.isinf(eta) else round(eta),
            'hospital': self.hospital and {k: self.hospital[k] for k in ('name', 'lat', 'lon')},
            'reroutes': self.reroutes,
            'events': [e for e in self.events if admin or e['public']][-30:],
            **({'area': self.area and self.area.key, 'age_s': round(time.time() - self.created)} if admin else {}),
        }


class Fleet:
    def __init__(self, settings):
        self.settings = settings
        self.areas, self.ambulances, self.requests = {}, {}, {}
        self.marks = []                         # accidents / jams / closures shown on the maps

    def add_mark(self, area, kind, pos, until_sim, text):
        self.marks.append({'area': area, 'type': kind, 'pos': pos, 'until': until_sim, 'text': text})

    def active_marks(self, area=None):
        self.marks = [m for m in self.marks if m['area'].sim.t < m['until']]
        return [{'type': m['type'], 'pos': m['pos'], 'text': m['text']}
                for m in self.marks if area is None or m['area'] is area]

    def request_view(self, req, admin=False):
        return req.public(admin=admin, marks=self.active_marks(req.area) if req.area else ())

    # ---------- setup ----------
    def add_area(self, area):
        if area.key in self.areas:
            return
        self.areas[area.key] = area
        bases = ([dict(h, kind='hospital') for h in area.hospitals] + [dict(p, kind='police') for p in area.police]
                 + [dict(p, kind='standby') for p in area.standby])
        for base in bases:
            aid = f'AMB-{len(self.ambulances) + 1:04d}'
            self.ambulances[aid] = Ambulance(aid, area, base)

    def in_area(self, area, status=None):
        return [a for a in self.ambulances.values() if a.area is area and (status is None or a.status == status)]

    # ---------- dispatch ----------
    async def dispatch(self, req):
        area = req.area
        G = area.G
        if req.patient_node is None:
            req.patient_node = area.snap(req.lat, req.lon)
        free = self.in_area(area, 'available')
        n = G.nodes[req.patient_node]
        free.sort(key=lambda a: (G.nodes[a.node]['y'] - n['y']) ** 2 + (G.nodes[a.node]['x'] - n['x']) ** 2)
        candidates = free[:CANDIDATES]
        if not candidates:
            if req.status != 'waiting':
                req.status = 'waiting'
                total = len(self.in_area(area))
                req.log('info', 'All nearby ambulances are busy. You are next in the queue.' if total else
                        'No ambulances in this area yet. Retrying.')
            req.next_retry = time.time() + 10
            return
        plans = await asyncio.to_thread(self._plan_many, area, [a.node for a in candidates], req.patient_node)
        options = [(sec, a, path) for a, (path, sec) in zip(candidates, plans)
                   if path and a.status == 'available']
        if not options:
            if req.status != 'waiting':
                req.log('info', 'No ambulance can reach this spot by road right now. Retrying.')
            req.status, req.next_retry = 'waiting', time.time() + 10
            return
        sec, amb, path = min(options, key=lambda o: o[0])
        amb.status, amb.request, amb.trip = 'to_patient', req, Trip(G, path, req.patient_node, area.cost_at)
        req.ambulance, req.status, req.since_demo = amb, 'to_patient', 0
        req.log('dispatch', f'{amb.id} dispatched from {amb.base["name"]} ({amb.base["kind"]}). '
                            f'Estimated arrival {math.ceil(sec / 60)} min.')

    @staticmethod
    def _plan_many(area, starts, goal):
        out = []
        for s in starts:
            try:
                out.append(dynamic_astar(area.G, s, goal, area.cost_at))
            except ValueError:
                out.append((None, math.inf))
        return out

    def cancel(self, req):
        if req.status not in ACTIVE:
            return
        amb = req.ambulance
        req.status = 'cancelled'
        req.log('info', 'Request cancelled.')
        if amb and amb.request is req and amb.status in ('to_patient', 'at_scene'):
            self._send_home(amb)

    # ---------- the engine: called once per real second ----------
    def tick(self, tick_s):
        for area in self.areas.values():
            try:
                area.sim.step(tick_s)
            except Exception:
                traceback.print_exc()
        for amb in list(self.ambulances.values()):
            if amb.status == 'available':
                continue
            try:
                self._tick_ambulance(amb, tick_s)
            except Exception:
                traceback.print_exc()
        now = time.time()
        for req in list(self.requests.values()):
            if req.status == 'waiting' and now >= req.next_retry and req.area:
                req.next_retry = now + 10
                asyncio.ensure_future(self.dispatch(req))

    def _tick_ambulance(self, amb, tick_s):
        area, req, s = amb.area, amb.request, amb.status
        if s in ('to_patient', 'to_hospital', 'returning'):
            if amb.trip is None:                # route still being planned
                return
            if req and s != 'returning':
                self._maybe_demo_event(amb, req, tick_s)
            ev = amb.trip.advance_and_check(tick_s)
            if ev and req and s != 'returning':
                if ev['type'] == 'reroute':
                    req.reroutes += 1
                    saved = f', saves about {ev["saved_s"]} s' if ev.get('saved_s') else ' around a closed road'
                    req.log('reroute', f'Rerouted{saved}.')
                else:
                    req.log('check', ev['name'], public=False)
            if amb.trip.arrived():
                amb.node = amb.trip.dest
                if s == 'to_patient':
                    amb.status, amb.wait_until = 'at_scene', area.sim.t + SCENE_S
                    req.status = 'at_scene'
                    req.log('arrived_patient', f'{amb.id} has reached the patient.')
                elif s == 'to_hospital':
                    amb.status, amb.wait_until = 'at_hospital', area.sim.t + HANDOVER_S
                    req.status = 'arrived'
                    req.log('arrived_hospital', f'Arrived at {req.hospital["name"]}.')
                else:
                    amb.status, amb.trip = 'available', None
        elif s == 'at_scene' and area.sim.t >= amb.wait_until and not amb.planning:
            amb.planning = True
            asyncio.ensure_future(self._to_hospital(amb, req))
        elif s == 'at_hospital' and area.sim.t >= amb.wait_until:
            amb.request = None
            self._send_home(amb)

    async def _to_hospital(self, amb, req):
        area = amb.area
        try:
            best = await asyncio.to_thread(pick_hospital, area.G, amb.node, area.hospitals,
                                           area.cost_at, req.condition)
            if best is None:                    # none of the 5 nearest reachable: try every hospital
                ranked = await asyncio.to_thread(rank_hospitals, area.G, amb.node, area.hospitals,
                                                 area.cost_at, req.condition, 0.0, len(area.hospitals))
                best = ranked[0] if ranked else None
            if req.status != 'at_scene':        # cancelled meanwhile
                return
            if best is None:
                amb.wait_until = area.sim.t + 30
                req.log('info', 'No hospital reachable from here right now. Retrying shortly.')
                return
            h = best['hospital']
            req.hospital = h
            amb.trip = Trip(area.G, best['path'], h['node'], area.cost_at)
            amb.status, req.status, req.since_demo = 'to_hospital', 'to_hospital', 0
            why = 'treats ' + req.condition.lower() if best['match'] and req.condition != 'General' else 'best overall'
            req.log('hospital', f'Hospital chosen: {h["name"]} ({why}, {h.get("beds_free", "?")} beds free).')
        except Exception:
            traceback.print_exc()
            amb.wait_until = area.sim.t + 30
            req.log('info', 'Could not choose a hospital (error). Retrying shortly.')
        finally:
            amb.planning = False

    def _send_home(self, amb):
        """Drive back to base; the route is planned in the background."""
        amb.request, amb.trip = None, None
        if amb.node == amb.base['node']:
            amb.status = 'available'
            return
        amb.status = 'returning'

        async def plan():
            try:
                path, _ = await asyncio.to_thread(dynamic_astar, amb.area.G, amb.node, amb.base['node'],
                                                  amb.area.cost_at)
                amb.trip = Trip(amb.area.G, path, amb.base['node'], amb.area.cost_at)
            except Exception:                   # no route home right now: reset at base
                amb.node, amb.status = amb.base['node'], 'available'
        asyncio.ensure_future(plan())

    # ---------- simulated road events ahead of active ambulances ----------
    def _maybe_demo_event(self, amb, req, tick_s):
        st = self.settings
        req.since_demo += tick_s
        # Demo mode: one guaranteed accident on each leg, shortly after the ambulance sets off
        leg = amb.status
        if st.get('demo_mode') and leg not in req.forced_legs and req.since_demo >= st.get('demo_after_s', 10):
            req.forced_legs.add(leg)
            if self.force_accident(amb, req):
                req.since_demo = 0
                return
        if not st['demo_events'] or req.demo_count >= st['max_demo_events']:
            return
        first = req.demo_count == 0 and req.since_demo >= 30
        if not first and req.since_demo < st['demo_every_s']:
            return
        req.since_demo = 0
        ev = self._demo_event(amb)
        if ev:
            req.demo_count += 1
            req.log(ev[0], ev[1])

    def force_accident(self, amb, req):
        """Close a road on the ambulance's route ahead where a detour exists, and reroute right away.

        Used by demo mode and the control room's "Simulate accident" button, so a demo always shows
        a reroute (red old route + blue new route). Returns True if it worked."""
        t, area = amb.trip, amb.area
        if not t or t.arrived():
            return False
        G, sim = area.G, area.sim
        start, last = t.i + 2, len(t.path) - 2          # at least one junction ahead, not the final road
        if last < start:
            return False
        mid = (start + last) // 2
        order = sorted(range(start, last + 1), key=lambda j: abs(j - mid))   # prefer the middle of what is left
        for j in order[:12]:
            a, b = t.path[j], t.path[j + 1]
            closed = [(x, y, k) for x, y in ((a, b), (b, a)) if G.has_edge(x, y) for k in G[x][y]]
            saved = {e: sim.impair.get(e) for e in closed}
            for e in closed:
                sim.impair[e] = 1.0
            try:
                dynamic_astar(G, t.path[t.i + 1], t.dest, area.cost_at)
            except ValueError:                           # no way around here: try another road
                for e, v in saved.items():
                    if v is None:
                        sim.impair.pop(e, None)
                    else:
                        sim.impair[e] = v
                continue
            for e in closed:
                sim.incidents.append({'edge': e, 'until': sim.t + 600})
            na, nb = G.nodes[a], G.nodes[b]
            text = f'Accident reported ahead on {road_name(G, a, b)}: road closed.'
            self.add_mark(area, 'accident', [(na['y'] + nb['y']) / 2, (na['x'] + nb['x']) / 2], sim.t + 600, text)
            req.log('accident', text)
            ev = t.check()                               # react now instead of at the next 5 s check
            if ev and ev['type'] == 'reroute':
                req.reroutes += 1
                req.log('reroute', 'Rerouted around the closed road.')
            return True
        return False

    def _demo_event(self, amb):
        t, area = amb.trip, amb.area
        G, sim = area.G, area.sim
        j = min(t.i + random.randint(3, 8), len(t.path) - 2)
        if j <= t.i + 1:
            return None
        a, b = t.path[j], t.path[j + 1]
        if random.random() < 0.6:                       # accident: road closed for a while
            closed = [(x, y, k) for x, y in ((a, b), (b, a)) if G.has_edge(x, y) for k in G[x][y]]
            for e in closed:
                sim.impair[e] = 1.0
            try:                                         # keep it only if there is a way around
                dynamic_astar(G, t.path[t.i + 1], t.dest, area.cost_at)
            except ValueError:
                for e in closed:
                    sim.impair.pop(e, None)
                return None
            for e in closed:
                sim.incidents.append({'edge': e, 'until': sim.t + 400})
            na, nb = G.nodes[a], G.nodes[b]
            text = f'Accident reported ahead on {road_name(G, a, b)}: road closed.'
            self.add_mark(area, 'accident', [(na['y'] + nb['y']) / 2, (na['x'] + nb['x']) / 2], sim.t + 400, text)
            return 'accident', text
        n = G.nodes[a]
        sim.add_jam_near(n['y'], n['x'])
        text = f'Heavy traffic building on {road_name(G, a, b)}.'
        self.add_mark(area, 'jam', [n['y'], n['x']], sim.t + 400, text)
        return 'jam', text

    # ---------- views ----------
    def overview(self):
        ambs = list(self.ambulances.values())
        counts = {}
        for a in ambs:
            counts[a.status] = counts.get(a.status, 0) + 1
        active = [r for r in self.requests.values() if r.status in ACTIVE]
        recent = sorted([r for r in self.requests.values() if r.status not in ACTIVE],
                        key=lambda r: -r.created)[:15]
        return {
            'areas': [a.info() for a in self.areas.values()],
            'counts': counts, 'total': len(ambs),
            'ambulances': [a.admin() for a in ambs],
            'requests': [self.request_view(r, admin=True) for r in sorted(active, key=lambda r: r.created)],
            'marks': self.active_marks(),
            'recent': [{'id': r.id, 'status': r.status, 'condition': r.condition,
                        'hospital': r.hospital and r.hospital['name'], 'reroutes': r.reroutes} for r in recent],
            'settings': self.settings,
        }
