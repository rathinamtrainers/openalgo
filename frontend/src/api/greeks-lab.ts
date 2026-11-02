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
  mood?: string | null
  disclaimer?: string | null
  missing?: boolean
}

export function boardHasGreeks(board: CoaBoard | null | undefined): boolean {
  return Boolean(board?.greeks_exchange && board?.greeks_underlying)
}

export function boardSessionExchange(board: CoaBoard | null | undefined): string {
  const slug = (board?.slug || '').toLowerCase()
  if (slug === 'btc-options' || slug === 'btc' || board?.title === 'BTC') return 'CRYPTO'
  return board?.greeks_exchange || 'NSE'
}

export type CoaMapTone = 'call' | 'floor' | 'spot' | 'ceiling' | 'put'

export interface CoaMapMark {
  key: string
  label: string
  value: number
  pct: number
  tone: CoaMapTone
}

export interface CoaMapBand {
  top: number
  height: number
  left: number
  width: number
}

export interface CoaMapLayout {
  callZone: CoaMapBand | null
  putZone: CoaMapBand | null
  inside: CoaMapBand | null
  marks: CoaMapMark[]
  spotPct: number | null
}

function mapPct(value: number, low: number, high: number): number {
  if (high <= low) return 50
  return Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100))
}

function mapBand(from: number, to: number, low: number, high: number): CoaMapBand | null {
  const lo = Math.min(from, to)
  const hi = Math.max(from, to)
  if (hi <= lo) return null
  const bottom = mapPct(lo, low, high)
  const top = mapPct(hi, low, high)
  const span = top - bottom
  return { top: 100 - top, height: span, left: bottom, width: span }
}

export function buildCoaMap(board: CoaBoard | null | undefined): CoaMapLayout | null {
  if (!board) return null
  const spot = board.last ?? board.spot
  const floor = board.support
  const ceiling = board.resistance
  const callBuy = board.eos
  const putBuy = board.eor
  const values = [spot, floor, ceiling, callBuy, putBuy].filter(
    (value): value is number => value != null && Number.isFinite(value)
  )
  if (values.length < 2) return null
  const minV = Math.min(...values)
  const maxV = Math.max(...values)
  if (maxV <= minV) return null
  const pad = (maxV - minV) * 0.08
  const low = minV - pad
  const high = maxV + pad
  const marks: CoaMapMark[] = []
  const push = (key: string, label: string, value: number | null | undefined, tone: CoaMapTone) => {
    if (value == null || !Number.isFinite(value)) return
    marks.push({ key, label, value, pct: mapPct(value, low, high), tone })
  }
  push('eor', 'Put-buy', putBuy, 'put')
  push('resistance', 'Ceiling', ceiling, 'ceiling')
  push('spot', 'Last', spot, 'spot')
  push('support', 'Floor', floor, 'floor')
  push('eos', 'Call-buy', callBuy, 'call')
  marks.sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
  return {
    callZone: callBuy != null && floor != null ? mapBand(callBuy, floor, low, high) : null,
    putZone: putBuy != null && ceiling != null ? mapBand(ceiling, putBuy, low, high) : null,
    inside: floor != null && ceiling != null ? mapBand(floor, ceiling, low, high) : null,
    marks,
    spotPct: spot == null ? null : mapPct(spot, low, high),
  }
}

export interface CoaBoardsResponse {
  status: 'success' | 'error'
  message?: string
  boards?: CoaBoard[]
}

export interface CoaTrade {
  id: string
  board: string
  title: string
  side: string
  scenario?: string | null
  in_at?: string | null
  in_ist?: string | null
  out_at?: string | null
  out_ist?: string | null
  day?: string | null
  in_label: string
  out_label: string
  rule?: string | null
  hold_s?: number | null
  hold_label: string
  ist_span?: string | null
  entry_ltp?: number | null
  exit_ltp?: number | null
  strike?: number | null
  strike_label?: string | null
  expiry?: string | null
  symbol?: string | null
  lot_size?: number | null
  lots?: number | null
  qty?: number | null
  currency?: string | null
  margin_used?: number | null
  peak_ltp?: number | null
  peak_pct?: number | null
  mark_ltp?: number | null
  atm_pnl?: number | null
  mfe?: number | null
  eos?: number | null
  eor?: number | null
  support?: number | null
  resistance?: number | null
  open?: boolean
  exit_scenario?: string | null
}

export interface CoaTradesResponse {
  status: 'success' | 'error'
  message?: string
  trades?: CoaTrade[]
  count?: number
}

export interface CoaScenarioGloss {
  code: string
  map: string
  trade: string
  line: string
  tip: string
}

