import { Fragment, useMemo, useState } from 'react'
import {
  dayCumulativePnl,
  inScenarioLine,
  istTradeDay,
  marginByCurrency,
  marginOf,
  outRuleLine,
  pnlByCurrency,
  runningPnl,
  scenarioGloss,
  type CoaTrade,
} from '@/api/greeks-lab'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Card, CardContent } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'nifty', label: 'Nifty' },
  { id: 'banknifty', label: 'Banknifty' },
  { id: 'sensex', label: 'Sensex' },
  { id: 'crudeoil', label: 'Crude' },
  { id: 'natgas', label: 'Natgas' },
  { id: 'btc-options', label: 'BTC' },
] as const

const COLS = 11

function fmtPrint(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function fmtPct(value: number | null | undefined): string | null {
  if (value == null || Number.isNaN(value) || value === 0) return null
  const sign = value > 0 ? '+' : '−'
  return `${sign}${Math.abs(value).toFixed(1)}%`
}

function fmtMoney(value: number | null | undefined, currency?: string | null): string {
  if (value == null || Number.isNaN(value)) return '—'
  const abs = Math.abs(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  if (currency === 'USD') return `${sign}$${abs}`
  return `${sign}₹${abs}`
}

function fmtCapital(value: number | null | undefined, currency?: string | null): string {
  if (value == null || Number.isNaN(value)) return '—'
  const abs = Math.abs(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })
  return currency === 'USD' ? `$${abs}` : `₹${abs}`
}

function fmtBag(bag: Record<string, number>): string {
  const parts = Object.entries(bag).map(([currency, value]) => fmtMoney(value, currency))
  return parts.length ? parts.join(' · ') : '—'
}

function fmtCapitalBag(bag: Record<string, number>): string {
  const parts = Object.entries(bag).map(([currency, value]) => fmtCapital(value, currency))
  return parts.length ? parts.join(' · ') : '—'
}

function bagClass(bag: Record<string, number>): string {
  const total = Object.values(bag).reduce((sum, value) => sum + value, 0)
  if (!Object.keys(bag).length || total === 0) return 'text-muted-foreground'
  return total > 0 ? 'text-emerald-500' : 'text-red-500'
}

function moneyClass(value: number | null | undefined): string {
  if (value == null || value === 0) return 'text-muted-foreground'
  return value > 0 ? 'text-emerald-500' : 'text-red-500'
}

function lotLabel(row: CoaTrade): string {
  if (row.lots == null && row.lot_size == null) return '—'
  const lots = row.lots ?? 1
  const size = row.lot_size
  if (size == null) return `${lots} lot`
  return `${lots} × ${size.toLocaleString('en-IN')}`
}

function dayStats(rows: CoaTrade[]): {
  fills: number
  bag: Record<string, number>
  margin: Record<string, number>
} {
  return { fills: rows.length, bag: pnlByCurrency(rows), margin: marginByCurrency(rows) }
}

function Hint({ text, tip }: { text: string; tip?: string | null }) {
  const line = (
    <div className="text-[11px] font-normal leading-snug text-muted-foreground">{text}</div>
  )
  if (!tip) return line
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" className="text-left">
          {line}
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs">{tip}</TooltipContent>
    </Tooltip>
  )
}

function PnlStat({
  label,
  hint,
  bag,
  capital = false,
}: {
  label: string
  hint: string
  bag: Record<string, number>
  capital?: boolean
}) {
  return (
    <div className="min-w-[8.5rem] rounded-md border border-border/60 bg-muted/30 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          'text-sm font-semibold tabular-nums',
          capital ? 'text-foreground' : bagClass(bag)
        )}
      >
        {capital ? fmtCapitalBag(bag) : fmtBag(bag)}
      </p>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
    </div>
  )
}

