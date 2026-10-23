"""Road graphs from OpenStreetMap, one area at a time (any city in India)."""
import osmnx as ox

# Realistic Indian city speeds (km/h) for roads that have no speed limit in OSM
HWY_SPEEDS = {'motorway': 80, 'trunk': 60, 'primary': 50, 'secondary': 40, 'tertiary': 35,
              'residential': 25, 'unclassified': 25, 'living_street': 15}


def clean_lanes(d):
    lanes = d.get('lanes', 1)
    if isinstance(lanes, list):
        lanes = lanes[0]
    try:
        return max(1, int(float(lanes)))
    except (TypeError, ValueError):
        return 1


def prepare(G):
    """Make every edge carry clean numeric fields the router needs."""
    for u, v, k, d in G.edges(keys=True, data=True):
        d['lanes'] = clean_lanes(d)
        d['length'] = float(d.get('length', 1.0)) or 1.0
        d['speed_kph'] = float(d.get('speed_kph', 30.0)) or 30.0
    return G


def download_graph(center, radius_m):
    """Download drivable roads within radius_m of center (lat, lon). Takes 20-90 s."""
    G = ox.graph_from_point(center, dist=radius_m, network_type='drive')
    G = ox.routing.add_edge_speeds(G, hwy_speeds=HWY_SPEEDS)
    G = ox.routing.add_edge_travel_times(G)
    return prepare(G)


def save_graph(G, path):
    ox.save_graphml(G, path)


def load_graph(path):
    return prepare(ox.load_graphml(path))
