import { useEffect, useState } from 'react'
import UserApp from './UserApp.jsx'
import AdminApp from './AdminApp.jsx'

// One website, two pages: the public page at "/" and the admin page at "/#/admin".
export default function App() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const onHash = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return hash.startsWith('#/admin') ? <AdminApp /> : <UserApp />
}
