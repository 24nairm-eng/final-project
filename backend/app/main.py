"""Emergency Route Optimizer: one server for the API, the live engine and the website."""
import asyncio, time, traceback
from contextlib import asynccontextmanager
from pathlib import Path

import osmnx as ox
from fastapi import Depends, FastAPI, HTTPException, Request as HttpRequest, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from . import trip as trip_module
from .areas import AreaManager
from .fleet import ACTIVE, Fleet, Request
from .geocode import router as geocode_router

SETTINGS = {
    'tick_s': 5,              # simulated seconds per real second (5 = trips play 5x faster)
    'demo_events': True,      # simulated accidents / jams ahead of ambulances
    'max_demo_events': 2,     # at most this many per emergency
    'demo_every_s': 240,      # simulated seconds between them (after the first one)
    'delta_c': 0.30,          # reroute when the remaining route is 30% slower than planned
    'demo_mode': True,        # one guaranteed accident (and reroute) on every trip, for demonstrations
    'demo_after_s': 10,       # simulated seconds after the ambulance sets off
}
MAX_ACTIVE_PER_DEVICE = 2     # stops one phone from flooding the system with requests
IMPAIR = {'pothole': .2, 'water': .5, 'construction': .6, 'accident': 1.0}
DIST = Path(__file__).resolve().parents[2] / 'dashboard' / 'dist'   # built website

areas = AreaManager()
fleet = Fleet(SETTINGS)
user_sockets, admin_sockets = {}, set()


async def ensure_area(lat, lon):
    area = await areas.get(lat, lon)
    fleet.add_area(area)
    return area


async def prepare_and_dispatch(req):
    try:
        req.area = await ensure_area(req.lat, req.lon)
    except Exception as e:
        req.status, req.error = 'failed', str(e) if isinstance(e, ValueError) else 'Could not load the map for this area.'
        req.log('error', req.error)
        traceback.print_exc()
        return
    if req.status == 'preparing':
        await fleet.dispatch(req)


@asynccontextmanager
async def lifespan(app):
    trip_module.DELTA_C = SETTINGS['delta_c']
    for entry in areas.index[:3]:               # warm up the most used areas in the background
        asyncio.ensure_future(ensure_area(entry['lat'], entry['lon']))
    task = asyncio.create_task(engine_loop())
    yield
    task.cancel()


app = FastAPI(title='Emergency Route Optimizer API', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)        # API docs are admin-only
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173'], allow_credentials=True,
                   allow_methods=['*'], allow_headers=['*'])
app.include_router(geocode_router)              # /api/geocode: typed address -> map location


# ---------------- public (no login) ----------------
class RequestIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    condition: str = 'General'
    phone: str | None = Field(default=None, max_length=20)          # number of the person booking
    patient_name: str | None = Field(default=None, max_length=60)
    landmark: str | None = Field(default=None, max_length=120)      # e.g. "Opposite Hanuman temple, gate 2"


@app.get('/api/health')
def health():
    return {'ok': True, 'areas_loaded': len(fleet.areas), 'ambulances': len(fleet.ambulances)}


@app.post('/api/requests')
async def create_request(body: RequestIn, http: HttpRequest):
    forwarded = http.headers.get('x-forwarded-for')        # set by the hosting proxy when deployed
    ip = forwarded.split(',')[0].strip() if forwarded else (http.client.host if http.client else '?')
    active = [r for r in fleet.requests.values() if r.ip == ip and r.status in ACTIVE]
    if len(active) >= MAX_ACTIVE_PER_DEVICE:
        raise HTTPException(429, 'You already have an active request. Please wait for it to finish.')
    condition = body.condition if body.condition in ('General', 'Cardiac', 'Trauma', 'Neuro', 'Burns') else 'General'
    clean = lambda v: (v or '').strip() or None
    req = Request(body.lat, body.lon, condition, clean(body.phone), ip, clean(body.patient_name), clean(body.landmark))
    fleet.requests[req.id] = req
    area = areas.area_at(req.lat, req.lon)
    if area:
        req.area = area
        req.status = 'preparing'
        await fleet.dispatch(req)
    else:
        req.log('info', 'First request from this area: loading its road map (about 1 minute).')
        asyncio.ensure_future(prepare_and_dispatch(req))
    return fleet.request_view(req)


def get_req(rid):
    req = fleet.requests.get(rid)
    if not req:
        raise HTTPException(404, 'Request not found (the server may have restarted).')
    return req


@app.get('/api/requests/{rid}')
def read_request(rid: str):
    return fleet.request_view(get_req(rid))


@app.post('/api/requests/{rid}/cancel')
def cancel_request(rid: str):
    req = get_req(rid)
    fleet.cancel(req)
    return fleet.request_view(req)


@app.websocket('/ws/requests/{rid}')
async def request_socket(ws: WebSocket, rid: str):
    await ws.accept()
    user_sockets.setdefault(rid, set()).add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        user_sockets.get(rid, set()).discard(ws)


# ---------------- admin (password) ----------------
class LoginIn(BaseModel):
    password: str


