import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { type BtcArrest, type BtcDeskData, type BtcLine, btcDeskApi } from '@/api/btc-desk'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

function num(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function clock(unix: number | null): string {
  if (unix == null) return '—'
  return new Date(unix * 1000).toLocaleTimeString()
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-normal text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  )
}

function LineTable({ lines }: { lines: BtcLine[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Kind</TableHead>
          <TableHead>Expiry</TableHead>
          <TableHead>Near</TableHead>
          <TableHead className="text-right">Ask</TableHead>
          <TableHead>Far</TableHead>
          <TableHead className="text-right">Bid</TableHead>
          <TableHead className="text-right">Diff</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.length === 0 ? (
          <TableRow>
            <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
              No anomalies right now.
            </TableCell>
          </TableRow>
        ) : (
          lines.map((line) => (
            <TableRow key={`${line.near}-${line.far}`}>
              <TableCell>{line.kind}</TableCell>
              <TableCell>{line.expiry}</TableCell>
              <TableCell className="font-medium">{line.near}</TableCell>
              <TableCell className="text-right tabular-nums">{num(line.near_ask, 2)}</TableCell>
              <TableCell className="font-medium">{line.far}</TableCell>
              <TableCell className="text-right tabular-nums">{num(line.far_bid, 2)}</TableCell>
              <TableCell className="text-right tabular-nums text-emerald-600">
                {num(line.diff, 2)}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  )
}

function ArrestTable({ rows }: { rows: BtcArrest[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Mode</TableHead>
          <TableHead>Near</TableHead>
          <TableHead>Far</TableHead>
          <TableHead className="text-right">Diff</TableHead>
          <TableHead className="text-right">Qty</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 ? (
          <TableRow>
            <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
              No arrests recorded yet.
            </TableCell>
          </TableRow>
        ) : (
          rows.map((row, index) => (
            <TableRow key={`${row.near}-${row.far}-${row.detected_at}-${index}`}>
              <TableCell>{clock(row.detected_at)}</TableCell>
              <TableCell>
                <Badge variant="outline">{row.mode || '—'}</Badge>
              </TableCell>
              <TableCell>{row.near}</TableCell>
              <TableCell>{row.far}</TableCell>
              <TableCell className="text-right tabular-nums">{num(row.diff, 2)}</TableCell>
              <TableCell className="text-right tabular-nums">{num(row.qty, 0)}</TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  )
}

export default function BtcDesk() {
  const [data, setData] = useState<BtcDeskData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const response = await btcDeskApi.getDesk()
      if (response.status !== 'success' || !response.data) {
        setError(response.message || 'Desk status unavailable')
        return
      }
      setError(null)
      setData(response.data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Desk status unavailable')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => {
      void load()
    }, 2000)
    return () => window.clearInterval(timer)
  }, [load])

  const desk = data?.desk
  const lines = desk?.open_lines ?? []
  const mode = desk?.mode || 'paper'

  return (
    <div className="py-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">BTC Desk</h1>
          <p className="text-muted-foreground mt-1">
            Delta India BTC options. A line is a far bid at least 15 above the nearer ask. Arrests
            are both legs together and stay listed here. Positions and Orderbook are the books for
            the broker signed in on this session.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={data?.alive ? 'default' : 'secondary'}>
            {data?.alive ? 'Live book' : 'Desk quiet'}
          </Badge>
          <Badge variant="outline">{mode}</Badge>
          <Button variant="outline" size="sm" asChild>
            <Link to={data?.positions_href || '/positions'}>Positions</Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to={data?.orderbook_href || '/orderbook'}>Orderbook</Link>
          </Button>
        </div>
      </div>

      {error ? <div className="text-sm text-destructive">{error}</div> : null}
      {data?.db_error ? (
        <div className="text-sm text-destructive">Arrest log: {data.db_error}</div>
      ) : null}

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
        <Stat label="Spot" value={loading && !desk ? '…' : num(desk?.spot, 1)} />
        <Stat label="Expiries" value={num(desk?.expiries, 0)} />
        <Stat label="Contracts" value={num(desk?.contracts, 0)} />
        <Stat label="Fresh quotes" value={num(desk?.fresh, 0)} />
        <Stat label="Quote changes" value={num(desk?.changes, 0)} />
        <Stat label="Lines" value={num(desk?.lines, 0)} />
        <Stat label="Arrests" value={num(desk?.arrests, 0)} />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            Open lines
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              {data?.alive
                ? `updated ${desk?.updated || 'just now'}`
                : data?.age_seconds != null
                  ? `last write ${num(data.age_seconds, 0)}s ago`
                  : 'waiting for the desk'}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <LineTable lines={lines} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Arrests</CardTitle>
        </CardHeader>
        <CardContent>
          <ArrestTable rows={data?.arrests ?? []} />
        </CardContent>
      </Card>
    </div>
  )
}
