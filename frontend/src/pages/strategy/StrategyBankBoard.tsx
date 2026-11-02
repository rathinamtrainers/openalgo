import { Link } from 'react-router-dom'
import type { StrategyBook, StrategyTimelineEvent } from '@/api/strategy-books'
import { strategyPerformanceLine, strategyPnlClass } from '@/lib/strategy-performance'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

function bookBadge(status: StrategyBook['run_status']) {
  if (status === 'live-entered') return { label: 'Live-Entered', variant: 'default' as const }
  if (status === 'active') return { label: 'Active', variant: 'default' as const }
  if (status === 'parked') return { label: 'Parked', variant: 'secondary' as const }
  return { label: 'Mind down', variant: 'destructive' as const }
}

function statusBarClass(status: StrategyBook['run_status']) {
  if (status === 'live-entered') return 'bg-green-500'
  if (status === 'active') return 'bg-blue-500'
  if (status === 'parked') return 'bg-gray-400'
  return 'bg-red-500'
}

function LogSnippet({
  title,
  events,
  empty,
}: {
  title: string
  events: StrategyTimelineEvent[]
  empty: string
}) {
  return (
    <div className="min-w-0">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
        {title}
      </h3>
      {events.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul className="space-y-3">
          {events.map((event) => (
            <li key={event.event_id} className="space-y-0.5">
              <p className="text-sm font-medium leading-snug">{event.title}</p>
              <p className="text-sm text-muted-foreground leading-snug">{event.explanation}</p>
              <p className="text-xs text-muted-foreground">
                {event.created_at_ist || event.created_at_utc}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function BookPanel({ book }: { book: StrategyBook }) {
  const badge = bookBadge(book.run_status)
  const href = book.audit_href || `/strategy/books/${book.id}`
  const decisions = book.sections?.key_decisions || []
  const events = book.sections?.event_log || []
  const audit = book.sections?.audit_trail || []
  const statusPlain = book.status_plain || book.detail

  return (
    <Card className="relative overflow-hidden">
      <div className={`absolute top-0 left-0 right-0 h-1 ${statusBarClass(book.run_status)}`} />
      <CardHeader className="pb-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1 min-w-0">
            <CardTitle className="text-lg">{book.name}</CardTitle>
            <CardDescription>
              {book.kind_label} · {book.underlying}
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={badge.variant}>{badge.label}</Badge>
            <Button variant="outline" size="sm" asChild>
              <Link to={href}>Full log</Link>
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm leading-relaxed">{statusPlain}</p>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline" className="font-normal">
            {book.execution_label}
          </Badge>
          {book.tt_sid != null ? (
            <Badge variant="outline" className="font-normal font-mono">
              SID {book.tt_sid}
            </Badge>
          ) : book.tt_template_id != null ? (
            <Badge variant="outline" className="font-normal font-mono">
              template {book.tt_template_id}
            </Badge>
          ) : null}
          <Badge variant="outline" className="font-normal">
            mind {book.mind}
          </Badge>
        </div>
        <p className={`text-xs ${strategyPnlClass(book.performance?.summary.total_pnl)}`}>
          {strategyPerformanceLine(book.performance)}
        </p>
        <div className="grid gap-6 lg:grid-cols-3 pt-1">
          <LogSnippet
            title="Key decisions"
            events={decisions}
            empty="No decisions yet. Enter, hold, and flip rows land here."
          />
          <LogSnippet
            title="Event log"
            events={events}
            empty="No events yet. Status changes and fills will appear here."
          />
          <LogSnippet
            title="Audit trail"
            events={audit}
            empty="No audit rows yet. Status, Hands posts, and Analyze fills appear here."
          />
        </div>
      </CardContent>
    </Card>
  )
}

export function StrategyBankBoard({ books }: { books: StrategyBook[] }) {
  if (books.length === 0) {
    return (
      <Card className="py-10">
        <CardContent className="text-center text-muted-foreground">
          No Strike Desk books registered.
        </CardContent>
      </Card>
    )
  }
  return (
    <div className="space-y-4">
      {books.map((book) => (
        <BookPanel key={book.id} book={book} />
      ))}
    </div>
  )
}
