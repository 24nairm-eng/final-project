# Version 2: what changed and where

## How to install this version

1. Stop both servers (Ctrl+C in the uvicorn and npm terminals).
2. Save the current version: in `emergency-router`, run `git add .` then `git commit -m "v1 before upgrade"`.
3. Unzip this file **into** your `emergency-router` folder and choose **Replace** for every file.
   Keep your `.venv`, `backend\data` and `dashboard\node_modules` folders; they are not in the zip.
4. Delete these old files, which version 2 no longer uses:
   `backend\app\hospitals.py` (replaced by `places.py`) and
   `backend\test_step3.py`, `test_step5.py`, `test_step6.py`, `test_step7.py` (written for version 1).
5. Install packages (nothing new, this just makes sure):
   - `cd backend` then `..\.venv\Scripts\activate` then `pip install -r requirements.txt`
   - `cd ..\dashboard` then `npm install`

## How to run it

**Option A, one server (like the real deployment):**
```
cd dashboard
npm run build
cd ..\backend
uvicorn app.main:app --reload
```
Open **http://localhost:8000** for the public page and **http://localhost:8000/#/admin** for the admin page.
Run `npm run build` again whenever you change the website code.

**Option B, while editing the website:** keep two terminals as before
(`uvicorn app.main:app --reload` in `backend`, `npm run dev` in `dashboard`) and open **http://localhost:5173**.
The website forwards API calls to the backend by itself.

**Admin password:** `admin123` by default. Change it before deploying, in PowerShell before starting uvicorn:
`$env:ADMIN_PASSWORD = "your-strong-password"`

## What changed

| Your request | What was done | Where |
| --- | --- | --- |
| Less frequent rerouting | ΔC raised from 20% to 30%; a new route must save at least 5%; at most 2 simulated events per emergency, 4 simulated minutes apart; random city accidents 5x rarer, random jams 3x rarer. All adjustable live in the admin page | `backend/app/trip.py`, `traffic.py`, `main.py` (`SETTINGS`) |
| Ambulances at every hospital and police station | Every hospital and police station from OpenStreetMap gets an ambulance. Each emergency compares the 5 nearest free ambulances by real driving time and sends the fastest | `backend/app/fleet.py` (`add_area`, `dispatch`), `places.py` |
| Works anywhere in India | The first request in a new place downloads that area's roads, hospitals and police stations (about 1 minute), saves them in `backend/data/areas/`, and is instant after that. Your Nagpur map is reused automatically | `backend/app/areas.py`, `graph.py` |
| User page without login | Public page at `/`: choose what happened, optional phone number, one button "Send ambulance to my location". Live map, ETA, progress steps, updates, cancel. Survives a page refresh | `dashboard/src/UserApp.jsx` |
| Admin page with password | `/#/admin`: password login, live map of every ambulance coloured by status, all active emergencies, fleet list, settings, close a road, load a new city in advance, API docs | `dashboard/src/AdminApp.jsx`, `backend/app/auth.py`, `main.py` (`/api/admin/...`) |
| Many ambulances moving at once | Each ambulance goes available → to patient → with patient → to hospital → at hospital → returning → available, independently. Busy ambulances are skipped; if all are busy the request waits in a queue | `backend/app/fleet.py` |
| Current location instead of tapping the map | The main button uses the phone's GPS. Tapping the map is kept only as a fallback if location is denied | `dashboard/src/UserApp.jsx` (`locateAndSend`) |
| Backend and dashboard as one site, docs for admin only | The Python server now serves the website too (one address). The public `/docs` page is switched off; the API docs are at `/admin/api-docs` and need the admin login | `backend/app/main.py` (bottom: `StaticFiles`; `admin_docs`) |
| Works on mobile | Mobile-first layout: big buttons, bottom sheet over the map, "Call 108" button, safe for phone notches. Admin page also stacks on a phone | `dashboard/src/index.css`, `index.html` |
| Deployment | `Dockerfile` builds the website and runs the server in one container, ready for Render, Railway or any VPS. Deployment steps are a separate guide | `Dockerfile`, `.dockerignore` |
| Safety | Yellow banner on the public page: student prototype, ambulances are simulated, call 108 / 112. One phone can have at most 2 active requests | `UserApp.jsx`, `main.py` (`MAX_ACTIVE_PER_DEVICE`) |

## New and changed files

```
backend/app/
  areas.py     NEW   loads any area of India on first use, caches it
  fleet.py     NEW   ambulances, emergencies, dispatch, the life cycle, simulated road events
  auth.py      NEW   admin password login (sessions, cookie or Bearer token)
  places.py    NEW   hospitals + police stations from OSM, hospital selection (replaces hospitals.py)
  graph.py     CHANGED  downloads/loads a graph for any area, not just Nagpur
  main.py      REWRITTEN  public API, admin API, live updates, serves the website
  trip.py      CHANGED  ΔC 30%, reroute only if 5% faster
  traffic.py   CHANGED  fewer random accidents and jams
  routing.py   CHANGED  heuristic speed covers all loaded areas
  weights.py   unchanged
backend/requirements.txt   UPDATED
dashboard/
  index.html, vite.config.js            CHANGED (title, phone settings, API forwarding in dev)
  src/App.jsx                           REWRITTEN (switches between public and admin page)
  src/UserApp.jsx, AdminApp.jsx         NEW
  src/api.js, mapParts.jsx              NEW (shared helpers, map markers)
  src/main.jsx, index.css               CHANGED
Dockerfile, .dockerignore, .gitignore   NEW / UPDATED
```

## API (for the report)

