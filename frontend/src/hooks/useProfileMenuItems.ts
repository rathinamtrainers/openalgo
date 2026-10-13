import { useEffect } from 'react'
import { profileMenuItems } from '@/config/navigation'
import { useAuthStore } from '@/stores/authStore'
import { useBrokerStore } from '@/stores/brokerStore'

/**
 * Profile dropdown items filtered by broker capabilities.
 *
 * Single source of truth for menu gating, used by the shared Navbar AND the
 * standalone full-screen layouts (Playground, Historify, Historify Charts,
 * Flow Editor) so crypto-only items (Leverage) and equity-only items
 * (Holdings) never leak into the wrong broker's menus (GitHub issue #1480).
 */
export function useProfileMenuItems() {
  const { capabilities, isLoaded, fetchCapabilities } = useBrokerStore()
  const brokerConnected = useAuthStore((state) => Boolean(state.user?.isLoggedIn && state.user?.broker))

  // Capabilities describe the connected broker. Fetching them before that
  // session exists hits a broker-only route and used to wipe the OpenAlgo sign-in.
  useEffect(() => {
    if (brokerConnected && !isLoaded) {
      fetchCapabilities()
    }
  }, [brokerConnected, isLoaded, fetchCapabilities])

  return profileMenuItems.filter((item) => {
    if (item.href === '/leverage') return capabilities?.leverage_config === true
    if (item.href === '/holdings') return capabilities?.broker_type !== 'crypto'
    return true
  })
}
