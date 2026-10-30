import { webClient } from './client'
import { gexApi } from './gex'

export interface GreeksLabLeg {
  symbol: string
  ltp: number
  iv: number | null
  delta: number | null
  gamma: number | null
  theta: number | null
  vega: number | null
  days_to_expiry: number | null
}

export interface GreeksLabRow {
  strike: number
  ce: GreeksLabLeg | null
  pe: GreeksLabLeg | null
}

export interface ThetaPoint {
  minute: number
  price: number
}

export interface SideProjection {
  theta_path: ThetaPoint[]
  whatif: Array<Array<number | null>>
}

export interface DeskInsightChip {
  state: string
  label: string
  detail: string
}

export interface DeskInsights {
  as_of_ist?: string
  straddle?: number | null
  theta?: number | null
  adx?: number | null
  vix?: number | null
  dte?: number | null
  is_expiry?: boolean
  decay?: DeskInsightChip
  quadshield?: DeskInsightChip
  session?: DeskInsightChip
  trend?: DeskInsightChip
  disclaimer?: string
}

export interface GreeksLabResponse {
  status: 'success' | 'error'
  message?: string
  underlying?: string
  exchange?: string
  expiry_date?: string
  spot_price?: number
  atm_strike?: number
  selected_strike?: number
  lot_size?: number
  horizon_minutes?: number
  atm_iv?: number | null
  expected_move?: number | null
  spot_shocks_pct?: number[]
  iv_shocks_pts?: number[]
  disclaimer?: string
  insights?: DeskInsights
  readout?: {
    source?: 'template' | 'llm' | string
    line?: string
    sit_still?: string | null
  }
  watched?: GreeksLabRow
  projections?: {
    ce: SideProjection
    pe: SideProjection
  }
  chain?: GreeksLabRow[]
}

export interface CoaBoard {
  slug: string
  title: string
  source?: 'coa' | 'quote' | string
  greeks_exchange: string | null
  greeks_underlying: string | null
  market?: string | null
  index?: string | null
  scenario_label?: string | null
  as_of_ist?: string | null
  computed_at?: string | null
  spot?: number | null
  last?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  change_vs_open?: number | null
  do_now?: string | null
  line?: string | null
  stance?: string | null
  support?: number | null
  resistance?: number | null
  eos?: number | null
  eor?: number | null
  missing?: boolean
}

export interface CoaBoardsResponse {
  status: 'success' | 'error'
  message?: string
  boards?: CoaBoard[]
}

export const greeksLabApi = {
  getCoaBoards: async (): Promise<CoaBoardsResponse> => {
    const response = await webClient.get<CoaBoardsResponse>('/greekslab/api/coa')
    return response.data
  },
  getSnapshot: async (params: {
    underlying: string
    exchange: string
    expiry_date: string
    strike?: number
    horizon_minutes?: number
  }): Promise<GreeksLabResponse> => {
    const response = await webClient.post<GreeksLabResponse>('/greekslab/api/snapshot', params)
    return response.data
  },
  getUnderlyings: gexApi.getUnderlyings,
  getExpiries: gexApi.getExpiries,
}
