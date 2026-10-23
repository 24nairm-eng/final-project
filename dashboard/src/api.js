// Small helpers shared by the user and admin pages.

export async function api(path, { method = 'GET', body, token } = {}) {
  const res = await fetch(path, {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  let data = null
  try { data = await res.json() } catch { /* empty response */ }
  if (!res.ok) {
    const err = new Error((data && data.detail && (typeof data.detail === 'string' ? data.detail : 'Invalid input')) || `Error ${res.status}`)
    err.status = res.status
    throw err
  }
  return data
}

export function wsUrl(path) {
  return `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}${path}`
}

// Live updates over a WebSocket that reconnects by itself (phones drop connections often).
export function liveSocket(path, onMessage, onClose) {
  let ws, stopped = false, retry
  const open = () => {
    ws = new WebSocket(wsUrl(path))
    ws.onmessage = e => onMessage(JSON.parse(e.data))
    ws.onclose = e => {
      if (stopped) return
      if (onClose && onClose(e) === false) return
      retry = setTimeout(open, 2000)
    }
  }
  open()
  return () => { stopped = true; clearTimeout(retry); ws && ws.close() }
}

export function fmtEta(s) {
  if (s == null) return '--:--'
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

export function fmtAgo(s) {
  const m = Math.floor(s / 60)
  return `+${m}:${String(s % 60).padStart(2, '0')}`
}

export const INDIA = [22.6, 79.0]
