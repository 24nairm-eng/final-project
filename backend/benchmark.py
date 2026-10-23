"""Benchmark for the research paper.

Runs the project's own routing code (weights.py, routing.py, traffic.py, trip.py)
in four experiments and writes results/results.json:

  E1  search effort: time-dependent Dijkstra vs Dynamic A* (same cost function)
  E2  travel time under traffic and incidents: static vs traffic-aware vs proposed rerouting
  E3  sensitivity of the rerouting threshold dC
  E4  hospital selection: nearest vs fastest vs proposed score

Usage (from the backend folder):
  python benchmark.py                          # synthetic city network (no internet needed)
  python benchmark.py --graph data/areas/nagpur.graphml   # a real downloaded area
"""
import argparse, copy, heapq, json, math, random, statistics as st, time
from pathlib import Path

import networkx as nx

from app import trip as trip_mod
from app.routing import dist_m, dynamic_astar, make_cost_at, set_vmax, static_astar
from app.traffic import TrafficSim
from app.trip import Trip

_orig_astar = trip_mod.dynamic_astar

DT = 5                     # simulation step (s)
WARMUP_STEPS = 60          # 5 minutes of traffic before each trip
CAP_S = 3600               # give up after one simulated hour


# ---------------- networks ----------------
def synthetic_city(n=40, spacing_m=200, seed=1):
    """A jittered grid city: arterials every 5 blocks, a faster ring, irregular side streets."""
    rng = random.Random(seed)
    G = nx.MultiDiGraph(crs='epsg:4326')
    lat0, lon0 = 21.10, 79.04
    dlat, dlon = spacing_m / 111320, spacing_m / 103900
    for r in range(n):
        for c in range(n):
            G.add_node(r * n + c, y=lat0 + (r + rng.uniform(-.25, .25)) * dlat,
                       x=lon0 + (c + rng.uniform(-.25, .25)) * dlon)
    for r in range(n):
        for c in range(n):
            for dr, dc in ((0, 1), (1, 0)):
                r2, c2 = r + dr, c + dc
                if r2 >= n or c2 >= n:
                    continue
                line = r if dr == 0 else c
                ring = line in (5, n - 6)
                arterial = line % 5 == 0
                if not (ring or arterial) and rng.random() < 0.10:
                    continue                                    # missing side street
                hw, sp, ln = (('trunk', 60.0, 3) if ring else ('primary', 50.0, 2) if arterial
                              else ('residential', 30.0, 1))
                a, b = r * n + c, r2 * n + c2
                L = dist_m(G, a, b) * rng.uniform(1.0, 1.12)    # roads are not perfectly straight
                for x, y in ((a, b), (b, a)):
                    G.add_edge(x, y, length=L, speed_kph=sp, lanes=ln, highway=hw, name=f'{hw} {a}-{b}')
    big = max(nx.strongly_connected_components(G), key=len)
    return G.subgraph(big).copy()


def load_real(path):
    import osmnx as ox
    from app.graph import prepare
    return prepare(ox.load_graphml(path))


def free_flow(d):
    return d['length'] / (d['speed_kph'] / 3.6)


# ---------------- E1: search effort ----------------
def td_search(G, s, t, cost_at, use_h):
    """Same algorithm as routing.dynamic_astar, counting expanded nodes. use_h=False -> Dijkstra."""
    from app import routing
    h = (lambda n: dist_m(G, n, t) / routing.V_MAX) if use_h else (lambda n: 0.0)
    g, closed, heap = {s: 0.0}, set(), [(h(s), s)]
    while heap:
        _, u = heapq.heappop(heap)
        if u == t:
            break
        if u in closed:
            continue
        closed.add(u)
        for v, keys in G[u].items():
            c = min(cost_at(u, v, k, g[u]) for k in keys)
            if g[u] + c < g.get(v, math.inf):
                g[v] = g[u] + c
                heapq.heappush(heap, (g[v] + h(v), v))
    return g.get(t, math.inf), len(closed)


def od_pairs(G, k, rng, lo=2000, hi=6000):
    nodes, out = list(G.nodes), []
    while len(out) < k:
        a, b = rng.choice(nodes), rng.choice(nodes)
        if lo <= dist_m(G, a, b) <= hi:
            out.append((a, b))
    return out


