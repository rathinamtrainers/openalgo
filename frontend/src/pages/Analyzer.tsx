import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle,
  Download,
  Eye,
  Filter,
  Percent,
  TrendingUp,
  Users,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { JsonEditor } from '@/components/ui/json-editor'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn, makeFormatCurrency } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'

interface RoundTrip {
  symbol?: string | null
  exchange?: string | null
  product?: string | null
  side?: string | null
  underlying?: string | null
  expiry?: string | null
  strike?: string | null
  option_type?: string | null
  entry_time?: string | null
  exit_time?: string | null
  entry_price?: number | null
  exit_price?: number | null
  traded_quantity?: number | null
  trade_pnl?: number | null
  roi_percent?: number | null
  cum_pnl?: number | null
  cum_roi_percent?: number | null
  open?: boolean
}

interface ApiRequest extends RoundTrip {
  timestamp: string
  api_type: string
  source: string
  quantity?: number
  position_size?: number
  orderid?: string
  action?: string
  fill_price?: number | null
  request_data: Record<string, unknown>
  response_data?: Record<string, unknown>
  analysis: {
    issues?: boolean | string[]
    error?: string
    error_type?: string
    warnings?: string[]
  }
}

interface Stats {
  total_requests: number
  issues: {
    total: number
  }
  symbols: string[]
  sources: string[]
}

interface TradeStats {
  trades: number
  open_trades: number
  net_pnl: number
  roi_percent: number | null
  total_premium: number
  winning: number
  losing: number
  even: number
  win_rate: number | null
  gross_profit: number
  gross_loss: number
  avg_win: number | null
  avg_loss: number | null
  avg_pnl: number | null
  avg_roi_percent: number | null
  profit_factor: number | null
  profit_factor_infinite: boolean
  expectancy: number | null
  best_trade: number | null
  worst_trade: number | null
  max_consecutive_wins: number
  max_consecutive_losses: number
  max_drawdown: number
  max_drawdown_pct: number
  pe_trades: number
  pe_pnl: number
  ce_trades: number
  ce_pnl: number
}

interface AnalyzerData {
  requests: ApiRequest[]
  trades?: RoundTrip[]
  trade_stats?: TradeStats
  stats: Stats
}

const EXCHANGE_COLORS: Record<string, string> = {
  NSE: 'bg-cyan-500/10 text-cyan-600 border-cyan-500/30',
  NFO: 'bg-purple-500/10 text-purple-600 border-purple-500/30',
  CDS: 'bg-blue-500/10 text-blue-600 border-blue-500/30',
  BSE: 'bg-gray-500/10 text-gray-600 border-gray-500/30',
  BFO: 'bg-yellow-500/10 text-yellow-600 border-yellow-500/30',
  BCD: 'bg-red-500/10 text-red-600 border-red-500/30',
  MCX: 'bg-primary/10 text-primary border-primary/30',
  NCDEX: 'bg-green-500/10 text-green-600 border-green-500/30',
  NCO: 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30',
  NSE_INDEX: 'bg-cyan-500/10 text-cyan-600 border-cyan-500/30',
  BSE_INDEX: 'bg-gray-500/10 text-gray-600 border-gray-500/30',
  GLOBAL_INDEX: 'bg-indigo-500/10 text-indigo-600 border-indigo-500/30',
}

function formatTradeClock(ts?: string | null): string {
  if (!ts) return '—'
  const match = ts.match(/(\d{2}:\d{2}:\d{2})/)
  return match ? match[1] : ts
}

function strikeLabel(item: RoundTrip): string | null {
  if (item.strike && item.option_type) return `${item.strike} ${item.option_type}`
  if (item.option_type) return item.option_type
  return null
}

function isProfit(value: number | null | undefined): boolean {
  return (value ?? 0) >= 0
}

