import { ArrowLeft, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getBookTimeline, type StrategyTimeline, type StrategyTimelineEvent } from '@/api/strategy-books'
import { StrategyPerformancePanel } from '@/components/strategy/StrategyPerformancePanel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatStrategyPnl, strategyPnlClass } from '@/lib/strategy-performance'
import { showToast } from '@/utils/toast'

const KINDS = ['all', 'decision', 'fill', 'status', 'action'] as const

function kindClass(kind: string) {
  if (kind === 'fill' || kind === 'enter') return 'bg-green-600'
  if (kind === 'decision') return 'bg-amber-500'
  if (kind === 'action') return 'bg-blue-600'
  return 'bg-slate-500'
}

function outcomeClass(outcome: string | null, kind: string) {
  if (outcome === 'enter') return 'bg-green-600 text-white'
  if (outcome === 'hold') return 'bg-blue-600 text-white'
  if (kind === 'fill') return 'bg-green-600 text-white'
  return 'bg-amber-600 text-white'
}

function TimelineRow({ event }: { event: StrategyTimelineEvent }) {
  return (
    <li className="relative pl-8 pb-6 last:pb-0">
      <span className={`absolute left-0 top-1.5 h-3 w-3 rounded-full ${kindClass(event.kind)}`} />
      <span className="absolute left-[5px] top-5 bottom-0 w-px bg-border last:hidden" />
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">{event.created_at_ist || event.created_at_utc}</p>
          <Badge className={outcomeClass(event.outcome, event.kind)}>{event.kind}</Badge>
          {event.reason_code && (
            <Badge variant="outline" className="font-mono font-normal">
              {event.reason_code}
            </Badge>
          )}
        </div>
        <h3 className="text-base font-semibold">{event.title}</h3>
        <p className="text-sm text-muted-foreground">{event.explanation}</p>
        {event.detail && <p className="text-xs text-muted-foreground">{event.detail}</p>}
      </div>
    </li>
  )
}

function isKeyDecision(event: StrategyTimelineEvent) {
  return (
    event.kind === 'decision' ||
    event.kind === 'status' ||
    event.kind === 'action' ||
    (event.kind === 'fill' && event.outcome === 'enter')
  )
}

function EventList({
  events,
  empty,
}: {
  events: StrategyTimelineEvent[]
  empty: string
}) {
  if (!events.length) {
    return (
      <Card className="py-10">
        <CardContent className="text-center text-muted-foreground">{empty}</CardContent>
      </Card>
    )
  }
  return (
    <Card>
      <CardContent className="pt-6">
        <ol className="relative">
          {events.map((event) => (
            <TimelineRow key={event.event_id} event={event} />
          ))}
        </ol>
      </CardContent>
    </Card>
  )
}

