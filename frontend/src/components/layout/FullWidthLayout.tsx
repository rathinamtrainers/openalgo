import { Navigate, Outlet } from 'react-router-dom'
import { SocketProvider } from '@/components/socket/SocketProvider'
import { useAuthStore } from '@/stores/authStore'

/**
 * Full-width layout for apps like Playground that need maximum screen space.
 * No container constraints, minimal chrome.
 */
export function FullWidthLayout() {
  const { isAuthenticated, user } = useAuthStore()

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  if (!user?.broker) {
    return <Navigate to="/broker" replace />
  }

  return (
    <SocketProvider>
      <div className="app-shell h-screen flex flex-col overflow-hidden">
        <div className="app-atmosphere" aria-hidden />
        <Outlet />
      </div>
    </SocketProvider>
  )
}
