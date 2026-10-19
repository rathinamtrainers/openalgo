import { ArrowLeft, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  getBookTimeline,
  type StrategyTimeline,
  type StrategyTimelineEvent,
} from '@/api/strategy-books'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
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
      <span
        className={`absolute left-0 top-1.5 h-3 w-3 rounded-full ${kindClass(event.kind)}`}
      />
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

export default function StrategyBookTimeline() {
  const { bookId = 'analyzer-nifty' } = useParams()
  const [log, setLog] = useState<StrategyTimeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [day, setDay] = useState('')
  const [kind, setKind] = useState<(typeof KINDS)[number]>('all')

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
              Event timeline for this book. Each row says what happened and why.
            </p>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
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
            <CardDescription>Shown events</CardDescription>
            <CardTitle className="text-lg">{log?.events.length ?? 0}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {Object.entries(log?.counts || {})
              .map(([key, value]) => `${key} ${value}`)
              .join(' · ') || '—'}
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
      ) : !log?.events.length ? (
        <Card className="py-10">
          <CardContent className="text-center text-muted-foreground">
            No events match this filter. New decisions, fills, and status changes will land here.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <ol className="relative">
              {log.events.map((event) => (
                <TimelineRow key={event.event_id} event={event} />
              ))}
            </ol>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
