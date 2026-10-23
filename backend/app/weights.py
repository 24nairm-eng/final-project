ALPHA, BETA = 2.0, 1.5

def edge_weight(d, density, impairment):
    if impairment >= 1.0:
        return float('inf')
    base = d['length'] / (d['speed_kph'] / 3.6)          # free-flow seconds
    lane_factor = 1.3 if d['lanes'] == 1 else 1.0         # narrow roads congest faster
    return base * (1 + ALPHA * density * lane_factor) * (1 + BETA * impairment)