function summarizeTrades(trips: RoundTrip[]): TradeStats {
  const closed = trips
    .filter((trip) => !trip.open && trip.trade_pnl != null)
    .slice()
    .sort((a, b) => (a.exit_time || a.entry_time || '').localeCompare(b.exit_time || b.entry_time || ''))
  const empty: TradeStats = {
    trades: 0,
    open_trades: trips.filter((trip) => trip.open).length,
    net_pnl: 0,
    roi_percent: null,
    total_premium: 0,
    winning: 0,
    losing: 0,
    even: 0,
    win_rate: null,
    gross_profit: 0,
    gross_loss: 0,
    avg_win: null,
    avg_loss: null,
    avg_pnl: null,
    avg_roi_percent: null,
    profit_factor: null,
    profit_factor_infinite: false,
    expectancy: null,
    best_trade: null,
    worst_trade: null,
    max_consecutive_wins: 0,
    max_consecutive_losses: 0,
    max_drawdown: 0,
    max_drawdown_pct: 0,
    pe_trades: 0,
    pe_pnl: 0,
    ce_trades: 0,
    ce_pnl: 0,
  }
  if (!closed.length) return empty

  const pnls = closed.map((trip) => Number(trip.trade_pnl))
  const premiums = closed.map((trip) => Number(trip.traded_quantity || 0) * Number(trip.entry_price || 0))
  const net = Number(pnls.reduce((sum, pnl) => sum + pnl, 0).toFixed(2))
  const premium = Number(premiums.reduce((sum, value) => sum + value, 0).toFixed(2))
  const wins = pnls.filter((pnl) => pnl > 0)
  const losses = pnls.filter((pnl) => pnl < 0)
  const evens = pnls.filter((pnl) => pnl === 0)
  const grossProfit = Number(wins.reduce((sum, pnl) => sum + pnl, 0).toFixed(2))
  const grossLoss = Number(Math.abs(losses.reduce((sum, pnl) => sum + pnl, 0)).toFixed(2))
  let equity = 0
  let peak = 0
  let maxDd = 0
  for (const pnl of pnls) {
    equity += pnl
    if (equity > peak) peak = equity
    const drawdown = peak - equity
    if (drawdown > maxDd) maxDd = drawdown
  }
  let maxCw = 0
  let maxCl = 0
  let streakW = 0
  let streakL = 0
  for (const pnl of pnls) {
    if (pnl > 0) {
      streakW += 1
      streakL = 0
    } else if (pnl < 0) {
      streakL += 1
      streakW = 0
    } else {
      streakW = 0
      streakL = 0
    }
    maxCw = Math.max(maxCw, streakW)
    maxCl = Math.max(maxCl, streakL)
  }
  const pe = closed.filter((trip) => (trip.option_type || '').toUpperCase() === 'PE')
  const ce = closed.filter((trip) => (trip.option_type || '').toUpperCase() === 'CE')
  const avgPnl = Number((net / closed.length).toFixed(2))
  const pfInfinite = wins.length > 0 && losses.length === 0
  return {
    ...empty,
    trades: closed.length,
    net_pnl: net,
    roi_percent: premium ? Number(((net / premium) * 100).toFixed(2)) : null,
    total_premium: premium,
    winning: wins.length,
    losing: losses.length,
    even: evens.length,
    win_rate: Number(((wins.length / closed.length) * 100).toFixed(2)),
    gross_profit: grossProfit,
    gross_loss: grossLoss,
    avg_win: wins.length ? Number((grossProfit / wins.length).toFixed(2)) : null,
    avg_loss: losses.length ? Number((losses.reduce((sum, pnl) => sum + pnl, 0) / losses.length).toFixed(2)) : null,
    avg_pnl: avgPnl,
    avg_roi_percent: premium ? Number(((net / premium) * 100).toFixed(2)) : null,
    profit_factor: pfInfinite ? null : grossLoss ? Number((grossProfit / grossLoss).toFixed(2)) : null,
    profit_factor_infinite: pfInfinite,
    expectancy: avgPnl,
    best_trade: Number(Math.max(...pnls).toFixed(2)),
    worst_trade: Number(Math.min(...pnls).toFixed(2)),
    max_consecutive_wins: maxCw,
    max_consecutive_losses: maxCl,
    max_drawdown: Number(maxDd.toFixed(2)),
    max_drawdown_pct: premium ? Number(((maxDd / premium) * 100).toFixed(2)) : 0,
    pe_trades: pe.length,
    pe_pnl: Number(pe.reduce((sum, trip) => sum + Number(trip.trade_pnl), 0).toFixed(2)),
    ce_trades: ce.length,
    ce_pnl: Number(ce.reduce((sum, trip) => sum + Number(trip.trade_pnl), 0).toFixed(2)),
  }
}