def e1(G, pairs):
    sim = TrafficSim(G, seed=11)
    for _ in range(WARMUP_STEPS):
        sim.step(DT)
    cost_at = make_cost_at(G, sim.density, {}, sim.impair)
    rows = []
    for s, t in pairs:
        t0 = time.perf_counter(); cd, nd = td_search(G, s, t, cost_at, False); td = time.perf_counter() - t0
        t0 = time.perf_counter(); ca, na = td_search(G, s, t, cost_at, True); ta = time.perf_counter() - t0
        t0 = time.perf_counter(); dynamic_astar(G, s, t, cost_at); tp = time.perf_counter() - t0
        rows.append({'dij_ms': td * 1e3, 'dij_nodes': nd, 'astar_ms': ta * 1e3, 'astar_nodes': na,
                     'prod_astar_ms': tp * 1e3, 'gap': abs(cd - ca) / cd})
    m = lambda k: st.mean(r[k] for r in rows)
    return {'pairs': len(rows), 'dijkstra_ms': m('dij_ms'), 'dijkstra_nodes': m('dij_nodes'),
            'astar_ms': m('astar_ms'), 'astar_nodes': m('astar_nodes'), 'prod_astar_ms': m('prod_astar_ms'),
            'p95_prod_astar_ms': sorted(r['prod_astar_ms'] for r in rows)[int(.95 * len(rows)) - 1],
            'max_cost_gap': max(r['gap'] for r in rows), 'node_reduction': 1 - m('astar_nodes') / m('dij_nodes')}


# ---------------- E2/E3: driving a trip under traffic ----------------
class Trace:
    """Traffic for one trial, recorded once and replayed for every strategy.

    Traffic does not depend on which route the ambulance takes, so every strategy sees exactly
    the same densities and the same incident at the same time."""
    def __init__(self, G, seed, scenario, s, t):
        self.G, self.scenario = G, scenario
        self.sim = TrafficSim(G, seed=seed)
        types = {e: self.sim.road_type(e) for e in self.sim.edges}
        self.sim.road_type = types.__getitem__                 # speed-up only; same values
        for _ in range(WARMUP_STEPS):
            self.sim.step(DT)
        self.edges = self.sim.edges
        self.density, self.impair = dict(self.sim.density), dict(self.sim.impair)
        self.cost_at = make_cost_at(G, self.density, {}, self.impair)
        # the system's initial plan (traffic-aware, at departure); the incident happens on it
        W = {e: self.cost_at(*e, 0) for e in self.edges}
        self.planned = static_astar(G, s, t, W)
        self.states = [(list(self.sim.density.values()), dict(self.sim.impair))]

    def _fire(self):
        G, path = self.G, self.planned
        k = len(path) // 2
        a, b = path[k], path[k + 1]
        if self.scenario == 'closure':
            for x, y in ((a, b), (b, a)):
                for kk in (G[x][y] if G.has_edge(x, y) else ()):
                    self.sim.impair[(x, y, kk)] = 1.0
                    self.sim.incidents.append({'edge': (x, y, kk), 'until': self.sim.t + 1200})
        elif self.scenario in ('jam', 'congestion'):              # local jam / wide-area congestion
            n = G.nodes[a]
            r = 300 if self.scenario == 'jam' else 800
            self.sim.add_jam_near(n['y'], n['x'], radius_m=r, add=0.9, duration=900)

    def apply(self, step):
        """Load the traffic state after `step` steps into the shared dicts used by cost_at."""
        while len(self.states) <= step:
            if len(self.states) - 1 == 60 // DT:
                self._fire()                                    # incident 60 s after departure
            self.sim.step(DT)
            self.states.append((list(self.sim.density.values()), dict(self.sim.impair)))
        dens, imp = self.states[step]
        for e, x in zip(self.edges, dens):
            self.density[e] = x
        self.impair.clear()
        self.impair.update(imp)


class ReactiveTrip(Trip):
    """Trip whose driver, on reaching a junction where the next road is closed, turns and re-plans
    from there (what any driver does). Used for every strategy, so a closure is never just driven through."""
    forced = 0

    def advance(self, dt):
        while dt > 0 and not self.arrived():
            a, b = self.path[self.i], self.path[self.i + 1]
            if self.progress == 0 and math.isinf(self.cost(a, b, 0)):
                try:
                    new = _orig_astar(self.G, a, self.dest, self.cost_at)[0]
                    self.path = self.path[:self.i] + new
                    self.planned = self.planned[:self.i] + self.costs(new, 0)
                    self.forced += 1
                    continue
                except ValueError:
                    return                              # nowhere to go: wait at the junction
            c = self.drive_cost(a, b)
            need = (1 - self.progress) * c
            if need > dt:
                self.progress += dt / c
                dt = 0
            else:
                dt -= need
                self.i, self.progress = self.i + 1, 0.0


