import { Link } from 'react-router-dom'
import type {
  StrategyBookPerformance,
  StrategyBookPerformanceSummary,
  StrategyPerformanceReport,
  StrategyReportRow,
  StrategyTradeSummary,
} from '@/api/strategy-books'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatPct, formatStrategyPnl, strategyPnlClass } from '@/lib/strategy-performance'
import { cn, makeFormatCurrency } from '@/lib/utils'

const formatInr = makeFormatCurrency()

function profitRatioLabel(summary: StrategyBookPerformanceSummary) {
  if (summary.profit_factor == null) {
    if ((summary.wins || 0) > 0 && (summary.losses || 0) === 0) return 'All wins'
    return '—'
  }
  return `${summary.profit_factor.toFixed(2)}×`
}

function yearlyPace(summary: StrategyBookPerformanceSummary) {
  const value = summary.cagr_potential_pct ?? summary.cagr_pct
  if (value == null || Number.isNaN(value) || Math.abs(value) > 500) return '—'
  return formatPct(value)
}

function dayCount(value: number | null | undefined) {
  if (!value) return '—'
  return String(value)
}

function allRows(report: StrategyPerformanceReport): StrategyReportRow[] {
  const overall: StrategyReportRow = {
    id: 'sandbox-all',
    name: 'All strategies',
    kind: 'all',
    href: null,
    performance: report.overall,
  }
  return [overall, ...report.strategies]
}

function isActive(row: StrategyReportRow) {
  const summary = row.performance.summary
  return summary.fills > 0 || summary.closed_round_trips > 0 || summary.unrealized_pnl !== 0
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: string
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn('text-sm font-semibold tabular-nums truncate', tone)}>{value}</p>
    </div>
  )
}

