import type { StrategyBookPerformance } from '@/api/strategy-books'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatPct, formatRatio, formatStrategyPnl, strategyPnlClass } from '@/lib/strategy-performance'
import { makeFormatCurrency } from '@/lib/utils'

const formatInr = makeFormatCurrency()

export function StrategyPerformancePanel({
  performance,
}: {
  performance: StrategyBookPerformance | undefined
}) {
  const summary = performance?.summary
  const trades = performance?.trades || []
  const trips = performance?.round_trips || []
  const open = performance?.open_positions || []

  return (
    <div className="space-y-4">
      {performance?.source_note && (
        <p className="text-sm text-muted-foreground">{performance.source_note}</p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Realized PnL</CardDescription>
            <CardTitle className={`text-lg ${strategyPnlClass(summary?.realized_pnl)}`}>
              {formatStrategyPnl(summary?.realized_pnl)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Unrealized PnL</CardDescription>
            <CardTitle className={`text-lg ${strategyPnlClass(summary?.unrealized_pnl)}`}>
              {formatStrategyPnl(summary?.unrealized_pnl)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Closed trades</CardDescription>
            <CardTitle className="text-lg">{summary?.closed_round_trips ?? 0}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {summary?.wins ?? 0} won · {summary?.losses ?? 0} lost
            {summary?.win_rate_pct != null ? ` · ${summary.win_rate_pct}% win` : ''}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Fills</CardDescription>
            <CardTitle className="text-lg">{summary?.fills ?? 0}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {summary?.buys ?? 0} buy · {summary?.sells ?? 0} sell
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Decisions</CardDescription>
            <CardTitle className="text-lg">{summary?.enter_decisions ?? 0} entered</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {summary?.hold_decisions ?? 0} hold · {summary?.decline_decisions ?? 0} aside
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>RoI</CardDescription>
            <CardTitle className={`text-lg ${strategyPnlClass(summary?.roi_pct)}`}>
              {formatPct(summary?.roi_pct)}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            On {formatInr(summary?.capital_deployed || 0)} premium deployed
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Yearly pace</CardDescription>
            <CardTitle
              className={`text-lg ${strategyPnlClass(
                Math.abs(summary?.cagr_potential_pct ?? 0) > 500 ? null : summary?.cagr_potential_pct,
              )}`}
            >
              {summary?.cagr_potential_pct != null && Math.abs(summary.cagr_potential_pct) <= 500
                ? formatPct(summary.cagr_potential_pct)
                : '—'}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {summary?.calendar_days
              ? `${summary.calendar_days} calendar · ${summary.trading_days} trading days`
              : 'Need a longer stretch'}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Sharpe</CardDescription>
            <CardTitle className="text-lg">{formatRatio(summary?.sharpe)}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Sortino {formatRatio(summary?.sortino)} · rf 6.5%
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Max drawdown</CardDescription>
            <CardTitle className="text-lg">{formatPct(summary?.max_drawdown_pct)}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Calmar {formatRatio(summary?.calmar)} · PF{' '}
            {summary?.profit_factor == null && (summary?.wins || 0) > 0 && (summary?.losses || 0) === 0
              ? '∞'
              : formatRatio(summary?.profit_factor)}
          </CardContent>
        </Card>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Calendar days</CardDescription>
            <CardTitle className="text-lg">{summary?.calendar_days || '—'}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Trading days</CardDescription>
            <CardTitle className="text-lg">{summary?.trading_days || '—'}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Avg up day</CardDescription>
            <CardTitle className={`text-lg ${strategyPnlClass(summary?.avg_profit_day)}`}>
              {formatStrategyPnl(summary?.avg_profit_day)}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Avg down day</CardDescription>
            <CardTitle className={`text-lg ${strategyPnlClass(summary?.avg_loss_day)}`}>
              {formatStrategyPnl(summary?.avg_loss_day)}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>
      {summary?.sample_note && (
        <p className="text-xs text-muted-foreground">{summary.sample_note}</p>
      )}

      {open.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Open positions</CardTitle>
            <CardDescription>Still open for this strategy.</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Side</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Avg</TableHead>
                  <TableHead className="text-right">LTP</TableHead>
                  <TableHead className="text-right">Unrealized</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {open.map((row) => (
                  <TableRow key={`${row.symbol}-${row.exchange}-${row.product}`}>
                    <TableCell className="font-medium">{row.symbol}</TableCell>
                    <TableCell>{row.side}</TableCell>
                    <TableCell className="text-right">{row.quantity}</TableCell>
                    <TableCell className="text-right">{formatInr(row.average_price)}</TableCell>
                    <TableCell className="text-right">{formatInr(row.ltp)}</TableCell>
                    <TableCell className={`text-right ${strategyPnlClass(row.unrealized_pnl)}`}>
                      {formatStrategyPnl(row.unrealized_pnl)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Closed trades</CardTitle>
          <CardDescription>FIFO round trips tagged to this strategy.</CardDescription>
        </CardHeader>
        <CardContent>
          {trips.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No closed round trips yet. Fills appear here once this strategy tags an OpenAlgo
              order.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Exit</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Side</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Entry</TableHead>
                  <TableHead className="text-right">Exit px</TableHead>
                  <TableHead className="text-right">PnL</TableHead>
                  <TableHead className="text-right">RoI</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trips.map((row, index) => (
                  <TableRow key={`${row.symbol}-${row.exit_at_ist}-${index}`}>
                    <TableCell>{row.exit_at_ist || '—'}</TableCell>
                    <TableCell className="font-medium">{row.symbol}</TableCell>
                    <TableCell>{row.side}</TableCell>
                    <TableCell className="text-right">{row.quantity}</TableCell>
                    <TableCell className="text-right">{formatInr(row.entry_price)}</TableCell>
                    <TableCell className="text-right">{formatInr(row.exit_price)}</TableCell>
                    <TableCell className={`text-right ${strategyPnlClass(row.pnl)}`}>
                      {formatStrategyPnl(row.pnl)}
                    </TableCell>
                    <TableCell className={`text-right ${strategyPnlClass(row.roi_pct)}`}>
                      {formatPct(row.roi_pct)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Past fills</CardTitle>
          <CardDescription>Every OpenAlgo fill tagged to this strategy.</CardDescription>
        </CardHeader>
        <CardContent>
          {trades.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No fills tagged to this strategy yet. New books, webhooks, and Python scripts show up
              here automatically when their placeorder strategy name matches.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Price</TableHead>
                  <TableHead>Tag</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.map((row) => (
                  <TableRow key={row.trade_id}>
                    <TableCell>{row.created_at_ist}</TableCell>
                    <TableCell>
                      <Badge
                        className={
                          row.action === 'BUY' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'
                        }
                      >
                        {row.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium">{row.symbol}</TableCell>
                    <TableCell className="text-right">{row.quantity}</TableCell>
                    <TableCell className="text-right">{formatInr(row.price)}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {row.strategy}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
