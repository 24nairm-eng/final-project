import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, Marker, Tooltip } from 'react-leaflet'
import { api, fmtEta, INDIA, liveSocket } from './api.js'
import { ambulanceIcon, EventMarks, FitTo, hospitalIcon, MapClick, patientIcon, RouteLines, STATUS_COLORS, Tiles } from './mapParts.jsx'

const TOKEN_KEY = 'era_admin_token'
const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) } catch { return null } }
const setToken = t => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ } }

const STATUS_LABEL = {
  available: 'Available', to_patient: 'To patient', at_scene: 'With patient',
  to_hospital: 'To hospital', at_hospital: 'At hospital', returning: 'Returning',
}

export default function AdminApp() {
  const [token, setTok] = useState(getToken())
  const onLogout = useCallback(() => { setToken(null); setTok(null) }, [])
  if (!token) return <Login onLogin={t => { setToken(t); setTok(t) }} />
  return <Dashboard token={token} onLogout={onLogout} />
}

function Login({ onLogin }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const r = await api('/api/admin/login', { method: 'POST', body: { password } })
      onLogin(r.token)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="brand"><span className="brand-mark">+</span> Admin control room</div>
        <label className="field">
          <span>Admin password</span>
          <input id="admin-password" type="password" autoComplete="current-password" value={password}
                 onChange={e => setPassword(e.target.value)} autoFocus />
        </label>
        {error && <p className="note error">{error}</p>}
        <button className="sos" disabled={!password || busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <a className="linkish" href="#/">Back to the public page</a>
      </form>
    </div>
  )
}

