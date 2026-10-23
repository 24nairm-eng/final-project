import heapq, math
import networkx as nx
from .weights import edge_weight

V_MAX = 60 / 3.6   # fastest road speed in m/s across all loaded areas

def set_vmax(G):
    global V_MAX
    V_MAX = max(V_MAX, max(d['speed_kph'] for _, _, d in G.edges(data=True)) / 3.6)

def dist_m(G, a, b):
    y1, x1, y2, x2 = G.nodes[a]['y'], G.nodes[a]['x'], G.nodes[b]['y'], G.nodes[b]['x']
    p = math.pi / 180
    h = math.sin((y2 - y1) * p / 2) ** 2 + math.cos(y1 * p) * math.cos(y2 * p) * math.sin((x2 - x1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))

def dijkstra(G, s, t):
    return nx.dijkstra_path(G, s, t, weight='travel_time')

def static_astar(G, s, t, W):
    """W: {(u, v, k): seconds}, a snapshot taken at departure."""
    weight = lambda u, v, keys: min(W[(u, v, k)] for k in keys)
    return nx.astar_path(G, s, t, heuristic=lambda a, b: dist_m(G, a, b) / V_MAX, weight=weight)

def make_cost_at(G, live, predicted, impair):
    """Cost of edge (u, v, k) if the ambulance enters it arrive_s seconds from now."""
    def cost_at(u, v, k, arrive_s):
        e = (u, v, k)
        w = min(1.0, arrive_s / 600)                 # blend: now -> 10-minute forecast
        rho = (1 - w) * live[e] + w * predicted.get(e, live[e])
        return edge_weight(G.edges[e], rho, impair.get(e, 0.0))
    return cost_at

def dynamic_astar(G, s, t, cost_at, t0=0.0):
    """Time-dependent A*. Returns (path, seconds)."""
    h = lambda n: dist_m(G, n, t) / V_MAX
    g, prev, closed = {s: t0}, {}, set()
    heap = [(h(s), s)]
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
                prev[v] = u
                heapq.heappush(heap, (g[v] + h(v), v))
    if t not in g:
        raise ValueError('no route')
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return path[::-1], g[t] - t0
