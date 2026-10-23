import { useEffect, useState } from 'react'
import { CircleMarker, MapContainer, Marker, Tooltip } from 'react-leaflet'
import { api, fmtAgo, fmtEta, INDIA, liveSocket } from './api.js'
import { ambulanceIcon, EventMarks, FitTo, hospitalIcon, MapClick, patientIcon, pinIcon, RouteLines, Tiles } from './mapParts.jsx'

const CONDITIONS = [
  { value: 'Cardiac', label: 'Heart / chest pain' },
  { value: 'Neuro', label: 'Stroke / unconscious' },
  { value: 'Trauma', label: 'Accident / injury' },
  { value: 'Burns', label: 'Burns' },
  { value: 'General', label: 'Other' },
]
const ACTIVE = ['preparing', 'waiting', 'to_patient', 'at_scene', 'to_hospital']
const STEPS = [
  { key: 'to_patient', label: 'On the way' },
  { key: 'at_scene', label: 'With patient' },
  { key: 'to_hospital', label: 'To hospital' },
  { key: 'arrived', label: 'At hospital' },
]
const KEY = 'era_active_request'
const remember = id => { try { id ? localStorage.setItem(KEY, id) : localStorage.removeItem(KEY) } catch { /* private mode */ } }
const recall = () => { try { return localStorage.getItem(KEY) } catch { return null } }

function Details({ phone, setPhone, patientName, setPatientName, landmark, setLandmark, landmarkHint, collapsed }) {
  const fields = (
    <>
      <label className="field">
        <span>Patient name</span>
        <input id="patient-name" type="text" maxLength={60} value={patientName} autoComplete="name"
               onChange={e => setPatientName(e.target.value)} placeholder="e.g. Sunita Sharma" />
      </label>
      <label className="field">
        <span>Your phone number (person booking)</span>
        <input id="phone" type="tel" inputMode="tel" maxLength={15} value={phone} autoComplete="tel"
               onChange={e => setPhone(e.target.value)} placeholder="e.g. 98XXXXXXXX" />
      </label>
      <label className="field">
        <span>Landmark</span>
        <input id="landmark" type="text" maxLength={120} value={landmark}
               onChange={e => setLandmark(e.target.value)} placeholder={landmarkHint} />
      </label>
    </>
  )
  const filled = [patientName, phone, landmark].filter(Boolean).length
  if (collapsed) return (
    <details className="details">
      <summary>Add patient name, your phone, landmark <span className="muted">(optional{filled ? `, ${filled} filled` : ''})</span></summary>
      {fields}
    </details>
  )
  return (
    <fieldset className="details">
      <legend>Patient details <span className="muted">(optional, helps the crew)</span></legend>
      {fields}
    </fieldset>
  )
}