const COA_SCENARIOS: Record<string, { map: string; trade: string }> = {
  '1.1': { map: 'Floor and ceiling are both holding', trade: 'CE and PE can arm' },
  '1.2': { map: 'Floor holding, ceiling softening lower', trade: 'PE only' },
  '1.3': { map: 'Floor holding, ceiling softening higher', trade: 'CE only' },
  '1.4': { map: 'Floor softening lower, ceiling holding', trade: 'PE only' },
  '1.5': { map: 'Floor softening higher, ceiling holding', trade: 'CE only' },
  '1.6': { map: 'Both sides softening lower', trade: 'PE only' },
  '1.7': { map: 'Both sides softening higher', trade: 'CE only' },
  '1.8': { map: 'Mixed — floor off lower, ceiling off higher', trade: 'Stand aside' },
  '1.9': { map: 'Mixed — floor off higher, ceiling off lower', trade: 'Stand aside' },
}

export function scenarioCode(raw?: string | null): string | null {
  const match = String(raw || '').match(/(\d\.\d)/)
  return match ? match[1] : null
}

export function scenarioGloss(raw?: string | null): CoaScenarioGloss | null {
  const code = scenarioCode(raw)
  if (!code) return null
  const item = COA_SCENARIOS[code]
  if (!item) return null
  return {
    code,
    map: item.map,
    trade: item.trade,
    line: `${item.map} · ${item.trade}`,
    tip: `CoA ${code}: ${item.map}. ${item.trade}.`,
  }
}

export function inScenarioLine(row: CoaTrade): string | null {
  return scenarioGloss(row.scenario || row.in_label)?.line || null
}

export function outRuleLine(row: CoaTrade): string | null {
  const text = `${row.rule || ''} ${row.out_label || ''}`
  const side = (row.side || '').toUpperCase() === 'PE' ? 'PE' : 'CE'
  if (/scenario kill/i.test(text)) {
    const gloss = scenarioGloss(row.exit_scenario || row.out_label || row.rule)
    const map = gloss?.map || `CoA ${scenarioCode(row.exit_scenario || row.out_label) || '?'}`
    return `${map} · ${side} no longer allowed`
  }
  if (/spot target opposite/i.test(text)) {
    return side === 'PE' ? 'Spot printed the floor (support)' : 'Spot printed the ceiling (resistance)'
  }
  if (/flatten/i.test(text)) return 'House flatten at 15:15 IST'
  if (/spot SL/i.test(text)) return 'Spot broke the extension stop'
  if (/TSL live S\/R|reclaim/i.test(text)) return 'Trail after reclaim of live S/R'
  if (/premium fuse/i.test(text)) return 'ATM premium lost 30% from entry'
  if (/premium TSL/i.test(text)) return 'Premium trail after +40% arm, lock +20%'
  if (row.open) return 'Still in — waiting on a house exit'
  return scenarioGloss(row.exit_scenario)?.line || null
}

export function istTradeDay(date = new Date()): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date)
}

export function marginOf(row: CoaTrade): number | null {
  if (row.margin_used != null && !Number.isNaN(row.margin_used)) return row.margin_used
  if (row.entry_ltp == null || row.qty == null) return null
  if (Number.isNaN(row.entry_ltp) || Number.isNaN(row.qty) || row.qty <= 0) return null
  return Math.round(row.entry_ltp * row.qty * 100) / 100
}

export function marginByCurrency(rows: CoaTrade[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const row of rows) {
    const value = marginOf(row)
    if (value == null) continue
    const currency = row.currency || 'INR'
    out[currency] = (out[currency] || 0) + value
  }
  return out
}

export function pnlByCurrency(rows: CoaTrade[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const row of rows) {
    if (row.atm_pnl == null || Number.isNaN(row.atm_pnl)) continue
    const currency = row.currency || 'INR'
    out[currency] = (out[currency] || 0) + row.atm_pnl
  }
  return out
}

export function runningPnl(rows: CoaTrade[]): Map<string, number> {
  const chrono = [...rows].sort((a, b) => String(a.in_at || '').localeCompare(String(b.in_at || '')))
  const out = new Map<string, number>()
  const run: Record<string, number> = {}
  for (const row of chrono) {
    if (row.atm_pnl == null || Number.isNaN(row.atm_pnl)) continue
    const currency = row.currency || 'INR'
    run[currency] = (run[currency] || 0) + row.atm_pnl
    out.set(row.id, run[currency])
  }
  return out
}

export function dayCumulativePnl(
  groups: Array<[string, CoaTrade[]]>
): Map<string, Record<string, number>> {
  const out = new Map<string, Record<string, number>>()
  const run: Record<string, number> = {}
  for (const [day, dayRows] of [...groups].reverse()) {
    const part = pnlByCurrency(dayRows)
    for (const [currency, value] of Object.entries(part)) {
      run[currency] = (run[currency] || 0) + value
    }
    out.set(day, { ...run })
  }
  return out
}

export const greeksLabApi = {
  getCoaBoards: async (): Promise<CoaBoardsResponse> => {
    const response = await webClient.get<CoaBoardsResponse>('/greekslab/api/coa')
    return response.data
  },
  getCoaTrades: async (): Promise<CoaTradesResponse> => {
    const response = await webClient.get<CoaTradesResponse>('/greekslab/api/coa-trades')
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
