"""Address search: turns typed text like "Sitabuldi, Nagpur" into map coordinates.

Uses OpenStreetMap's free Nominatim service. Its rules allow at most one request per
second and ask for a clear app name, so requests go through here (not straight from
the browser), are spaced out, and repeated searches are answered from a cache.
"""
import threading, time

import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
NOMINATIM = 'https://nominatim.openstreetmap.org/search'
HEADERS = {'User-Agent': 'EmergencyRouteOptimizer/2.0 (student project, Ramdeobaba University, Nagpur)'}
_cache, _lock, _last = {}, threading.Lock(), [0.0]


@router.get('/api/geocode')
def geocode(q: str = Query(min_length=3, max_length=200),
            lat: float | None = None, lon: float | None = None):
    """Up to 6 matching places in India. lat/lon (optional) make nearby places rank first."""
    near = f'{lat:.1f},{lon:.1f}' if lat is not None and lon is not None else ''
    key = (q.strip().lower(), near)
    if key in _cache:
        return _cache[key]
    params = {'q': q, 'format': 'jsonv2', 'countrycodes': 'in', 'limit': 6}
    if near:
        params['viewbox'] = f'{lon - 0.3},{lat + 0.3},{lon + 0.3},{lat - 0.3}'   # prefer, not limit to, this box
    with _lock:
        wait = 1.0 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)                     # at most one request per second
        _last[0] = time.time()
        try:
            r = requests.get(NOMINATIM, params=params, headers=HEADERS, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception:
            raise HTTPException(502, 'Address search is not available right now. Please tap the location on the map.')
    results = [{'name': d.get('display_name', ''), 'lat': float(d['lat']), 'lon': float(d['lon'])} for d in data]
    _cache[key] = results
    return results