export default function StrategyBookTimeline() {
  const { bookId = 'analyzer-nifty' } = useParams()
  const [log, setLog] = useState<StrategyTimeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [day, setDay] = useState('')
  const [kind, setKind] = useState<(typeof KINDS)[number]>('all')
  const [section, setSection] = useState('decisions')

  const load = useCallback(async () => {
    try {
      setLoading(true)
      setLog(
        await getBookTimeline(bookId, {
          day: day || undefined,
          kind: kind === 'all' ? undefined : kind,
          limit: 500,
        })
      )
    } catch {
      showToast.error('Failed to load the strategy timeline', 'strategy')
    } finally {
      setLoading(false)
    }
  }, [bookId, day, kind])

  useEffect(() => {
    load()
  }, [load])

  const latest = log?.latest || log?.events[0]
  const performance = log?.performance
  const summary = performance?.summary
  const decisions = useMemo(() => {
    const fromApi = log?.sections?.key_decisions
    if (fromApi && fromApi.length > 0) {
      return fromApi
    }
    return (log?.events || []).filter(isKeyDecision)
  }, [log])
  const eventLog = useMemo(
    () => log?.sections?.event_log || log?.events || [],
    [log]
  )
  const auditTrail = useMemo(
    () =>
      log?.sections?.audit_trail ||
      log?.events.filter((row) => row.kind === 'status' || row.kind === 'action' || row.kind === 'fill') ||
      [],
    [log]
  )

  return (
    <div className="container mx-auto py-6 space-y-6">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div className="space-y-2">
          <Button variant="ghost" size="sm" asChild className="-ml-2">
            <Link to="/strategy">
              <ArrowLeft className="h-4 w-4 mr-2" />
              My Strategies
            </Link>
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{log?.name || 'Strategy timeline'}</h1>
            <p className="text-muted-foreground">
              Performance, key decisions, event log, and audit trail for this book.
            </p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Latest</CardDescription>
            <CardTitle className="text-lg">{latest?.title || 'none yet'}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {latest?.created_at_ist || 'No events recorded for this book yet'}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Performance</CardDescription>
            <CardTitle className={`text-lg ${strategyPnlClass(summary?.total_pnl)}`}>
              {formatStrategyPnl(summary?.total_pnl)}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {summary?.roi_pct != null
              ? `${summary.roi_pct > 0 ? '+' : ''}${summary.roi_pct}% RoI · ${summary.fills} fills`
              : `${summary?.fills ?? 0} fills · ${summary?.closed_round_trips ?? 0} closed`}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Execution</CardDescription>
            <CardTitle className="text-lg">
              {log?.execution_target === 'tradetron-hands'
                ? 'Tradetron Hands'
                : log?.execution_target === 'openalgo-tradetron'
                  ? 'OpenAlgo Analyze + Tradetron Hands'
                  : log?.execution_target === 'openalgo'
                    ? 'OpenAlgo Analyze'
                    : log?.execution_target || '—'}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground font-mono">
            {log?.tt_sid != null
              ? `SID ${log.tt_sid}`
              : log?.tt_template_id != null
                ? `template ${log.tt_template_id}`
                : 'No Tradetron SID'}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Book</CardDescription>
            <CardTitle className="text-lg">{log?.underlying || bookId}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">{log?.notes || '—'}</CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap gap-3 items-center">
        <label className="text-sm text-muted-foreground" htmlFor="timeline-day">
          Day
        </label>
        <select
          id="timeline-day"
          className="h-9 rounded-md border bg-transparent px-3 text-sm"
          value={day}
          onChange={(event) => setDay(event.target.value)}
        >
          <option value="">All days</option>
          {(log?.days || []).map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
        <label className="text-sm text-muted-foreground" htmlFor="timeline-kind">
          Kind
        </label>
        <select
          id="timeline-kind"
          className="h-9 rounded-md border bg-transparent px-3 text-sm"
          value={kind}
          onChange={(event) => setKind(event.target.value as (typeof KINDS)[number])}
        >
          {KINDS.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </div>

      {loading && !log ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <Tabs value={section} onValueChange={setSection}>
          <TabsList>
            <TabsTrigger value="decisions">Key decisions</TabsTrigger>
            <TabsTrigger value="performance">Performance</TabsTrigger>
            <TabsTrigger value="events">Event log</TabsTrigger>
            <TabsTrigger value="audit">Audit trail</TabsTrigger>
          </TabsList>
          <TabsContent value="decisions" className="pt-4">
            <EventList
              events={decisions}
              empty="No key decisions yet. When the mind enters, holds, or flips, rows land here."
            />
          </TabsContent>
          <TabsContent value="performance" className="pt-4">
            <StrategyPerformancePanel performance={performance} />
          </TabsContent>
          <TabsContent value="events" className="pt-4">
            <EventList
              events={eventLog}
              empty="No events match this filter. New decisions, fills, and status changes will land here."
            />
          </TabsContent>
          <TabsContent value="audit" className="pt-4">
            <EventList
              events={auditTrail}
              empty="No audit rows yet. Status changes, Hands actions, and Analyze fills appear here."
            />
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
