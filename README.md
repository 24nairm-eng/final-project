# Emergency Route Optimizer (version 2.3.2, complete project)

AI-Powered Dynamic Route Optimization System for Emergency Vehicles (Idea Lab project).
All updates up to version 2.3.2 are already included. See `CHANGES.md` for what changed and where.

## First-time setup (once)

Open a terminal in this folder (`emergency-router`) and run:

```
python -m venv .venv
.\.venv\Scripts\activate
pip install -r backend\requirements.txt
```

The website is already built (`dashboard\dist`), so `npm` is only needed if you change website code
(`cd dashboard`, `npm install`, `npm run build`).

## Run it (every time)

```
.\.venv\Scripts\activate
cd backend
uvicorn app.main:app --reload
```

- Public page: http://localhost:8000
- Control room (admin): http://localhost:8000/#/admin (password `admin123`)
- On a phone: in a second terminal, `.\cloudflared.exe tunnel --url http://localhost:8000` and open the https link it prints.

## Demo mode (on by default)

Every trip gets one simulated accident ahead of the ambulance a couple of seconds after it sets off, and the
ambulance reroutes (old route in red, new route in blue). In the control room each moving emergency also has a
**Simulate accident ahead (demo)** button. Turn demo mode off in the control room's Settings tab.

## Reproduce the paper's results

```
cd backend
python benchmark.py
```
Results are written to `backend\results\results.json`.

The first emergency in an area downloads that area's roads, hospitals and police stations (about 1 minute);
after that it is saved in `backend\data` and is instant.
