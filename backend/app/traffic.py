import math, random

BASE = {'trunk': .45, 'primary': .45, 'secondary': .35, 'tertiary': .3, 'residential': .15}

class TrafficSim:
    def __init__(self, G, seed=0, level=1.0):
        self.G, self.rng, self.level, self.t = G, random.Random(seed), level, 0
        self.edges = list(G.edges(keys=True))
        self.density = {e: 0.0 for e in self.edges}
        self.impair = {}      # (u, v, k) -> impairment
        self.jams = []        # [{'edges': set, 'until': t, 'add': x}]
        self.incidents = []   # [{'edge': e, 'until': t}]

    def road_type(self, e):
        h = self.G.edges[e].get('highway', 'residential')
        return h[0] if isinstance(h, list) else h

    def step(self, dt=5):
        self.t += dt
        hour = (8 + self.t / 3600) % 24
        rush = 1 + .5 * math.exp(-((hour - 9) / 1.5) ** 2) + .5 * math.exp(-((hour - 18.5) / 1.5) ** 2)
        if self.rng.random() < .01 * self.level * dt / 5:          # new jam
            u, v, k = self.rng.choice(self.edges)
            near = {(u, v, k)} | {(v, w, kk) for w in self.G[v] for kk in self.G[v][w]}
            self.jams.append({'edges': near, 'until': self.t + self.rng.randint(300, 1200), 'add': .4})
        if self.rng.random() < .001 * self.level * dt / 5:         # new accident
            e = self.rng.choice(self.edges)
            self.incidents.append({'edge': e, 'until': self.t + self.rng.randint(600, 1800)})
            self.impair[e] = 1.0
        self.jams = [j for j in self.jams if j['until'] > self.t]
        for i in [i for i in self.incidents if i['until'] <= self.t]:
            self.impair.pop(i['edge'], None)
        self.incidents = [i for i in self.incidents if i['until'] > self.t]
        extra = {}
        for j in self.jams:
            for e in j['edges']:
                extra[e] = max(extra.get(e, 0), j['add'])
        for e in self.edges:
            x = BASE.get(self.road_type(e), .2) * self.level * rush
            x += extra.get(e, 0)
            x += self.rng.gauss(0, .03)
            self.density[e] = min(1.0, max(0.0, x))

    def add_jam_near(self, lat, lon, radius_m=250, add=0.8, duration=400):
        """A whole area slows down: every road within radius_m of (lat, lon)."""
        edges = set()
        for e in self.edges:
            n = self.G.nodes[e[0]]
            dy = (n['y'] - lat) * 111320
            dx = (n['x'] - lon) * 105000
            if dx * dx + dy * dy < radius_m ** 2:
                edges.add(e)
        self.jams.append({'edges': edges, 'until': self.t + duration, 'add': add})
        return len(edges)
