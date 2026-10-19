import { webClient } from './client'

export type StrategyBookRunStatus = 'live-entered' | 'active' | 'parked' | 'mind-down'

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
  notes: string | null
  readable: boolean
  days: string[]
  counts: Record<string, number>
  latest: StrategyTimelineEvent | null
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
