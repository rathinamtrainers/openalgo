import type { StrategyBookPerformance } from '@/api/strategy-books'
import { makeFormatCurrency } from '@/lib/utils'

const formatInr = makeFormatCurrency()

export function formatStrategyPnl(value: number | null | undefined, empty = '—') {
  if (value == null) return empty
  const formatted = formatInr(Math.abs(value))
  if (value > 0) return `+${formatted}`
  if (value < 0) return `-${formatted}`
  return formatted
}

export function strategyPnlClass(value: number | null | undefined) {
  if (value == null || value === 0) return 'text-muted-foreground'
  return value > 0 ? 'text-emerald-500' : 'text-red-500'
}

export function formatRatio(value: number | null | undefined, digits = 2, empty = '—') {
  if (value == null || Number.isNaN(value)) return empty
  return value.toFixed(digits)
}

export function formatPct(value: number | null | undefined, empty = '—') {
  if (value == null || Number.isNaN(value)) return empty
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export function strategyPerformanceLine(performance?: StrategyBookPerformance | null) {
  const summary = performance?.summary
  if (!summary) return 'Performance · past trades'
  const signed = formatStrategyPnl(summary.total_pnl)
  if (summary.roi_pct != null) {
    return `${signed} · ${formatPct(summary.roi_pct)} RoI · ${summary.closed_round_trips} closed`
  }
  return `${signed} · ${summary.fills} fills · ${summary.closed_round_trips} closed`
}
