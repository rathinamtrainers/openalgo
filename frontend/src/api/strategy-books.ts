import { webClient } from './client'

export type StrategyBookRunStatus = 'live-entered' | 'active' | 'parked' | 'mind-down'

export interface StrategyBookPerformanceSummary {
  fills: number
  buys: number
  sells: number
  closed_round_trips: number
  wins: number
  losses: number
  win_rate_pct: number | null
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  open_quantity: number
  days_traded: number
  first_trade_at_ist: string | null
  last_trade_at_ist: string | null
  enter_decisions: number
  hold_decisions: number
  decline_decisions: number
  capital_deployed: number
  account_capital: number
  roi_pct: number | null
  account_roi_pct: number | null
  cagr_pct: number | null
  cagr_potential_pct: number | null
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  max_drawdown_pct: number | null
  profit_factor: number | null
  expectancy: number | null
  avg_win: number | null
  avg_loss: number | null
  calendar_days?: number
  trading_days?: number
  profit_days?: number
  loss_days?: number
  avg_profit_day?: number | null
  avg_loss_day?: number | null
  avg_day?: number | null
  sample_days: number
  sample_note: string | null
}

export interface StrategyBookTrade {
  trade_id: string
  created_at_utc: string
  created_at_ist: string
  trading_day: string
  symbol: string
  exchange: string
  product: string
  action: string
  quantity: number
  price: number
  strategy: string
}

export interface StrategyBookRoundTrip {
  symbol: string
  exchange: string
  product: string
  quantity: number
  side: string
  entry_price: number
  exit_price: number
  pnl: number
  roi_pct?: number | null
  capital?: number | null
  entry_at_ist: string | null
  exit_at_ist: string | null
  trading_day: string | null
}

export interface StrategyBookOpenPosition {
  symbol: string
  exchange: string
  product: string
  quantity: number
  average_price: number
  ltp: number
  unrealized_pnl: number
  side: string
  opened_at_ist?: string | null
}

export interface StrategyBookPerformance {
  source: 'openalgo-analyze' | 'tradetron-hands' | 'none' | string
  source_note: string
  day: string
  summary: StrategyBookPerformanceSummary
  trades?: StrategyBookTrade[]
  round_trips?: StrategyBookRoundTrip[]
  open_positions?: StrategyBookOpenPosition[]
}

export interface StrategyTradeSummary {
  strategy_id: string
  strategy_name: string
  status: 'closed' | 'open' | string
  symbol: string
  exchange?: string
  product?: string
  side: string
  quantity: number
  entry_price: number
  exit_price: number | null
  pnl: number | null
  roi_pct: number | null
  entry_at_ist: string | null
  exit_at_ist: string | null
  trading_day: string | null
}

export interface StrategyReportRow {
  id: string
  name: string
  kind: string
  href?: string | null
  performance: StrategyBookPerformance
}

export interface StrategyNarrativeLine {
  id: string
  name: string
  text: string
}

export interface StrategyNarrative {
  source: 'template' | 'llm' | string
  overall: string
  strategies: StrategyNarrativeLine[]
}

export interface StrategyPerformanceReport {
  overall: StrategyBookPerformance
  strategies: StrategyReportRow[]
  trade_summaries: StrategyTradeSummary[]
  narrative?: StrategyNarrative
}

export interface StrategyBook {
  id: string
  name: string
  kind: string
  kind_label: string
  underlying: string
  execution_target: string
  execution_label: string
  tt_sid: number | null
  tt_template_id: number | null
  parked: boolean
  notes: string | null
  run_status: StrategyBookRunStatus
  detail: string
  mind: 'up' | 'down'
  audit_href?: string | null
  performance?: StrategyBookPerformance
  status_plain?: string
  latest?: StrategyTimelineEvent | null
  sections?: {
    key_decisions: StrategyTimelineEvent[]
    event_log: StrategyTimelineEvent[]
    audit_trail: StrategyTimelineEvent[]
  }
}

export type DecisionOutcome = 'enter' | 'hold' | 'decline'

export interface DeskDecision {
  tick_id: string
  created_at_utc: string
  created_at_ist: string
  trading_day: string
  index_symbol: string
  trigger: string
  outcome: DecisionOutcome | string
  reason_code: string
  reason_text: string
  regime_label: string | null
  regime_confidence: number | null
  latency_ms: number | null
  trace_complete: boolean
  book: {
    flat?: boolean | null
    decisions_today?: number | null
    positions?: string[]
  }
}

export interface DeskDecisionLog {
  book_id: string
  name: string
  cadence_seconds: number
  source: 'journal' | 'export' | 'missing'
  source_path: string
  readable: boolean
  days: string[]
  counts: Record<DecisionOutcome, number>
  decisions: DeskDecision[]
}

interface BooksResponse {
  status: 'success' | 'error'
  books?: StrategyBook[]
  message?: string
}

export async function getStrategyBooks(): Promise<StrategyBook[]> {
  const res = await webClient.get<BooksResponse>('/strategy/api/books')
  if (res.data.status !== 'success' || !res.data.books) {
    throw new Error(res.data.message || 'Failed to load strategy books')
  }
  return res.data.books
}

export async function getStrategyBankBoard(snippet = 5): Promise<StrategyBook[]> {
  const res = await webClient.get<BooksResponse>('/strategy/api/books/board', {
    params: { snippet },
  })
  if (res.data.status !== 'success' || !res.data.books) {
    throw new Error(res.data.message || 'Failed to load strategy bank')
  }
  return res.data.books
}

interface DecisionsResponse extends DeskDecisionLog {
  status: 'success' | 'error'
  message?: string
}

export async function getBookDecisions(
  bookId: string,
  params: { day?: string; outcome?: string; limit?: number } = {}
): Promise<DeskDecisionLog> {
  const res = await webClient.get<DecisionsResponse>(`/strategy/api/books/${bookId}/decisions`, {
    params,
  })
  if (res.data.status !== 'success') {
    throw new Error(res.data.message || 'Failed to load decision log')
  }
  return res.data
}

export type TimelineKind = 'decision' | 'fill' | 'status' | 'action'

export interface StrategyTimelineEvent {
  event_id: string
  book_id: string
  created_at_utc: string
  created_at_ist: string
  trading_day: string
  kind: TimelineKind | string
  title: string
  outcome: string | null
  reason_code: string | null
  explanation: string
  detail: string | null
}

export interface StrategyTimeline {
  book_id: string
  name: string
  kind: string
  underlying: string
  execution_target?: string | null
  tt_sid?: number | null
  tt_template_id?: number | null
  audit_href?: string | null
  notes: string | null
  readable: boolean
  days: string[]
  counts: Record<string, number>
  latest: StrategyTimelineEvent | null
  sections?: {
    key_decisions: StrategyTimelineEvent[]
    event_log: StrategyTimelineEvent[]
    audit_trail: StrategyTimelineEvent[]
  }
  performance?: StrategyBookPerformance
  events: StrategyTimelineEvent[]
}

interface TimelineResponse extends StrategyTimeline {
  status: 'success' | 'error'
  message?: string
}

export async function getBookTimeline(
  bookId: string,
  params: { day?: string; kind?: string; limit?: number } = {}
): Promise<StrategyTimeline> {
  const res = await webClient.get<TimelineResponse>(`/strategy/api/books/${bookId}/timeline`, {
    params,
  })
  if (res.data.status !== 'success') {
    throw new Error(res.data.message || 'Failed to load timeline')
  }
  return res.data
}
