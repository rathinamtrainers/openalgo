import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { SocketProvider } from '@/components/socket/SocketProvider'
import { useAuthStore } from '@/stores/authStore'
import { MobileBottomNav } from './MobileBottomNav'
import { Navbar } from './Navbar'

export function Layout() {
  const { isAuthenticated, user } = useAuthStore()
  const location = useLocation()
  // The BTC desk reads Delta directly. It opens after the OpenAlgo sign-in
  // and does not wait for the Zerodha broker session.
  if (location.pathname === '/btc') {
    try {
      sessionStorage.setItem('oa-after-login', '/btc')
    } catch {
      // ignore
    }
  }
  const btcWithoutBroker = location.pathname === '/btc' && Boolean(user?.username)

  // AuthSync has already synced Flask session with Zustand store
  // So we just need to check the Zustand store state
  if (!isAuthenticated && !btcWithoutBroker) {
    const loginTarget = location.pathname === '/btc' ? '/login?next=/btc' : '/login'
    return <Navigate to={loginTarget} replace />
  }

  // If logged in but no broker selected, redirect to broker selection
  if (!user?.broker && !btcWithoutBroker) {
    return <Navigate to="/broker" replace />
  }

  const shell = (
    <div className="app-shell flex flex-col">
      <div className="app-atmosphere" aria-hidden />
      <Navbar />
      <main className="container mx-auto px-4 py-6 pb-24 md:pb-6 flex-1">
        <Outlet />
      </main>
      <MobileBottomNav />
    </div>
  )

  if (!user?.broker) {
    return shell
  }

  return <SocketProvider>{shell}</SocketProvider>
}

export function PublicLayout() {
  return (
    <div className="app-shell">
      <div className="app-atmosphere" aria-hidden />
      <Outlet />
    </div>
  )
}