export default function UserApp() {
  const [condition, setCondition] = useState('Cardiac')
  const [phone, setPhone] = useState('')
  const [patientName, setPatientName] = useState('')
  const [landmark, setLandmark] = useState('')
  const [req, setReq] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [pickMode, setPickMode] = useState(false)
  const [picked, setPicked] = useState(null)
  const [me, setMe] = useState(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)       // null = not searched yet
  const [searching, setSearching] = useState(false)
  const [pickedName, setPickedName] = useState('')

  // Resume a request after a page refresh.
  useEffect(() => {
    const id = recall()
    if (id) api(`/api/requests/${id}`).then(setReq).catch(() => remember(null))
  }, [])

  // Live updates for the current request.
  useEffect(() => {
    if (!req?.id) return
    return liveSocket(`/ws/requests/${req.id}`, setReq)
  }, [req?.id])

  async function send(lat, lon) {
    setBusy(true)
    setNote('Requesting an ambulance…')
    try {
      const r = await api('/api/requests', { method: 'POST', body: { lat, lon, condition, phone: phone || null, patient_name: patientName || null, landmark: landmark || null } })
      remember(r.id)
      setReq(r)
      setPickMode(false)
      setPicked(null)
      setNote('')
    } catch (e) {
      setNote(e.message)
    } finally {
      setBusy(false)
    }
  }

  function locateAndSend() {
    if (!navigator.geolocation) {
      setNote('This browser cannot share your location. Tap your location on the map instead.')
      setPickMode(true)
      return
    }
    setBusy(true)
    setNote('Finding your location…')
    navigator.geolocation.getCurrentPosition(
      pos => {
        const { latitude: lat, longitude: lon, accuracy } = pos.coords
        setMe({ lat, lon, accuracy: Math.round(accuracy) })
        send(lat, lon)
      },
      err => {
        setBusy(false)
        setNote(err.code === 1
          ? 'Location permission was denied. Tap your location on the map instead.'
          : 'Could not get your location. Tap your location on the map instead.')
        setPickMode(true)
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 },
    )
  }

  async function searchAddress(e) {
    e.preventDefault()
    if (query.trim().length < 3) { setNote('Type at least 3 letters of the address.'); return }
    setSearching(true)
    setNote('')
    try {
      const near = me ? `&lat=${me.lat}&lon=${me.lon}` : ''
      const r = await api(`/api/geocode?q=${encodeURIComponent(query.trim())}${near}`)
      setResults(r)
      if (!r.length) setNote('No place found. Try adding the area and city, e.g. "Sitabuldi, Nagpur".')
    } catch (err) {
      setResults([])
      setNote(err.message)
    } finally {
      setSearching(false)
    }
  }

  function choosePlace(p) {
    setPicked([p.lat, p.lon])
    setPickedName(p.name)
    setResults(null)
  }

  function leavePick() {
    setPickMode(false); setPicked(null); setPickedName(''); setResults(null); setQuery(''); setNote('')
  }

  async function cancel() {
    try { setReq(await api(`/api/requests/${req.id}/cancel`, { method: 'POST' })) } catch (e) { setNote(e.message) }
  }

  function reset() {
    remember(null)
    setReq(null)
    setNote('')
  }

  const detailProps = {
    phone, setPhone, patientName, setPatientName, landmark, setLandmark,
    landmarkHint: 'e.g. Opposite Hanuman temple, gate 2',
  }
  const active = req && ACTIVE.includes(req.status)
  const rerouting = !!req?.rerouting
  const patient = req?.patient
  const amb = req?.ambulance?.position
  const hosp = req?.hospital ? [req.hospital.lat, req.hospital.lon] : null
  const fitPoints = req ? [patient, req.status === 'to_hospital' || req.status === 'arrived' ? hosp : amb]
    : picked ? [picked] : me ? [[me.lat, me.lon]] : []
  const fitKey = req ? `${req.id}-${req.ambulance?.id}-${req.hospital?.name}`
    : picked ? `pick-${picked[0]}-${picked[1]}` : me ? 'me' : null
  const stepIndex = STEPS.findIndex(s => s.key === req?.status)

  return (
    <div className="user-layout">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">+</span> Emergency Route Optimizer</div>
        <a className="call108" href="tel:108">Call 108</a>
      </header>
      <div className="proto-banner">
        Student prototype for demonstration. Ambulances shown here are simulated. In a real emergency call <b>108</b> or <b>112</b>.
      </div>

      <div className="user-main">
        {rerouting && (
          <div className="reroute-banner" role="alert">
            <b>↻ Rerouting the ambulance</b>
            <span><i className="key red" /> old route (blocked or slow) <i className="key blue" /> new faster route</span>
          </div>
        )}
        <MapContainer center={INDIA} zoom={5} className="map" zoomControl={false}>
          <Tiles />
          <MapClick onClick={ll => { if (pickMode && !req) { setPicked([ll.lat, ll.lng]); setPickedName('Location chosen on the map') } }} />
          <FitTo points={fitPoints} fitKey={fitKey} />
          {me && !req && (
            <CircleMarker center={[me.lat, me.lon]} radius={9} pathOptions={{ color: '#1d5fd0', fillColor: '#fff', fillOpacity: 1, weight: 3 }}>
              <Tooltip>You (accuracy about {me.accuracy} m)</Tooltip>
            </CircleMarker>
          )}
          {picked && !req && <Marker position={picked} icon={pinIcon} />}
          {req && <RouteLines route={req.route} oldRoute={req.old_route} />}
          {req && <EventMarks marks={req.marks} />}
          {patient && <Marker position={patient} icon={patientIcon}><Tooltip>Patient</Tooltip></Marker>}
          {hosp && <Marker position={hosp} icon={hospitalIcon}><Tooltip>{req.hospital.name}</Tooltip></Marker>}
          {amb && <Marker position={amb} icon={ambulanceIcon}><Tooltip>{req.ambulance.id}</Tooltip></Marker>}
        </MapContainer>

        <section className="sheet">
          {!req && !pickMode && (
            <>
              <h1 className="sheet-title">Need an ambulance?</h1>
              <p className="muted">Choose what happened, then send an ambulance to where you are.</p>
              <div className="chips" role="radiogroup" aria-label="What happened">
                {CONDITIONS.map(c => (
                  <button key={c.value} role="radio" aria-checked={condition === c.value}
                          className={`chip ${condition === c.value ? 'on' : ''}`} onClick={() => setCondition(c.value)}>
                    {c.label}
                  </button>
                ))}
              </div>
              <button className="sos" onClick={locateAndSend} disabled={busy}>
                {busy ? 'Please wait…' : 'Send ambulance to my location'}
              </button>
              <div className="or"><span>or</span></div>
              <button className="pick-btn" onClick={() => { setPickMode(true); setNote('') }}>
                <span className="pick-ic" aria-hidden="true">📍</span>
                <span><b>Type an address or choose on the map</b><small>For someone else, or if location is off</small></span>
              </button>
              <Details {...detailProps} collapsed />
            </>
          )}

          {!req && pickMode && (
            <>
              <h1 className="sheet-title">Where is the patient?</h1>
              <form className="search" onSubmit={searchAddress} role="search">
                <label className="field">
                  <span>Type an address, landmark or area</span>
                  <div className="search-row">
                    <input id="address" type="search" value={query} onChange={e => setQuery(e.target.value)}
                           placeholder="e.g. Sitabuldi, Nagpur" autoComplete="street-address" enterKeyHint="search" />
                    <button className="search-btn" disabled={searching}>{searching ? '…' : 'Search'}</button>
                  </div>
                </label>
              </form>
              {results && results.length > 0 && (
                <ul className="results">
                  {results.map((p, i) => (
                    <li key={i}><button onClick={() => choosePlace(p)}>{p.name}</button></li>
                  ))}
                </ul>
              )}
              <p className="muted small">Or tap the exact place on the map.</p>
              {picked && <div className="picked">📍 {pickedName || 'Selected location'}</div>}
              <Details {...detailProps} />
              <button className="sos" disabled={!picked || busy} onClick={() => send(picked[0], picked[1])}>
                {busy ? 'Please wait…' : picked ? 'Send ambulance here' : 'Search or tap the map first'}
              </button>
              <button className="linkish" onClick={leavePick}>Back</button>
            </>
          )}

          {req && (
            <>
              <div className={`status-pill s-${req.status}`}>{req.status.replace('_', ' ')}</div>
              <h1 className="sheet-title">{req.status_text}</h1>

              {stepIndex >= 0 && (
                <ol className="steps">
                  {STEPS.map((s, i) => <li key={s.key} className={i < stepIndex ? 'done' : i === stepIndex ? 'now' : ''}>{s.label}</li>)}
                </ol>
              )}

              {(req.status === 'to_patient' || req.status === 'to_hospital') && (
                <div className="eta">
                  <span className="eta-label">{req.status === 'to_patient' ? 'Arriving in' : 'Hospital in'}</span>
                  <span className="eta-value">{fmtEta(req.eta_s)}</span>
                </div>
              )}

              {(req.patient_name || req.landmark) && (
                <div className="info-row">🧑 <b>{req.patient_name || 'Patient'}</b>{req.landmark ? <> · 📌 {req.landmark}</> : null}</div>
              )}
              {req.ambulance && (
                <div className="crew-card">
                  <div className="crew-top">
                    <span className="plate">{req.ambulance.vehicle_no}</span>
                    <span className="muted small">{req.ambulance.type}</span>
                  </div>
                  <div className="crew-row">🚑 {req.ambulance.id} from <b>{req.ambulance.base}</b></div>
                  <div className="crew-row">👤 Driver: <b>{req.ambulance.driver}</b> · 📞 {req.ambulance.driver_phone}</div>
                  {req.ambulance.paramedic && <div className="crew-row">🩺 Paramedic: <b>{req.ambulance.paramedic}</b></div>}
                </div>
              )}
              {req.hospital && <div className="info-row">🏥 <b>{req.hospital.name}</b></div>}
              {req.reroutes > 0 && <div className="info-row muted">Route updated {req.reroutes} time{req.reroutes > 1 ? 's' : ''} to avoid delays</div>}

              {req.events?.length > 0 && (
                <ul className="events">
                  {[...req.events].reverse().map((e, i) => (
                    <li key={i} className={`ev-${e.type}`}><time>{fmtAgo(e.t)}</time><span>{e.text}</span></li>
                  ))}
                </ul>
              )}

              <div className="actions">
                {active && ['preparing', 'waiting', 'to_patient'].includes(req.status) && (
                  <button className="secondary" onClick={cancel}>Cancel request</button>
                )}
                {!active && <button className="sos" onClick={reset}>New request</button>}
              </div>
            </>
          )}

          {note && <p className="note" role="status">{note}</p>}
        </section>
      </div>
    </div>
  )
}