function StrategyCard({
  row,
  blurb,
  selected,
  onSelect,
}: {
  row: StrategyReportRow
  blurb?: string
  selected: boolean
  onSelect: (id: string) => void
}) {
  const summary = row.performance.summary
  const featured = row.id === 'sandbox-all'
  return (
    <button
      type="button"
      onClick={() => onSelect(row.id)}
      className={cn(
        'text-left rounded-xl border bg-card p-4 transition-colors',
        'hover:border-primary/40 hover:bg-muted/30',
        selected && 'border-primary ring-1 ring-primary/40',
        featured && 'sm:col-span-2 lg:col-span-2',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium leading-tight">{row.name}</p>
          <p className={cn('text-2xl font-bold tabular-nums mt-1', strategyPnlClass(summary.total_pnl))}>
            {formatStrategyPnl(summary.total_pnl)}
          </p>
        </div>
        {row.href ? (
          <Button asChild size="sm" variant="ghost" onClick={(e) => e.stopPropagation()}>
            <Link to={row.href}>Log</Link>
          </Button>
        ) : null}
      </div>
      {blurb ? <p className="mt-2 text-sm text-muted-foreground leading-snug">{blurb}</p> : null}
      <div className="mt-4 grid grid-cols-3 gap-x-3 gap-y-3">
        <Metric
          label="Finished"
          value={`${summary.closed_round_trips} · ${summary.wins}W ${summary.losses}L`}
        />
        <Metric label="Win" value={formatPct(summary.win_rate_pct)} />
        <Metric label="Profit ratio" value={profitRatioLabel(summary)} />
        <Metric label="Calendar days" value={dayCount(summary.calendar_days)} />
        <Metric label="Trading days" value={dayCount(summary.trading_days)} />
        <Metric
          label="Yearly pace"
          value={yearlyPace(summary)}
          tone={strategyPnlClass(
            Math.abs(summary.cagr_potential_pct ?? summary.cagr_pct ?? 0) > 500
              ? null
              : (summary.cagr_potential_pct ?? summary.cagr_pct),
          )}
        />
        <Metric
          label="Avg up day"
          value={formatStrategyPnl(summary.avg_profit_day)}
          tone={strategyPnlClass(summary.avg_profit_day)}
        />
        <Metric
          label="Avg down day"
          value={formatStrategyPnl(summary.avg_loss_day)}
          tone={strategyPnlClass(summary.avg_loss_day)}
        />
        <Metric
          label="Return"
          value={formatPct(summary.roi_pct)}
          tone={strategyPnlClass(summary.roi_pct)}
        />
      </div>
      <p className="mt-3 text-[11px] text-muted-foreground">
        {summary.max_drawdown_pct != null ? `Worst dip ${formatPct(summary.max_drawdown_pct)}. ` : ''}
        {summary.cagr_potential_pct == null
          ? 'Yearly pace waits until this has run for a few more weeks — a few days can look huge or tiny.'
          : 'Yearly pace stretches the same daily average over a year, from these real trades.'}
      </p>
    </button>
  )
}

function TradeSummaries({ rows }: { rows: StrategyTradeSummary[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No finished or open trades in this window.</p>
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Strategy</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Side</TableHead>
            <TableHead className="text-right">Qty</TableHead>
            <TableHead className="text-right">In</TableHead>
            <TableHead className="text-right">Out</TableHead>
            <TableHead className="text-right">P&L</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={`${row.strategy_id}-${row.symbol}-${row.entry_at_ist}-${index}`}>
              <TableCell className="font-medium">{row.strategy_name}</TableCell>
              <TableCell>
                <Badge variant={row.status === 'open' ? 'secondary' : 'outline'}>{row.status}</Badge>
              </TableCell>
              <TableCell>{row.symbol}</TableCell>
              <TableCell>{row.side}</TableCell>
              <TableCell className="text-right">{row.quantity}</TableCell>
              <TableCell className="text-right tabular-nums">{formatInr(row.entry_price)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {row.exit_price == null ? '—' : formatInr(row.exit_price)}
              </TableCell>
              <TableCell className={`text-right tabular-nums ${strategyPnlClass(row.pnl)}`}>
                {formatStrategyPnl(row.pnl)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function SelectedDetail({ performance }: { performance: StrategyBookPerformance }) {
  const trips = performance.round_trips || []
  if (trips.length === 0) {
    return null
  }
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Closed trades for this strategy</CardTitle>
        <CardDescription>One line per finished trade.</CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Symbol</TableHead>
              <TableHead>Side</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">In</TableHead>
              <TableHead className="text-right">Out</TableHead>
              <TableHead className="text-right">P&L</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {trips.map((row, index) => (
              <TableRow key={`${row.symbol}-${row.exit_at_ist}-${index}`}>
                <TableCell className="text-xs text-muted-foreground">{row.exit_at_ist || '—'}</TableCell>
                <TableCell className="font-medium">{row.symbol}</TableCell>
                <TableCell>{row.side}</TableCell>
                <TableCell className="text-right">{row.quantity}</TableCell>
                <TableCell className="text-right">{formatInr(row.entry_price)}</TableCell>
                <TableCell className="text-right">{formatInr(row.exit_price)}</TableCell>
                <TableCell className={`text-right ${strategyPnlClass(row.pnl)}`}>
                  {formatStrategyPnl(row.pnl)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

export function StrategySandboxReport({
  report,
  selectedId,
  onSelect,
}: {
  report: StrategyPerformanceReport | null
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  if (!report) {
    return null
  }

  const rows = allRows(report)
  const active = rows.filter(isActive)
  const idle = rows.filter((row) => !isActive(row) && row.id !== 'sandbox-all')
  const blurbs = new Map((report.narrative?.strategies || []).map((row) => [row.id, row.text]))
  const selected =
    selectedId === 'sandbox-all'
      ? report.overall
      : report.strategies.find((row) => row.id === selectedId)?.performance

  return (
    <div className="space-y-5 mb-8">
      <div className="space-y-3">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold">How each strategy did</h2>
            <p className="text-sm text-muted-foreground max-w-3xl">
              {report.narrative?.overall ||
                'Money made or lost from real finished trades. Yearly pace stretches that same result over a year.'}
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {active.map((row) => (
            <StrategyCard
              key={row.id}
              row={row}
              blurb={row.id === 'sandbox-all' ? undefined : blurbs.get(row.id)}
              selected={selectedId === row.id}
              onSelect={onSelect}
            />
          ))}
        </div>
        {idle.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            Not traded yet:{' '}
            {idle.map((row) => row.name.replace(/^PARKED — /, '')).join(' · ')}
          </p>
        ) : null}
      </div>

      {selected ? <SelectedDetail performance={selected} /> : null}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Trade summaries</CardTitle>
          <CardDescription>One line per trade. The request log below still has every click.</CardDescription>
        </CardHeader>
        <CardContent>
          <TradeSummaries rows={report.trade_summaries || []} />
        </CardContent>
      </Card>
    </div>
  )
}