REPLANS = [0]


def _counting_astar(*a, **k):
    REPLANS[0] += 1
    return _orig_astar(*a, **k)


trip_mod.dynamic_astar = _counting_astar


def run_trip(tr, s, t, strategy, delta_c=0.30):
    G = tr.G
    tr.apply(0)
    if strategy == 'dijkstra_freeflow':                  # a standard navigator: distance / speed limit
        path = nx.dijkstra_path(G, s, t, weight=lambda u, v, d: min(free_flow(x) for x in d.values()))
    else:                                                # traffic-aware plan at departure
        path = list(tr.planned)
    trip = ReactiveTrip(G, path, t, tr.cost_at)
    trip_mod.DELTA_C = delta_c
    REPLANS[0] = 0
    step = 0
    while not trip.arrived() and step * DT < CAP_S:
        step += 1
        tr.apply(step)
        if strategy == 'proposed':
            trip.advance_and_check(DT)
        else:
            trip.advance(DT)
    trip_mod.DELTA_C = 0.30
    return {'time_s': step * DT, 'reroutes': trip.reroutes, 'replans': REPLANS[0], 'forced': trip.forced, 'arrived': trip.arrived()}


def _trial(args):
    G, i, s, t, sc, seed, jobs = args
    tr = Trace(G, seed, sc, s, t)
    if len(tr.planned) < 6:
        return None
    return [(i, sc, name, dc, run_trip(tr, s, t, strat, dc)) for name, strat, dc in jobs]


def run_all(G, pairs, scenarios, jobs, seed0, procs):
    from multiprocessing import Pool
    tasks = [(G, i, s, t, sc, seed0 + i, jobs) for sc in scenarios for i, (s, t) in enumerate(pairs)]
    with Pool(procs) as pool:
        out = [r for res in pool.imap_unordered(_trial, tasks) if res for r in res]
    return out


def summarise_e2(rows):
    by = {}
    for i, sc, name, dc, r in rows:
        by.setdefault(sc, {}).setdefault(name, {})[i] = r
    summary = {}
    for sc, d in by.items():
        ids = sorted(d['dijkstra_freeflow'])
        base = [d['dijkstra_freeflow'][i]['time_s'] for i in ids]
        for name, rs in d.items():
            times = [rs[i]['time_s'] for i in ids]
            summary.setdefault(sc, {})[name] = {
                'n': len(ids), 'mean_s': st.mean(times), 'median_s': st.median(times),
                'p90_s': sorted(times)[int(.9 * len(times)) - 1],
                'mean_saving_vs_dijkstra_pct': 100 * st.mean((b - x) / b for b, x in zip(base, times)),
                'reroutes_per_trip': st.mean(rs[i]['reroutes'] for i in ids),
                'replans_per_trip': st.mean(rs[i]['replans'] for i in ids),
                'forced_detours_per_trip': st.mean(rs[i]['forced'] for i in ids),
                'arrived_pct': 100 * st.mean(rs[i]['arrived'] for i in ids)}
        if 'astar_snapshot' in d and 'proposed' in d:
            diff = [d['astar_snapshot'][i]['time_s'] - d['proposed'][i]['time_s'] for i in ids]
            summary[sc]['proposed_vs_snapshot'] = {
                'mean_saved_s': st.mean(diff),
                'ci95_saved_s': 1.96 * st.stdev(diff) / math.sqrt(len(diff)) if len(diff) > 1 else 0.0,
                'mean_saved_pct': 100 * st.mean(x / d['astar_snapshot'][i]['time_s'] for x, i in zip(diff, ids)),
                'better_pct': 100 * st.mean(x > 0 for x in diff),
                'worse_pct': 100 * st.mean(x < 0 for x in diff), 'max_saved_s': max(diff), 'max_lost_s': -min(diff)}
    return summary