| Who | Method | Path | Purpose |
| --- | --- | --- | --- |
| Public | POST | `/api/requests` | New emergency: lat, lon, condition, optional phone |
| Public | GET | `/api/requests/{id}` | Status, ambulance, route, ETA, hospital, updates |
| Public | POST | `/api/requests/{id}/cancel` | Cancel |
| Public | WebSocket | `/ws/requests/{id}` | Live updates every second |
| Admin | POST | `/api/admin/login`, `/api/admin/logout` | Password login |
| Admin | GET | `/api/admin/overview` | Every ambulance, emergency, area, settings |
| Admin | WebSocket | `/ws/admin` | Live overview every second |
| Admin | POST | `/api/admin/incidents` | Close a road |
| Admin | POST | `/api/admin/areas` | Load a city in advance |
| Admin | PUT | `/api/admin/settings` | Rerouting threshold, simulated events, simulation speed |
| Admin | GET | `/admin/api-docs` | Interactive API docs |

## Known limits (say these honestly in the viva)

- Traffic, accidents, hospital beds and ambulances are **simulated**; roads, hospitals and police stations are **real OpenStreetMap data**.
- The first emergency in a new area waits about a minute while its map downloads.
- Emergencies and settings are kept in memory, so they reset when the server restarts (PostgreSQL storage is the next step).
- Phones only share GPS with **https** sites, so phone location works after deployment; on your laptop it uses Wi-Fi location.

## Update: type an address (version 2.1)

| What | Where |
| --- | --- |
| "Type an address or choose on the map instead": search box for an address, landmark or area (India only); pick a result and send the ambulance there. Tapping the map still works | `dashboard/src/UserApp.jsx`, `index.css` |
| Address lookup through OpenStreetMap Nominatim, at most 1 request per second, repeated searches cached | `backend/app/geocode.py` (new), `backend/app/main.py` (2 lines: import + `include_router`) |

## Update: rerouting shown in red, patient details, crew details (version 2.2)

| What | Where |
| --- | --- |
| **Rerouting on the map**: when an ambulance changes route, the route it just left stays on the map as a **red dashed line** under the new **blue** route until the ambulance reaches the patient or hospital, with a "Rerouting the ambulance" banner for the first minute. Accidents (💥), jams (🚦) and closed roads (⛔) are marked on the map. Works on the public and admin pages | `backend/app/trip.py` (`old_route`), `fleet.py` (`Request.public`, `add_mark`, `active_marks`), `main.py`; `dashboard/src/mapParts.jsx` (`RouteLines`, `EventMarks`), `UserApp.jsx`, `AdminApp.jsx` |
| "Type an address or choose on the map" is now a highlighted red-outlined button with 📍 | `UserApp.jsx`, `index.css` (`.pick-btn`) |
| Patient name, phone number of the person booking, and landmark (all optional). Full form on the choose-on-map screen; folded under the buttons on the main screen. Shown to the control room with a tap-to-call number | `UserApp.jsx` (`Details`), `main.py` (`RequestIn`), `fleet.py` (`Request`), `AdminApp.jsx` |
| Ambulance and crew details: vehicle number (MH-31-…), ALS (hospital ambulances, with paramedic) or BLS (police-station ambulances), driver name and phone. **Generated demo data**; phone numbers are partly hidden so no real person's number appears | `fleet.py` (`make_crew`), `UserApp.jsx` (`.crew-card`), `AdminApp.jsx` (Emergencies and Fleet tabs) |

## Update: guaranteed reroute for demonstrations (version 2.3)

| What | Where |
| --- | --- |
| **Demo mode** (on by default): every trip, to the patient and to the hospital, gets one simulated accident about 10 simulated seconds after the ambulance sets off. The road chosen is ahead on its route and always has a way around, and the ambulance reroutes at once, so the red old route and blue new route always appear. Turn it off or change the timing in the control room's Settings tab | `backend/app/fleet.py` (`force_accident`, `_maybe_demo_event`), `main.py` (`SETTINGS`) |
| **"Simulate accident ahead (demo)" button** on every moving emergency in the control room: makes an accident and reroute happen whenever you click it | `backend/app/main.py` (`POST /api/admin/requests/{id}/accident`), `dashboard/src/AdminApp.jsx`, `index.css` |

## Fix: ambulance stuck "with patient" (version 2.3.1)

| Problem | Fix | Where |
| --- | --- | --- |
| On real OpenStreetMap maps some junctions sit on one-way dead ends: an ambulance can drive in but there is no way out, so no hospital could be reached and the trip stopped at "With patient" | Patients, hospitals and ambulance bases are now placed only on junctions in the main connected road network, which can always reach each other | `backend/app/areas.py` (`Area.snap`), `fleet.py` (`dispatch`) |
| If the 5 nearest hospitals were unreachable, or an error happened, the ambulance waited silently | It now tries every hospital in the area, and any problem is shown in the updates list and retried after 30 simulated seconds | `backend/app/fleet.py` (`_to_hospital`), `places.py` (`rank_hospitals`) |

## Fix: "All nearby ambulances are busy" with no ambulances (version 2.3.2)

| Problem | Fix | Where |
| --- | --- | --- |
| When the free map server timed out while downloading hospitals and police stations, the area got **no ambulances at all**, the empty list was saved, and every request waited forever | The download is retried, an empty result is not saved (so it is tried again next time), and every area always gets at least 8 ambulances: missing ones are added as "Standby point" ambulances spread around the area | `backend/app/areas.py` (`MIN_AMBULANCES`, standby points, `_load_or_download`), `fleet.py` (`add_area`) |
| The waiting message did not say why | It now says whether ambulances are busy, the area has none yet, or none can reach the spot by road | `backend/app/fleet.py` (`dispatch`) |
