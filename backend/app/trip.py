import math, random, time, uuid
from .routing import dynamic_astar

DELTA_C, CHECK_EVERY = 0.30, 5   # replan if 30% worse; check every 5 s (admin can change DELTA_C)

class Trip:
    def __init__(self, G, path, dest, cost_at):
        self.id = uuid.uuid4().hex[:8]
        self.G, self.dest, self.cost_at = G, dest, cost_at
        self.status, self.hospital, self.reroutes = 'to_patient', None, 0
        self.i, self.progress, self.since_check = 0, 0.0, 0.0   # on edge path[i] -> path[i+1]
        self.path, self.planned = path, self.costs(path, 0.0)
        self.old_route, self.old_route_at = None, 0.0   # the route just abandoned by a reroute

    def cost(self, a, b, t):                    # cheapest of the parallel roads a -> b
        return min(self.cost_at(a, b, k, t) for k in self.G[a][b])

    def costs(self, path, t0):                  # cost of each edge along path, entering at t0
        out, t = [], t0
        for a, b in zip(path, path[1:]):
            c = self.cost(a, b, t)
            out.append(c)
            t += c
        return out

    def arrived(self):
        return self.i >= len(self.path) - 1

    def left_on_edge(self):
        a, b = self.path[self.i], self.path[self.i + 1]
        return (1 - self.progress) * self.drive_cost(a, b)

    def drive_cost(self, a, b):                 # time to drive a -> b right now
        c = self.cost(a, b, 0)
        if math.isinf(c):                       # already on a closed road: squeeze past slowly
            d = min(self.G[a][b].values(), key=lambda d: d['length'])
            c = 3 * d['length'] / (d['speed_kph'] / 3.6)
        return c

    def advance(self, dt):                      # simulated driving
        while dt > 0 and not self.arrived():
            a, b = self.path[self.i], self.path[self.i + 1]
            c = self.drive_cost(a, b)
            need = (1 - self.progress) * c
            if need > dt:
                self.progress += dt / c
                dt = 0
            else:
                dt -= need
                self.i, self.progress = self.i + 1, 0.0

    def check(self):                            # the mid-transit recalibration loop
        if self.i + 2 >= len(self.path):
            return None                         # on the last road already
        off = self.left_on_edge()
        rest = self.path[self.i + 1:]           # from the next junction onwards
        now_costs = self.costs(rest, off)
        now, planned = sum(now_costs), sum(self.planned[self.i + 1:])
        delta = (now - planned) / planned if planned > 0 else 0.0
        if now <= planned * (1 + DELTA_C) and not math.isinf(now):
            return None
        try:
            new_path, new_cost = dynamic_astar(self.G, rest[0], self.dest, self.cost_at, off)
        except ValueError:                      # no way around right now: wait for the road to reopen
            self.planned[self.i + 1:] = now_costs
            return {'type': 'hold', 'name': 'Road ahead blocked and no way around it. Waiting for it to reopen.'}
        if new_path != rest and new_cost < now * 0.95:
            self.old_route, self.old_route_at = [self.path[self.i]] + rest, time.time()
            self.path = self.path[:self.i + 1] + new_path
            self.planned = self.planned[:self.i + 1] + self.costs(new_path, off)
            self.reroutes += 1
            return {'type': 'reroute', 'saved_s': round(now - new_cost) if not math.isinf(now) else None}
        self.planned[self.i + 1:] = now_costs   # keep the route, accept the new costs as the plan
        return {'type': 'hold', 'name': f'Route re-checked: {delta * 100:.0f}% slower ahead, but still the fastest. Keeping it.'}

    def advance_and_check(self, dt):
        self.advance(dt)
        self.since_check += dt
        if self.since_check >= CHECK_EVERY and not self.arrived():
            self.since_check = 0
            return self.check()
        return None

    def gps(self):                              # position + ~5 m of GPS noise
        a = self.G.nodes[self.path[self.i]]
        b = self.G.nodes[self.path[min(self.i + 1, len(self.path) - 1)]]
        lat = a['y'] + (b['y'] - a['y']) * self.progress + random.gauss(0, 5) / 111320
        lon = a['x'] + (b['x'] - a['x']) * self.progress + random.gauss(0, 5) / 105000
        return [lat, lon]

    def eta(self):
        if self.arrived():
            return 0
        off = self.left_on_edge()
        return off + sum(self.costs(self.path[self.i + 1:], off))

    def summary(self):
        eta = self.eta()
        return {'id': self.id, 'status': self.status, 'position': self.gps(),
                'route': [[self.G.nodes[n]['y'], self.G.nodes[n]['x']] for n in self.path[self.i:]],
                'eta_s': None if math.isinf(eta) else round(eta), 'reroutes': self.reroutes,
                'hospital': self.hospital and {'name': self.hospital['name']}}