function Dashboard({ token, onLogout }) {
  const [ov, setOv] = useState(null)
  const [tab, setTab] = useState('requests')
  const [mode, setMode] = useState('none')       // none | block | area
  const [focus, setFocus] = useState(null)       // { points, key }
  const [msg, setMsg] = useState('')
  const [showIdle, setShowIdle] = useState(true)

  useEffect(() => {
    api('/api/admin/overview', { token }).then(setOv).catch(e => { if (e.status === 401) onLogout() })
    return liveSocket(`/ws/admin?token=${encodeURIComponent(token)}`, setOv, e => {
      if (e.code === 4401) { onLogout(); return false }
    })
  }, [token, onLogout])

  const firstArea = ov?.areas?.[0]
  const start = useMemo(() => firstArea ? { points: [firstArea.center], key: 'area-' + firstArea.key } : null, [firstArea])
  const view = focus || start

  async function onMapClick(ll) {
    if (mode === 'block') {
      try {
        const r = await api('/api/admin/incidents', { method: 'POST', token, body: { lat: ll.lat, lon: ll.lng, type: 'accident' } })
        setMsg(`Closed ${r.road} (both directions). Ambulances heading there will reroute.`)
      } catch (e) { setMsg(e.message) }
      setMode('none')
    } else if (mode === 'area') {
      setMsg('Loading this area… this can take about a minute the first time.')
      setMode('none')
      try {
        const r = await api('/api/admin/areas', { method: 'POST', token, body: { lat: ll.lat, lon: ll.lng } })
        setMsg(`Area ready: ${r.hospitals} hospitals and ${r.police} police stations, each with an ambulance.`)
      } catch (e) { setMsg(e.message) }
    }
  }

  async function forceAccident(r) {
    try {
      await api(`/api/admin/requests/${r.id}/accident`, { method: 'POST', token })
      setMsg(`Accident simulated ahead of ${r.ambulance.id}. It is rerouting now (old route in red).`)
      setFocus({ points: [r.patient, r.ambulance?.position], key: r.id + Date.now() })
    } catch (e) { setMsg(e.message) }
  }

  async function saveSettings(patch) {
    try {
      const s = await api('/api/admin/settings', { method: 'PUT', token, body: patch })
      setOv(o => ({ ...o, settings: s }))
    } catch (e) { setMsg(e.message) }
  }

  async function logout() {
    try { await api('/api/admin/logout', { method: 'POST', token }) } catch { /* already expired */ }
    onLogout()
  }

  const counts = ov?.counts || {}
  const busyCount = (ov?.total || 0) - (counts.available || 0)

  return (
    <div className="admin-layout">
      <header className="topbar admin">
        <div className="brand"><span className="brand-mark">+</span> Control room</div>
        <div className="top-actions">
          <a href="/admin/api-docs" target="_blank" rel="noreferrer">API docs</a>
          <a href="#/">Public page</a>
          <button className="ghost" onClick={logout}>Log out</button>
        </div>
      </header>

      <div className="stats">
        <Stat label="Ambulances" value={ov?.total ?? '–'} />
        <Stat label="Available" value={counts.available ?? 0} color={STATUS_COLORS.available} />
        <Stat label="On duty" value={busyCount} color={STATUS_COLORS.to_patient} />
        <Stat label="Active emergencies" value={ov?.requests?.length ?? 0} />
        <Stat label="Areas loaded" value={ov?.areas?.length ?? 0} />
      </div>

      <div className="admin-main">
        <MapContainer center={INDIA} zoom={5} className={`map ${mode !== 'none' ? 'crosshair' : ''}`} preferCanvas>
          <Tiles />
          <MapClick onClick={onMapClick} />
          {view && <FitTo points={view.points} fitKey={view.key} maxZoom={14} />}
          {ov?.ambulances?.filter(a => showIdle || a.status !== 'available').map(a => (
            <CircleMarker key={a.id} center={a.position} radius={a.status === 'available' ? 4 : 7}
                          pathOptions={{ color: '#fff', weight: 1.5, fillColor: STATUS_COLORS[a.status], fillOpacity: 1 }}>
              <Tooltip>{a.id} · {a.vehicle_no} · {STATUS_LABEL[a.status]}<br />Driver: {a.driver}<br />Base: {a.base} ({a.base_kind})</Tooltip>
            </CircleMarker>
          ))}
          {ov?.requests?.map(r => (
            <Fragment key={r.id}>
              <RouteLines route={r.route} oldRoute={r.old_route} weight={5}
                          color={r.status === 'to_hospital' ? STATUS_COLORS.to_hospital : '#1d5fd0'} />
              <Marker position={r.patient} icon={patientIcon}><Tooltip>Emergency {r.id} · {r.condition}{r.patient_name ? ` · ${r.patient_name}` : ''}</Tooltip></Marker>
              {r.hospital && <Marker position={[r.hospital.lat, r.hospital.lon]} icon={hospitalIcon}><Tooltip>{r.hospital.name}</Tooltip></Marker>}
              {r.ambulance && r.route?.length > 1 && <Marker position={r.ambulance.position} icon={ambulanceIcon}><Tooltip>{r.ambulance.id} · {r.ambulance.vehicle_no}</Tooltip></Marker>}
            </Fragment>
          ))}
          <EventMarks marks={ov?.marks} />
        </MapContainer>

        <aside className="admin-panel">
          <div className="tabs" role="tablist">
            {['requests', 'fleet', 'settings'].map(t => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
                {t === 'requests' ? 'Emergencies' : t === 'fleet' ? 'Fleet' : 'Settings'}
              </button>
            ))}
          </div>

          <div className="tools">
            <button className={mode === 'block' ? 'on' : ''} onClick={() => setMode(mode === 'block' ? 'none' : 'block')}>
              {mode === 'block' ? 'Click a road to close it…' : 'Close a road'}
            </button>
            <button className={mode === 'area' ? 'on' : ''} onClick={() => setMode(mode === 'area' ? 'none' : 'area')}>
              {mode === 'area' ? 'Click a city to load it…' : 'Load a new area'}
            </button>
          </div>
          {msg && <p className="note">{msg}</p>}

          {tab === 'requests' && <Requests ov={ov} onFocus={setFocus} onAccident={forceAccident} />}
          {tab === 'fleet' && <Fleet ov={ov} showIdle={showIdle} setShowIdle={setShowIdle} onFocus={setFocus} />}
          {tab === 'settings' && ov && <Settings s={ov.settings} onSave={saveSettings} />}
        </aside>
      </div>
    </div>
  )
}