function formatSignedMoney(
  formatCurrency: (value: number) => string,
  value: number | null | undefined,
): string {
  if (value == null) return '—'
  const formatted = formatCurrency(Math.abs(value))
  if (value > 0) return `+${formatted}`
  if (value < 0) return `-${formatted}`
  return formatted
}

function formatPct(value: number | null | undefined, signed = true): string {
  if (value == null) return '—'
  const sign = signed && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function pnlTone(value: number | null | undefined): string {
  if (value == null || value === 0) return ''
  return value > 0 ? 'text-green-500' : 'text-red-500'
}

function withRunningTotals(trips: RoundTrip[]): RoundTrip[] {
  const closed = trips
    .map((trip, index) => ({ trip, index }))
    .filter(({ trip }) => !trip.open && trip.trade_pnl != null)
    .sort((a, b) =>
      (a.trip.exit_time || a.trip.entry_time || '').localeCompare(
        b.trip.exit_time || b.trip.entry_time || '',
      ),
    )
  const running = new Map<number, { cum_pnl: number; cum_roi_percent: number | null }>()
  let cumPnl = 0
  let cumPremium = 0
  for (const { trip, index } of closed) {
    cumPnl = Number((cumPnl + Number(trip.trade_pnl)).toFixed(2))
    cumPremium += Number(trip.traded_quantity || 0) * Number(trip.entry_price || 0)
    running.set(index, {
      cum_pnl: cumPnl,
      cum_roi_percent: cumPremium ? Number(((cumPnl / cumPremium) * 100).toFixed(2)) : null,
    })
  }
  return trips.map((trip, index) => {
    const stamped = running.get(index)
    if (!stamped) return { ...trip, cum_pnl: trip.cum_pnl ?? null, cum_roi_percent: trip.cum_roi_percent ?? null }
    return { ...trip, ...stamped }
  })
}

export default function Analyzer() {
  const { user } = useAuthStore()
  const formatCurrency = useMemo(() => makeFormatCurrency(user?.broker), [user?.broker])
  const [data, setData] = useState<AnalyzerData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [selectedRequest, setSelectedRequest] = useState<ApiRequest | null>(null)
  const [showDetailsDialog, setShowDetailsDialog] = useState(false)

  // biome-ignore lint/correctness/useExhaustiveDependencies: one-time data load on mount; subsequent fetches are triggered explicitly by the filter form
  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async (start?: string, end?: string) => {
    setIsLoading(true)
    try {
      const params = new URLSearchParams()
      if (start) params.append('start_date', start)
      if (end) params.append('end_date', end)

      const url = params.toString() ? `/analyzer/api/data?${params}` : '/analyzer/api/data'

      const response = await fetch(url, {
        credentials: 'include',
      })

      if (response.ok) {
        const result = await response.json()
        if (result.status === 'success') {
          setData(result.data)
        }
      }
    } catch (_error) {
    } finally {
      setIsLoading(false)
    }
  }

  const handleFilter = (e: React.FormEvent) => {
    e.preventDefault()
    fetchData(startDate, endDate)
  }

  const handleExport = () => {
    const params = new URLSearchParams()
    if (startDate) params.append('start_date', startDate)
    if (endDate) params.append('end_date', endDate)

    const url = params.toString() ? `/analyzer/export?${params}` : '/analyzer/export'
    window.location.href = url
  }

  const viewDetails = (request: ApiRequest) => {
    setSelectedRequest(request)
    setShowDetailsDialog(true)
  }

  const getRequestDetails = (request: ApiRequest): string => {
    if (request.api_type === 'cancelorder') {
      return `OrderID: ${request.orderid}`
    }

    let details = request.symbol || ''
    const qty = request.traded_quantity || request.quantity
    if (qty) {
      details += ` (${qty})`
    }
    if (request.api_type === 'placesmartorder' && request.position_size) {
      details += ` [Size: ${request.position_size}]`
    }
    return details
  }

  // Clean request data for display (remove apikey)
  const cleanRequestData = (data: Record<string, unknown>): Record<string, unknown> => {
    const cleaned = { ...data }
    delete cleaned.apikey
    return cleaned
  }

  if (isLoading) {
    return (
      <div className="container mx-auto py-8 px-4 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  const stats = data?.stats || { total_requests: 0, issues: { total: 0 }, symbols: [], sources: [] }
  const requests = data?.requests || []
  const trades = withRunningTotals(data?.trades || [])
  const tradeStats = data?.trade_stats || summarizeTrades(trades)

  const formatMoney = (value: number | null | undefined) => formatSignedMoney(formatCurrency, value)
  const profitFactorLabel = tradeStats.profit_factor_infinite
    ? '∞'
    : tradeStats.profit_factor == null
      ? '—'
      : tradeStats.profit_factor.toFixed(2)
  const detailStats: { label: string; value: string; tone?: string }[] = [
    { label: 'Winning', value: String(tradeStats.winning) },
    { label: 'Losing', value: String(tradeStats.losing) },
    { label: 'Even', value: String(tradeStats.even) },
    { label: 'Gross profit', value: formatMoney(tradeStats.gross_profit), tone: pnlTone(tradeStats.gross_profit) },
    { label: 'Gross loss', value: formatMoney(-tradeStats.gross_loss), tone: tradeStats.gross_loss ? 'text-red-500' : '' },
    { label: 'Average win', value: formatMoney(tradeStats.avg_win), tone: pnlTone(tradeStats.avg_win) },
    { label: 'Average loss', value: formatMoney(tradeStats.avg_loss), tone: pnlTone(tradeStats.avg_loss) },
    { label: 'Expectancy', value: formatMoney(tradeStats.expectancy), tone: pnlTone(tradeStats.expectancy) },
    { label: 'Profit factor', value: profitFactorLabel },
    { label: 'Best trade', value: formatMoney(tradeStats.best_trade), tone: pnlTone(tradeStats.best_trade) },
    { label: 'Worst trade', value: formatMoney(tradeStats.worst_trade), tone: pnlTone(tradeStats.worst_trade) },
    { label: 'Max consecutive wins', value: String(tradeStats.max_consecutive_wins) },
    { label: 'Max consecutive losses', value: String(tradeStats.max_consecutive_losses) },
    { label: 'Max drawdown', value: formatMoney(-tradeStats.max_drawdown), tone: tradeStats.max_drawdown ? 'text-red-500' : '' },
    { label: 'Max drawdown %', value: formatPct(tradeStats.max_drawdown ? -tradeStats.max_drawdown_pct : 0), tone: tradeStats.max_drawdown ? 'text-red-500' : '' },
    { label: 'Premium deployed', value: formatCurrency(tradeStats.total_premium) },
    { label: 'Open trades', value: String(tradeStats.open_trades) },
    {
      label: 'PE P&L',
      value: `${tradeStats.pe_trades} · ${formatMoney(tradeStats.pe_pnl)}`,
      tone: pnlTone(tradeStats.pe_pnl),
    },
    {
      label: 'CE P&L',
      value: `${tradeStats.ce_trades} · ${formatMoney(tradeStats.ce_pnl)}`,
      tone: pnlTone(tradeStats.ce_pnl),
    },
  ]

  return (
    <div className="container mx-auto py-6 px-4">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold">Sandbox Request Monitor</h1>
        <p className="text-muted-foreground mt-1">
          Cumulative P&L and RoI, Tradetron-style trade stats, then the filled blotter and API log
        </p>
      </div>

      {/* Date Filter */}
      <Card className="mb-6">
        <CardHeader className="pb-3">
          <CardTitle className="text-lg flex items-center gap-2">
            <Filter className="h-4 w-4" />
            Filters
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleFilter} className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <div className="space-y-2">
              <Label htmlFor="start_date" className="text-sm font-medium">
                Start Date
              </Label>
              <Input
                id="start_date"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="date-input-styled"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="end_date" className="text-sm font-medium">
                End Date
              </Label>
              <Input
                id="end_date"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="date-input-styled"
              />
            </div>
            <div className="flex gap-2 pt-6">
              <Button type="submit">
                <Filter className="h-4 w-4 mr-2" />
                Filter
              </Button>
              <Button type="button" variant="secondary" onClick={handleExport}>
                <Download className="h-4 w-4 mr-2" />
                Export CSV
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">Trade performance</CardTitle>
          <p className="text-sm text-muted-foreground">
            Closed sandbox round-trips in this date range. Trade % RoI is that round-trip only.
            Cumulative / Cum RoI is net P&L divided by total premium paid — size-weighted, so it
            will not equal the average of the per-row percentages.
          </p>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <div className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <TrendingUp className="h-4 w-4" />
                Cumulative P&L
              </div>
              <div className={cn('text-3xl font-bold mt-1', pnlTone(tradeStats.net_pnl))}>
                {formatMoney(tradeStats.net_pnl)}
              </div>
            </div>
            <div>
              <div className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <Percent className="h-4 w-4" />
                Cumulative RoI
              </div>
              <div className={cn('text-3xl font-bold mt-1', pnlTone(tradeStats.roi_percent))}>
                {formatPct(tradeStats.roi_percent)}
              </div>
            </div>
            <div>
              <div className="text-sm font-medium text-muted-foreground">Win rate</div>
              <div className="text-3xl font-bold mt-1">{formatPct(tradeStats.win_rate, false)}</div>
              <p className="text-xs text-muted-foreground mt-1">
                {tradeStats.winning}W / {tradeStats.losing}L
                {tradeStats.even ? ` / ${tradeStats.even}E` : ''}
              </p>
            </div>
            <div>
              <div className="text-sm font-medium text-muted-foreground">Closed trades</div>
              <div className="text-3xl font-bold mt-1">{tradeStats.trades}</div>
              <p className="text-xs text-muted-foreground mt-1">
                {tradeStats.open_trades} still open
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-5 gap-x-4 gap-y-3 text-sm">
            {detailStats.map((item) => (
              <div key={item.label}>
                <div className="text-muted-foreground">{item.label}</div>
                <div className={cn('font-medium', item.tone)}>{item.value}</div>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Max drawdown % is the worst peak-to-trough against premium paid. Sharpe, Sortino,
            recovery factor, MAE and MFE are not shown — those need daily equity and bar-level
            excursion data, which this blotter does not have.
          </p>
        </CardContent>
      </Card>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Activity className="h-4 w-4" />
              Total Requests
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-primary">{stats.total_requests}</div>
            <Badge className="mt-1">Last 24 hours</Badge>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" />
              Issues Found
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-yellow-500">{stats.issues.total}</div>
            <Badge variant="secondary" className="mt-1">
              Needs Attention
            </Badge>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <BarChart3 className="h-4 w-4" />
              Unique Symbols
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{stats.symbols.length}</div>
            <Badge variant="outline" className="mt-1">
              Tracked
            </Badge>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Users className="h-4 w-4" />
              Active Sources
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">{stats.sources.length}</div>
            <Badge variant="outline" className="mt-1">
              Connected
            </Badge>
          </CardContent>
        </Card>
      </div>

      {/* Round-trip blotter */}
      <Card className="mb-8">
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">Trades</CardTitle>
          <p className="text-sm text-muted-foreground">
            One row per filled round-trip. Cum P&L / Cum RoI run oldest → newest; the newest row
            matches the headline cumulative figures.
          </p>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Strike</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead>Entry</TableHead>
                  <TableHead className="text-right">Entry LTP</TableHead>
                  <TableHead>Exit</TableHead>
                  <TableHead className="text-right">Exit LTP</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead className="text-right">% RoI</TableHead>
                  <TableHead className="text-right">Cum P&L</TableHead>
                  <TableHead className="text-right">Cum RoI</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={11} className="text-center py-8 text-muted-foreground">
                      No filled trades in this date range
                    </TableCell>
                  </TableRow>
                ) : (
                  trades.map((trade, index) => {
                    const strike = strikeLabel(trade)
                    const pnl = trade.trade_pnl
                    const roi = trade.roi_percent
                    const cumPnl = trade.cum_pnl
                    const cumRoi = trade.cum_roi_percent
                    return (
                      <TableRow key={`${trade.symbol}-${trade.entry_time}-${index}`}>
                        <TableCell className="font-medium">
                          <div>{trade.symbol || '—'}</div>
                          {trade.exchange ? (
                            <Badge
                              className={cn('mt-1', EXCHANGE_COLORS[trade.exchange] || '')}
                              variant="outline"
                            >
                              {trade.exchange}
                            </Badge>
                          ) : null}
                        </TableCell>
                        <TableCell>{strike || '—'}</TableCell>
                        <TableCell className="text-right">
                          {trade.traded_quantity ?? '—'}
                        </TableCell>
                        <TableCell
                          className="font-mono text-xs"
                          title={trade.entry_time || undefined}
                        >
                          {formatTradeClock(trade.entry_time)}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {trade.entry_price != null ? formatCurrency(trade.entry_price) : '—'}
                        </TableCell>
                        <TableCell
                          className="font-mono text-xs"
                          title={trade.exit_time || undefined}
                        >
                          {trade.open ? 'Open' : formatTradeClock(trade.exit_time)}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {trade.exit_price != null ? formatCurrency(trade.exit_price) : '—'}
                        </TableCell>
                        <TableCell
                          className={cn(
                            'text-right font-medium',
                            pnl == null
                              ? 'text-muted-foreground'
                              : isProfit(pnl)
                                ? 'text-green-600'
                                : 'text-red-600'
                          )}
                        >
                          {pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}`}
                        </TableCell>
                        <TableCell
                          className={cn(
                            'text-right',
                            roi == null
                              ? 'text-muted-foreground'
                              : isProfit(roi)
                                ? 'text-green-600'
                                : 'text-red-600'
                          )}
                        >
                          {roi == null ? '—' : `${roi >= 0 ? '+' : ''}${roi.toFixed(2)}%`}
                        </TableCell>
                        <TableCell
                          className={cn(
                            'text-right font-medium',
                            cumPnl == null
                              ? 'text-muted-foreground'
                              : isProfit(cumPnl)
                                ? 'text-green-600'
                                : 'text-red-600'
                          )}
                        >
                          {cumPnl == null ? '—' : `${cumPnl >= 0 ? '+' : ''}${formatCurrency(cumPnl)}`}
                        </TableCell>
                        <TableCell
                          className={cn(
                            'text-right',
                            cumRoi == null
                              ? 'text-muted-foreground'
                              : isProfit(cumRoi)
                                ? 'text-green-600'
                                : 'text-red-600'
                          )}
                        >
                          {cumRoi == null ? '—' : `${cumRoi >= 0 ? '+' : ''}${cumRoi.toFixed(2)}%`}
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Requests Table */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Timestamp</TableHead>
                  <TableHead>API Type</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Details</TableHead>
                  <TableHead>Strike</TableHead>
                  <TableHead className="text-right">Entry LTP</TableHead>
                  <TableHead className="text-right">Exit LTP</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead className="text-right">% RoI</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>View</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={13} className="text-center py-8 text-muted-foreground">
                      No requests found
                    </TableCell>
                  </TableRow>
                ) : (
                  requests.map((request, index) => (
                    <TableRow key={index} className="hover:bg-muted/50">
                      <TableCell className="text-sm">{request.timestamp}</TableCell>
                      <TableCell>
                        <Badge variant="default">{request.api_type}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="truncate max-w-[120px]">
                          {request.source}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-medium">
                        <div>{getRequestDetails(request)}</div>
                        {request.entry_time ? (
                          <div className="text-xs text-muted-foreground mt-1">
                            In {formatTradeClock(request.entry_time)}
                            {request.exit_time ? ` → Out ${formatTradeClock(request.exit_time)}` : ''}
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell>{strikeLabel(request) || '—'}</TableCell>
                      <TableCell className="text-right font-mono">
                        {request.entry_price != null
                          ? formatCurrency(request.entry_price)
                          : request.fill_price != null
                            ? formatCurrency(request.fill_price)
                            : '—'}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {request.exit_price != null ? formatCurrency(request.exit_price) : '—'}
                      </TableCell>
                      <TableCell
                        className={cn(
                          'text-right font-medium',
                          request.trade_pnl == null
                            ? 'text-muted-foreground'
                            : isProfit(request.trade_pnl)
                              ? 'text-green-600'
                              : 'text-red-600'
                        )}
                      >
                        {request.trade_pnl == null
                          ? '—'
                          : `${request.trade_pnl >= 0 ? '+' : ''}${formatCurrency(request.trade_pnl)}`}
                      </TableCell>
                      <TableCell
                        className={cn(
                          'text-right',
                          request.roi_percent == null
                            ? 'text-muted-foreground'
                            : isProfit(request.roi_percent)
                              ? 'text-green-600'
                              : 'text-red-600'
                        )}
                      >
                        {request.roi_percent == null
                          ? '—'
                          : `${request.roi_percent >= 0 ? '+' : ''}${request.roi_percent.toFixed(2)}%`}
                      </TableCell>
                      <TableCell>
                        {request.exchange && (
                          <Badge
                            className={EXCHANGE_COLORS[request.exchange] || ''}
                            variant="outline"
                          >
                            {request.exchange}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        {request.action && (
                          <Badge variant={request.action === 'BUY' ? 'default' : 'destructive'}>
                            {request.action}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        {request.analysis.issues ? (
                          <Badge
                            variant="secondary"
                            className="bg-yellow-500/10 text-yellow-600 border-yellow-500/30"
                          >
                            <AlertTriangle className="h-3 w-3 mr-1" />
                            Error
                          </Badge>
                        ) : (
                          <Badge
                            variant="secondary"
                            className="bg-green-500/10 text-green-600 border-green-500/30"
                          >
                            <CheckCircle className="h-3 w-3 mr-1" />
                            Success
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <Button size="sm" onClick={() => viewDetails(request)}>
                          <Eye className="h-4 w-4 mr-1" />
                          View
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Request Details Dialog */}
      <Dialog open={showDetailsDialog} onOpenChange={setShowDetailsDialog}>
        <DialogContent className="max-w-[98vw] w-[98vw] sm:max-w-[98vw] p-4">
          <DialogHeader>
            <DialogTitle>Request Details</DialogTitle>
          </DialogHeader>
          {selectedRequest &&
            (() => {
              const requestJson = JSON.stringify(
                cleanRequestData(selectedRequest.request_data),
                null,
                2
              )
              const responseJson = JSON.stringify(
                selectedRequest.response_data || selectedRequest.analysis,
                null,
                2
              )
              const requestLines = requestJson.split('\n').length
              const responseLines = responseJson.split('\n').length
              const maxLines = Math.max(requestLines, responseLines)
              // Allow up to 70vh height
              const height = Math.min(Math.max(maxLines * 20 + 24, 200), window.innerHeight * 0.7)

              return (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 max-h-[85vh] overflow-y-auto">
                  <div className="min-w-0 overflow-hidden">
                    <h4 className="font-semibold mb-2">Request Data</h4>
                    <div className="rounded-lg border bg-card/50 overflow-auto" style={{ height }}>
                      <JsonEditor value={requestJson} readOnly={true} lineWrapping={false} />
                    </div>
                  </div>
                  <div className="min-w-0 overflow-hidden">
                    <h4 className="font-semibold mb-2">Response Data</h4>
                    <div className="rounded-lg border bg-card/50 overflow-auto" style={{ height }}>
                      <JsonEditor value={responseJson} readOnly={true} lineWrapping={false} />
                    </div>
                  </div>
                </div>
              )
            })()}
        </DialogContent>
      </Dialog>
    </div>
  )
}