def summarise_e3(rows):
    by = {}
    for i, sc, name, dc, r in rows:
        by.setdefault(dc, []).append(r)
    return [{'delta_c': dc, 'n': len(rs), 'mean_s': st.mean(r['time_s'] for r in rs),
             'reroutes_per_trip': st.mean(r['reroutes'] for r in rs),
             'replans_per_trip': st.mean(r['replans'] for r in rs),
             'forced_detours_per_trip': st.mean(r['forced'] for r in rs)} for dc, rs in sorted(by.items())]


# ---------------- E4: hospital choice ----------------
def e4(G, k=300, seed=7):
    from app.places import SPECIALTIES, rank_hospitals
    rng = random.Random(seed)
    nodes = list(G.nodes)
    hospitals = []
    for i in range(10):
        hospitals.append({'name': f'H{i}', 'node': rng.choice(nodes), 'beds_free': rng.randint(0, 8),
                          'er_wait_min': rng.randint(5, 40),
                          'specialties': ['General'] + rng.sample(SPECIALTIES, rng.randint(0, 2))})
    sim = TrafficSim(G, seed=seed)
    for _ in range(WARMUP_STEPS):
        sim.step(DT)
    cost_at = make_cost_at(G, sim.density, {}, sim.impair)
    conds = ['Cardiac', 'Trauma', 'Neuro', 'Burns', 'General']
    res = {'nearest': [], 'fastest': [], 'proposed': []}
    for _ in range(k):
        s, cond = rng.choice(nodes), rng.choice(conds)
        ranked = rank_hospitals(G, s, hospitals, cost_at, cond, top=len(hospitals))
        if not ranked:
            continue
        nearest = min(ranked, key=lambda r: dist_m(G, s, r['hospital']['node']))
        fastest = min(ranked, key=lambda r: r['eta_s'])
        for name, r in (('nearest', nearest), ('fastest', fastest), ('proposed', ranked[0])):
            h = r['hospital']
            res[name].append({'drive_min': r['eta_s'] / 60, 'wait_min': h['er_wait_min'],
                              'match': r['match'], 'bed': h['beds_free'] > 0})
    return {name: {'n': len(rs), 'drive_min': st.mean(x['drive_min'] for x in rs),
                   'wait_min': st.mean(x['wait_min'] for x in rs),
                   'drive_plus_wait_min': st.mean(x['drive_min'] + x['wait_min'] for x in rs),
                   'specialty_match_pct': 100 * st.mean(x['match'] for x in rs),
                   'bed_available_pct': 100 * st.mean(x['bed'] for x in rs)}
            for name, rs in res.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--graph', help='graphml file of a real area (default: synthetic city)')
    ap.add_argument('--pairs', type=int, default=150)
    ap.add_argument('--procs', type=int, default=2)
    ap.add_argument('--out', default='results/results.json')
    a = ap.parse_args()
    G = load_real(a.graph) if a.graph else synthetic_city()
    set_vmax(G)
    rng = random.Random(42)
    info = {'network': a.graph or 'synthetic 40x40 city', 'nodes': G.number_of_nodes(),
            'edges': G.number_of_edges(),
            'road_km': sum(d['length'] for *_, d in G.edges(data=True)) / 2000}
    print(info, flush=True)
    t0 = time.time()
    res = {'network': info}
    res['e1'] = e1(G, od_pairs(G, 200, rng)); print('E1', res['e1'], flush=True)
    pairs = od_pairs(G, a.pairs, rng)
    scen = ['background', 'jam', 'congestion', 'closure']
    e2_jobs = [('dijkstra_freeflow', 'dijkstra_freeflow', .3), ('astar_snapshot', 'astar_snapshot', .3),
               ('proposed', 'proposed', .3)]
    e3_jobs = [(f'dc{dc}', 'proposed', dc) for dc in (0.0, 0.1, 0.2, 0.3, 0.5, 1.0)]
    rows = run_all(G, pairs, scen, e2_jobs + e3_jobs, 100, a.procs)
    res['e2'] = summarise_e2([r for r in rows if r[2] in ('dijkstra_freeflow', 'astar_snapshot', 'proposed')])
    res['e3'] = summarise_e3([r for r in rows if r[2].startswith('dc')])
    print('E3', res['e3'], flush=True)
    res['e4'] = e4(G); print('E4', res['e4'], flush=True)
    res['runtime_s'] = time.time() - t0
    Path(a.out).parent.mkdir(exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps(res['e2'], indent=1))


if __name__ == '__main__':
    main()
