import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'

export const ambulanceIcon = L.divIcon({ className: 'mk-amb', html: '<span>🚑</span>', iconSize: [34, 34], iconAnchor: [17, 17] })
export const patientIcon = L.divIcon({ className: 'mk-patient', html: '<span></span>', iconSize: [22, 22], iconAnchor: [11, 11] })
export const hospitalIcon = L.divIcon({ className: 'mk-hosp', html: '<span>H</span>', iconSize: [24, 24], iconAnchor: [12, 12] })
export const pinIcon = L.divIcon({ className: 'mk-pin', html: '<span></span>', iconSize: [26, 26], iconAnchor: [13, 26] })

export const STATUS_COLORS = {
  available: '#2e9d5b', to_patient: '#c8102e', at_scene: '#7b3fe4',
  to_hospital: '#e07b00', at_hospital: '#7b3fe4', returning: '#1d5fd0',
}

export function Tiles() {
  return (
    <TileLayer url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" maxZoom={19}
               attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' />
  )
}

// Calls onClick(latlng) when the map is tapped.
export function MapClick({ onClick }) {
  useMapEvents({ click: e => onClick && onClick(e.latlng) })
  return null
}

// Fits the map to a set of points once per change of `fitKey`.
export function FitTo({ points, fitKey, maxZoom = 16 }) {
  const map = useMap()
  const done = useRef(null)
  useEffect(() => {
    const pts = (points || []).filter(Boolean)
    if (!pts.length || done.current === fitKey) return
    done.current = fitKey
    if (pts.length === 1) map.flyTo(pts[0], maxZoom, { duration: 0.8 })
    else map.flyToBounds(L.latLngBounds(pts), { padding: [60, 60], maxZoom, duration: 0.8 })
  }, [fitKey, points, map, maxZoom])
  return null
}

// Accident / jam / road-closure markers.
const MARK_EMOJI = { accident: '💥', jam: '🚦', closure: '⛔' }
export const markIcon = type => L.divIcon({
  className: `mk-event mk-${type}`, html: `<span>${MARK_EMOJI[type] || '⚠'}</span>`, iconSize: [30, 30], iconAnchor: [15, 15],
})

// The route just abandoned (red, dashed) drawn under the new one (blue).
export function RouteLines({ route, oldRoute, weight = 6, color = '#1d5fd0' }) {
  return (
    <>
      {oldRoute?.length > 1 && (
        <Polyline positions={oldRoute} pathOptions={{ color: '#d7263d', weight: weight + 1, opacity: 0.9, dashArray: '10 8' }}>
          <Tooltip sticky>Old route (blocked or slow), no longer used</Tooltip>
        </Polyline>
      )}
      {route?.length > 1 && <Polyline positions={route} pathOptions={{ color, weight, opacity: 0.9 }} />}
    </>
  )
}

export function EventMarks({ marks }) {
  return (marks || []).map((m, i) => (
    <Marker key={`${m.type}-${m.pos[0]}-${m.pos[1]}-${i}`} position={m.pos} icon={markIcon(m.type)}>
      <Tooltip>{m.text}</Tooltip>
    </Marker>
  ))
}