@app.post('/api/admin/login')
async def admin_login(body: LoginIn, http: HttpRequest, response: Response):
    token = auth.login(body.password)
    if not token:
        await asyncio.sleep(1)                  # slows down password guessing
        raise HTTPException(401, 'Wrong password')
    secure = http.headers.get('x-forwarded-proto', http.url.scheme) == 'https'
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite='strict', secure=secure,
                        max_age=auth.SESSION_HOURS * 3600)
    return {'token': token}


@app.post('/api/admin/logout')
def admin_logout(response: Response, token: str = Depends(auth.require_admin)):
    auth.logout(token)
    response.delete_cookie(auth.COOKIE)
    return {'ok': True}


@app.get('/api/admin/overview')
def admin_overview(_: str = Depends(auth.require_admin)):
    return fleet.overview()


class IncidentIn(BaseModel):
    lat: float
    lon: float
    type: str = 'accident'


@app.post('/api/admin/incidents')
def admin_incident(body: IncidentIn, _: str = Depends(auth.require_admin)):
    area = areas.area_at(body.lat, body.lon)
    if not area:
        raise HTTPException(400, 'No loaded map here.')
    G, value = area.G, IMPAIR.get(body.type, 1.0)
    u, v, k = ox.distance.nearest_edges(G, X=body.lon, Y=body.lat)
    edges = [(a, b, kk) for a, b in ((u, v), (v, u)) if G.has_edge(a, b) for kk in G[a][b]]
    for e in edges:
        area.sim.impair[e] = value
        area.sim.incidents.append({'edge': e, 'until': area.sim.t + 1800})   # clears after 30 sim minutes
    name = G.edges[u, v, k].get('name', 'unnamed road')
    name = name[0] if isinstance(name, list) else name
    fleet.add_mark(area, 'closure', [body.lat, body.lon], area.sim.t + 1800, f'Road closed by control room: {name}')
    return {'road': name, 'impairment': value}


@app.post('/api/admin/requests/{rid}/accident')
def admin_force_accident(rid: str, _: str = Depends(auth.require_admin)):
    """Demo button: an accident on this ambulance's route ahead, so it reroutes now."""
    req = get_req(rid)
    amb = req.ambulance
    if not amb or amb.status not in ('to_patient', 'to_hospital') or not amb.trip:
        raise HTTPException(400, 'This ambulance is not driving right now.')
    if not fleet.force_accident(amb, req):
        raise HTTPException(400, 'No road ahead with a way around it. Try again in a few seconds.')
    return fleet.request_view(req, admin=True)


class SettingsIn(BaseModel):
    demo_events: bool | None = None
    max_demo_events: int | None = Field(default=None, ge=0, le=10)
    demo_every_s: int | None = Field(default=None, ge=30, le=3600)
    delta_c: float | None = Field(default=None, ge=0.05, le=2.0)
    demo_mode: bool | None = None
    demo_after_s: int | None = Field(default=None, ge=5, le=600)
    tick_s: int | None = Field(default=None, ge=1, le=20)


@app.put('/api/admin/settings')
def admin_settings(body: SettingsIn, _: str = Depends(auth.require_admin)):
    for k, v in body.model_dump(exclude_none=True).items():
        SETTINGS[k] = v
    trip_module.DELTA_C = SETTINGS['delta_c']
    return SETTINGS


class AreaIn(BaseModel):
    lat: float
    lon: float


@app.post('/api/admin/areas')
async def admin_prepare_area(body: AreaIn, _: str = Depends(auth.require_admin)):
    """Load a city in advance so its first emergency is instant."""
    try:
        area = await ensure_area(body.lat, body.lon)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return area.info()


@app.get('/api/admin/openapi.json', include_in_schema=False)
def admin_openapi(_: str = Depends(auth.require_admin)):
    return app.openapi()


@app.get('/admin/api-docs', include_in_schema=False)
def admin_docs(http: HttpRequest):
    if not auth.valid(auth.token_from(http)):
        return RedirectResponse('/#/admin')
    return get_swagger_ui_html(openapi_url='/api/admin/openapi.json', title='Emergency Route Optimizer API (admin)')


@app.websocket('/ws/admin')
async def admin_socket(ws: WebSocket):
    token = ws.cookies.get(auth.COOKIE) or ws.query_params.get('token')
    if not auth.valid(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    admin_sockets.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        admin_sockets.discard(ws)


# ---------------- the engine ----------------
async def engine_loop():
    while True:
        started = time.time()
        try:
            fleet.tick(SETTINGS['tick_s'])
        except Exception:
            traceback.print_exc()
        for rid, socks in list(user_sockets.items()):
            req = fleet.requests.get(rid)
            if not req or not socks:
                continue
            msg = fleet.request_view(req)
            for ws in list(socks):
                try:
                    await ws.send_json(msg)
                except Exception:
                    socks.discard(ws)
        if admin_sockets:
            msg = fleet.overview()
            for ws in list(admin_sockets):
                try:
                    await ws.send_json(msg)
                except Exception:
                    admin_sockets.discard(ws)
        await asyncio.sleep(max(0.1, 1 - (time.time() - started)))


# ---------------- the website (built React app) ----------------
if DIST.exists():
    app.mount('/', StaticFiles(directory=DIST, html=True), name='site')
else:
    @app.get('/', include_in_schema=False)
    def no_site():
        return {'message': 'Website not built yet. Run "npm run build" in the dashboard folder, '
                           'or use "npm run dev" and open http://localhost:5173'}