function Stat({ label, value, color }) {
  return (
    <div className="stat">
      <span className="stat-value" style={color ? { color } : undefined}>{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  )
}

function Requests({ ov, onFocus, onAccident }) {
  if (!ov) return <p className="muted">Loading…</p>
  return (
    <>
      <h3 className="panel-h">Active emergencies</h3>
      {!ov.requests.length && <p className="muted">None right now. New requests from the public page appear here live.</p>}
      <ul className="req-list">
        {ov.requests.map(r => (
          <li key={r.id} onClick={() => onFocus({ points: [r.patient, r.ambulance?.position], key: r.id + Date.now() })}>
            <div className="req-top">
              <b>{r.condition}{r.patient_name ? ` · ${r.patient_name}` : ''}</b>
              <span className={`status-pill small s-${r.status}`}>{r.status.replace('_', ' ')}</span>
            </div>
            <div className="muted small">
              #{r.id}{r.ambulance ? ` · ${r.ambulance.id}` : ''}{r.eta_s != null ? ` · ETA ${fmtEta(r.eta_s)}` : ''}
              {r.reroutes ? ` · ${r.reroutes} reroute${r.reroutes > 1 ? 's' : ''}` : ''}
            </div>
            {r.hospital && <div className="small">🏥 {r.hospital.name}</div>}
            {r.old_route?.length > 1 && <div className="small reroute-tag">↻ {r.rerouting ? 'Rerouting now' : 'Rerouted'}: old route in red on the map</div>}
            {r.landmark && <div className="small">📌 {r.landmark}</div>}
            {r.phone && <div className="small">📞 Caller: <a href={`tel:${r.phone}`} onClick={e => e.stopPropagation()}>{r.phone}</a></div>}
            {r.ambulance && <div className="small">🚑 {r.ambulance.vehicle_no} · {r.ambulance.driver} ({r.ambulance.driver_phone})</div>}
            {r.events?.length > 0 && <div className="small last-ev">{r.events[r.events.length - 1].text}</div>}
            {['to_patient', 'to_hospital'].includes(r.status) && (
              <button className="demo-btn" onClick={e => { e.stopPropagation(); onAccident(r) }}>
                Simulate accident ahead (demo)
              </button>
            )}
          </li>
        ))}
      </ul>
      {ov.recent?.length > 0 && (
        <>
          <h3 className="panel-h">Recently finished</h3>
          <ul className="recent">
            {ov.recent.map(r => <li key={r.id}>#{r.id} · {r.condition} · {r.status}{r.hospital ? ` · ${r.hospital}` : ''}</li>)}
          </ul>
        </>
      )}
    </>
  )
}

function Fleet({ ov, showIdle, setShowIdle, onFocus }) {
  if (!ov) return <p className="muted">Loading…</p>
  const busy = ov.ambulances.filter(a => a.status !== 'available')
  return (
    <>
      <div className="legend">
        {Object.entries(STATUS_LABEL).map(([k, l]) => (
          <span key={k}><i style={{ background: STATUS_COLORS[k] }} />{l} ({ov.counts[k] || 0})</span>
        ))}
      </div>
      <label className="check"><input id="show-idle" type="checkbox" checked={showIdle} onChange={e => setShowIdle(e.target.checked)} /> Show available ambulances on the map</label>
      <h3 className="panel-h">On duty ({busy.length})</h3>
      <ul className="recent">
        {busy.map(a => (
          <li key={a.id} onClick={() => onFocus({ points: [a.position], key: a.id + Date.now() })} className="clickable">
            <i className="dot" style={{ background: STATUS_COLORS[a.status] }} /> {a.id} · {a.vehicle_no} · {STATUS_LABEL[a.status]}
            <div className="muted small">{a.type} · driver {a.driver} ({a.driver_phone}){a.paramedic ? ` · paramedic ${a.paramedic}` : ''} · base {a.base}</div>
          </li>
        ))}
      </ul>
      <h3 className="panel-h">Areas</h3>
      <ul className="recent">
        {ov.areas.map(a => (
          <li key={a.key} className="clickable" onClick={() => onFocus({ points: [a.center], key: a.key + Date.now() })}>
            {a.key} · {a.hospitals} hospitals · {a.police} police stations
          </li>
        ))}
      </ul>
    </>
  )
}

function Settings({ s, onSave }) {
  const [form, setForm] = useState(s)
  useEffect(() => setForm(s), [s])
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))
  return (
    <form className="settings" onSubmit={e => { e.preventDefault(); onSave(form) }}>
      <label className="check"><input id="demo-mode" type="checkbox" checked={!!form.demo_mode} onChange={e => set('demo_mode', e.target.checked)} /> <b>Demo mode:</b> one accident and reroute on every trip</label>
      <label className="field"><span>Demo accident after (simulated seconds)</span>
        <input id="demo-after" type="number" min="5" max="600" value={form.demo_after_s ?? 10} onChange={e => set('demo_after_s', +e.target.value)} /></label>
      <label className="check"><input id="demo-events" type="checkbox" checked={form.demo_events} onChange={e => set('demo_events', e.target.checked)} /> Simulated accidents and jams ahead of ambulances</label>
      <label className="field"><span>Max simulated events per emergency</span>
        <input id="max-demo" type="number" min="0" max="10" value={form.max_demo_events} onChange={e => set('max_demo_events', +e.target.value)} /></label>
      <label className="field"><span>Gap between simulated events (simulated seconds)</span>
        <input id="demo-every" type="number" min="30" max="3600" value={form.demo_every_s} onChange={e => set('demo_every_s', +e.target.value)} /></label>
      <label className="field"><span>Reroute threshold ΔC: {Math.round(form.delta_c * 100)}%</span>
        <input id="delta-c" type="range" min="0.1" max="1" step="0.05" value={form.delta_c} onChange={e => set('delta_c', +e.target.value)} /></label>
      <label className="field"><span>Simulation speed: {form.tick_s}× real time</span>
        <input id="tick" type="range" min="1" max="20" step="1" value={form.tick_s} onChange={e => set('tick_s', +e.target.value)} /></label>
      <button className="sos">Save settings</button>
      <p className="muted small">Settings reset to their defaults when the server restarts.</p>
    </form>
  )
}