export function CoaTradeLog({ trades }: { trades: CoaTrade[] }) {
  const [filter, setFilter] = useState<string>('all')
  const today = istTradeDay()
  const rows = useMemo(
    () => (filter === 'all' ? trades : trades.filter((row) => row.board === filter)),
    [filter, trades]
  )
  const groups = useMemo(() => {
    const byDay = new Map<string, CoaTrade[]>()
    for (const row of rows) {
      const day = row.day || 'Undated'
      const list = byDay.get(day) || []
      list.push(row)
      byDay.set(day, list)
    }
    return [...byDay.entries()]
  }, [rows])
  const daily = useMemo(
    () => pnlByCurrency(rows.filter((row) => row.day === today)),
    [rows, today]
  )
  const cumulative = useMemo(() => pnlByCurrency(rows), [rows])
  const runById = useMemo(() => runningPnl(rows), [rows])
  const dayCum = useMemo(() => dayCumulativePnl(groups), [groups])
  const todayFills = rows.filter((row) => row.day === today).length
  const todayMargin = useMemo(
    () => marginByCurrency(rows.filter((row) => row.day === today)),
    [rows, today]
  )
  const allMargin = useMemo(() => marginByCurrency(rows), [rows])

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">CoA trades</h2>
            <p className="text-xs text-muted-foreground">
              Hands-confirmed fills. CoA 1.1–1.9 is the floor/ceiling map at that tick. Margin is
              ATM premium blocked at entry. High ratchets from the next fill onward. P&amp;L is the
              ATM leg only.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <PnlStat
              label="Daily"
              hint={`${today} · ${todayFills} fill${todayFills === 1 ? '' : 's'}`}
              bag={daily}
            />
            <PnlStat
              label="Cumulative"
              hint={`${rows.length} fill${rows.length === 1 ? '' : 's'} · all days`}
              bag={cumulative}
            />
            <PnlStat
              label="Margin"
              hint={
                todayFills
                  ? `${today} · ATM premium`
                  : `${fmtCapitalBag(allMargin)} all days`
              }
              bag={todayFills ? todayMargin : allMargin}
              capital
            />
            <div className="flex flex-wrap gap-1">
              {FILTERS.map((item) => (
                <Button
                  key={item.id}
                  size="sm"
                  variant={filter === item.id ? 'default' : 'outline'}
                  className="h-7 px-2 text-xs"
                  onClick={() => setFilter(item.id)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          </div>
        </div>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">
            No Hands-confirmed CoA fill logged yet. The next tag appends here and stays.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>IST</TableHead>
                  <TableHead>Board / strike</TableHead>
                  <TableHead>Lot</TableHead>
                  <TableHead className="text-right">Margin</TableHead>
                  <TableHead>In</TableHead>
                  <TableHead>Out</TableHead>
                  <TableHead>Hold</TableHead>
                  <TableHead>Rule that fired</TableHead>
                  <TableHead className="text-right">High</TableHead>
                  <TableHead className="text-right">ATM print</TableHead>
                  <TableHead className="text-right">P&amp;L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups.map(([day, dayRows]) => {
                  const stats = dayStats(dayRows)
                  const cum = dayCum.get(day) || {}
                  return (
                    <Fragment key={day}>
                      <TableRow className="hover:bg-transparent">
                        <TableCell
                          colSpan={COLS}
                          className="bg-muted/40 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
                        >
                          <span>{day}</span>
                          <span className="ml-2 font-normal normal-case tracking-normal">
                            {stats.fills} fill{stats.fills === 1 ? '' : 's'}
                            {Object.keys(stats.bag).length
                              ? ` · day ${fmtBag(stats.bag)} · cum ${fmtBag(cum)}`
                              : ''}
                            {Object.keys(stats.margin).length
                              ? ` · margin ${fmtCapitalBag(stats.margin)}`
                              : ''}
                          </span>
                        </TableCell>
                      </TableRow>
                      {dayRows.map((row) => (
                        <TableRow key={row.id} className={cn(row.open && 'bg-primary/5')}>
                          <TableCell className="tabular-nums font-medium whitespace-nowrap">
                            {row.ist_span || `${row.in_ist || '—'}–${row.out_ist || 'open'}`}
                          </TableCell>
                          <TableCell className="whitespace-nowrap">
                            <div>{row.title}</div>
                            <div className="text-[11px] text-muted-foreground tabular-nums">
                              {row.strike_label ||
                                (row.strike
                                  ? `${row.strike} ${row.side}`
                                  : row.side || '—')}
                            </div>
                          </TableCell>
                          <TableCell className="tabular-nums whitespace-nowrap">
                            <div>{lotLabel(row)}</div>
                            {row.qty != null && row.lot_size != null && (
                              <div className="text-[11px] text-muted-foreground">
                                {row.qty.toLocaleString('en-IN')} units
                              </div>
                            )}
                          </TableCell>
                          <TableCell className="text-right tabular-nums whitespace-nowrap">
                            {fmtCapital(marginOf(row), row.currency)}
                          </TableCell>
                          <TableCell
                            className={cn(
                              'whitespace-normal min-w-[11rem]',
                              row.side === 'PE' ? 'text-red-500' : 'text-emerald-500'
                            )}
                          >
                            <div>{row.in_label}</div>
                            {inScenarioLine(row) && (
                              <Hint
                                text={inScenarioLine(row) || ''}
                                tip={scenarioGloss(row.scenario || row.in_label)?.tip}
                              />
                            )}
                          </TableCell>
                          <TableCell className={row.open ? 'text-muted-foreground' : ''}>
                            {row.out_label}
                          </TableCell>
                          <TableCell className="tabular-nums">{row.hold_label}</TableCell>
                          <TableCell className="whitespace-normal min-w-[13rem]">
                            <div>
                              {row.rule || (row.open ? 'Still in — waiting on a house exit.' : '—')}
                            </div>
                            {!row.open && outRuleLine(row) && (
                              <Hint
                                text={outRuleLine(row) || ''}
                                tip={
                                  scenarioGloss(row.exit_scenario || row.out_label || row.rule)?.tip
                                }
                              />
                            )}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            <div>{fmtPrint(row.peak_ltp)}</div>
                            {fmtPct(row.peak_pct) && (
                              <div className={cn('text-[11px]', moneyClass(row.peak_pct))}>
                                {fmtPct(row.peak_pct)}
                                {row.mfe != null ? ` · MFE ${fmtMoney(row.mfe, row.currency)}` : ''}
                              </div>
                            )}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-muted-foreground">
                            {fmtPrint(row.entry_ltp)}
                            {row.exit_ltp != null
                              ? ` → ${fmtPrint(row.exit_ltp)}`
                              : row.mark_ltp != null && row.open
                                ? ` → ${fmtPrint(row.mark_ltp)}`
                                : ''}
                          </TableCell>
                          <TableCell
                            className={cn(
                              'text-right tabular-nums font-medium whitespace-nowrap',
                              moneyClass(row.atm_pnl)
                            )}
                          >
                            <div>{fmtMoney(row.atm_pnl, row.currency)}</div>
                            {runById.has(row.id) && (
                              <div className="text-[11px] font-normal text-muted-foreground">
                                cum {fmtMoney(runById.get(row.id), row.currency)}
                              </div>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </Fragment>
                  )
                })}
                <TableRow className="hover:bg-transparent">
                  <TableCell
                    colSpan={10}
                    className="text-right text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
                  >
                    Daily {today} / Cumulative
                  </TableCell>
                  <TableCell className="text-right tabular-nums whitespace-nowrap">
                    <div className={cn('font-semibold', bagClass(daily))}>{fmtBag(daily)}</div>
                    <div className={cn('text-[11px] font-semibold', bagClass(cumulative))}>
                      cum {fmtBag(cumulative)}
                    </div>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
