import { webClient } from './client'

export interface BtcLine {
  kind: string
  expiry: string
  near: string
  far: string
  near_ask: number
  far_bid: number
  diff: number
  near_ask_size?: number
  far_bid_size?: number
}

export interface BtcDeskStatus {
  pid?: number
  mode?: string
  spot?: number
  expiries?: number
  contracts?: number
  quotes?: number
  changes?: number
  fresh?: number
  lines?: number
  arrests?: number
  updated?: string
  open_lines?: BtcLine[]
}

export interface BtcArrest {
  detected_at: number | null
  kind: string | null
  expiry: string | null
  near: string | null
  far: string | null
  near_ask: number | null
  far_bid: number | null
  diff: number | null
  spot: number | null
  mode: string | null
  qty: number | null
}

export interface BtcDeskData {
  alive: boolean
  age_seconds: number | null
  desk: BtcDeskStatus
  arrests: BtcArrest[]
  db_error: string | null
  positions_href: string
  orderbook_href: string
}

export interface BtcDeskResponse {
  status: 'success' | 'error'
  message?: string
  data?: BtcDeskData
}

export const btcDeskApi = {
  getDesk: async (): Promise<BtcDeskResponse> => {
    const response = await webClient.get<BtcDeskResponse>('/btc/api/desk')
    return response.data
  },
}
