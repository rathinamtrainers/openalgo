import { Check, ChevronsUpDown } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type CoaBoard,
  type DeskInsightChip,
  type GreeksLabLeg,
  type GreeksLabResponse,
  greeksLabApi,
} from '@/api/greeks-lab'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { isCoaBuy, useCoaBuySiren } from '@/hooks/useCoaBuySiren'
import { useSupportedExchanges } from '@/hooks/useSupportedExchanges'
import { showToast } from '@/utils/toast'

const AUTO_REFRESH_INTERVAL = 15000
const COA_ARMED_POLL_MS = 3000

function convertExpiryForAPI(expiry: string): string {
  if (!expiry) return ''
  const parts = expiry.split('-')
  if (parts.length === 3) {
    return `${parts[0]}${parts[1].toUpperCase()}${parts[2].slice(-2)}`
  }
  return expiry.replace(/-/g, '').toUpperCase()
}

function fmt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function fmtSnooze(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

const INSIGHT_TONE: Record<string, string> = {
  on: 'border-emerald-500/50 bg-emerald-500/10',
  run: 'border-emerald-500/50 bg-emerald-500/10',
  selling_day: 'border-emerald-500/50 bg-emerald-500/10',
  fading: 'border-emerald-500/50 bg-emerald-500/10',
  off: 'border-amber-500/50 bg-amber-500/10',
  pause_shorts: 'border-amber-500/50 bg-amber-500/10',
  buying_day: 'border-amber-500/50 bg-amber-500/10',
  stronger: 'border-amber-500/50 bg-amber-500/10',
  pause: 'border-red-500/50 bg-red-500/10',
  mixed: 'border-border bg-muted/40',
  sideways: 'border-border bg-muted/40',
  unknown: 'border-border bg-muted/40',
}

function chipTone(label: string | null | undefined): string {
  const text = (label || '').toUpperCase()
  if (text.includes('CALL')) return 'bg-emerald-500 text-white'
  if (text.includes('PUT')) return 'bg-red-500 text-white'
  return 'bg-muted text-foreground'
}

function clampPct(value: number, low: number, high: number): number {
  return Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100))
}

function CoaRange({ board }: { board: CoaBoard }) {
  const last = board.last ?? board.spot
  const low = board.low
  const high = board.high
  if (last == null || low == null || high == null || high <= low) return null
  const open = board.open ?? (board.change_vs_open != null ? last - board.change_vs_open : null)
  const lastPct = clampPct(last, low, high)
  const openPct = open == null ? null : clampPct(open, low, high)
  const up = open == null ? null : last >= open
  const fillLeft = openPct == null ? 0 : Math.min(openPct, lastPct)
  const fillWidth = openPct == null ? lastPct : Math.abs(lastPct - openPct)
  const fillClass = up == null ? 'bg-foreground/40' : up ? 'bg-emerald-500' : 'bg-red-500'
  const tickClass = up == null ? 'bg-foreground' : up ? 'bg-emerald-500' : 'bg-red-500'
  return (
    <div className="mt-2">
      <div className="relative h-2.5">
        <div className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-muted" />
        <div
          className={`absolute top-1/2 h-2 -translate-y-1/2 rounded-full ${fillClass}`}
          style={{ left: `${fillLeft}%`, width: `${Math.max(fillWidth, 1.5)}%` }}
        />
        {openPct != null ? (
          <div
            className="absolute top-1/2 h-2 w-0.5 -translate-y-1/2 bg-foreground/80"
            style={{ left: `calc(${openPct}% - 1px)` }}
            title="Open"
          />
        ) : null}
        <div
          className={`absolute top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full border border-background ${tickClass}`}
          style={{ left: `calc(${lastPct}% - 5px)` }}
          title="Last"
        />
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
        <span>{fmt(low, 0)}</span>
        <span>{fmt(high, 0)}</span>
      </div>
    </div>
  )
}

function CoaBoardGrid({
  boards,
  activeSlug,
  onSelect,
}: {
  boards: CoaBoard[]
  activeSlug: string | null
  onSelect: (board: CoaBoard) => void
}) {
  if (boards.length === 0) return null
  return (
    <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">
      {boards.map((board) => {
        const last = board.last ?? board.spot
        const vsOpen = board.change_vs_open
        const active = board.slug === activeSlug
        const tagged = isCoaBuy(board.scenario_label)
        const put = (board.scenario_label || '').toUpperCase().includes('PUT')
        return (
          <button
            key={board.slug}
            type="button"
            onClick={() => onSelect(board)}
            className={`text-left rounded-xl border p-3 transition-colors ${
              tagged
                ? put
                  ? 'border-red-500 ring-2 ring-red-500/50 bg-red-500/10'
                  : 'border-emerald-500 ring-2 ring-emerald-500/50 bg-emerald-500/10'
                : active
                  ? 'border-primary bg-muted/40'
                  : 'border-border hover:bg-muted/30'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-semibold tracking-wide">{board.title}</div>
              <span
                className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${chipTone(board.scenario_label)}`}
              >
                {board.scenario_label || (board.missing ? '—' : 'WATCHING')}
              </span>
            </div>
            <div
              className={`text-xl font-bold mt-2 ${
                vsOpen == null
                  ? ''
                  : vsOpen > 0
                    ? 'text-emerald-500'
                    : vsOpen < 0
                      ? 'text-red-500'
                      : ''
              }`}
            >
              {last == null
                ? '—'
                : last.toLocaleString('en-IN', {
                    minimumFractionDigits: last >= 1000 ? 1 : 2,
                    maximumFractionDigits: last >= 1000 ? 1 : 2,
                  })}
            </div>
            <div
              className={`text-xs ${
                vsOpen == null
                  ? 'text-muted-foreground'
                  : vsOpen > 0
                    ? 'text-emerald-500'
                    : vsOpen < 0
                      ? 'text-red-500'
                      : 'text-muted-foreground'
              }`}
            >
              {vsOpen == null
                ? board.as_of_ist || ''
                : `${vsOpen > 0 ? '▲' : vsOpen < 0 ? '▼' : '●'} ${vsOpen > 0 ? '+' : ''}${fmt(vsOpen, 1)} vs open`}
            </div>
            <CoaRange board={board} />
          </button>
        )
      })}
    </div>
  )
}

function CoaActiveCard({
  boards,
  slug,
  armed,
  alarming,
  snoozeLeftMs,
}: {
  boards: CoaBoard[]
  slug: string | null
  armed: boolean
  alarming: boolean
  snoozeLeftMs: number
}) {
  const active = boards.find((board) => board.slug === slug) || boards[0]
  if (!active || active.missing) return null
  return (
    <Card>
      <CardContent className="p-4 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span
              className={`text-sm font-bold px-3 py-1 rounded-full ${chipTone(active.scenario_label)}`}
            >
              {active.scenario_label || 'WATCHING'}
            </span>
            <span className="font-semibold">{active.title}</span>
          </div>
          <span className="text-xs text-muted-foreground">{active.as_of_ist}</span>
        </div>
        <p className="text-sm">{active.do_now || active.line || 'Map, not an order.'}</p>
        {active.source === 'quote' ? (
          <p className="text-[11px] text-muted-foreground">
            Quote tile only. CoA buy siren stays on Nifty, Bank Nifty, Sensex, and BTC.
          </p>
        ) : armed ? (
          <p className="text-[11px] text-muted-foreground">
            {alarming
              ? 'Siren on. Silence snoozes 5 minutes, then alerts arm again.'
              : 'Alerts armed. Siren only on a new CALL/PUT TAGGED — Hands-confirmed, not a map-only watch.'}
          </p>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {snoozeLeftMs > 0
              ? `Snoozed. Alerts arm again in ${fmtSnooze(snoozeLeftMs)}. Click Alerts armed to wake now.`
              : 'Alerts off. Click Alerts armed in the toolbar to hear a Hands-confirmed CALL/PUT tag.'}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ReadoutCard({ line, sitStill }: { line?: string; sitStill?: string | null }) {
  if (!line) return null
  return (
    <Card className="border-primary/40 bg-primary/5">
      <CardContent className="p-5 space-y-2">
        <div className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          Next hour, in plain words
        </div>
        <p className="text-lg font-semibold leading-snug">{line}</p>
        {sitStill && sitStill !== line ? (
          <p className="text-sm text-muted-foreground">{sitStill}</p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function InsightCard({ chip }: { chip?: DeskInsightChip }) {
  if (!chip) return null
  return (
    <Card className={INSIGHT_TONE[chip.state] || INSIGHT_TONE.mixed}>
      <CardContent className="p-4 space-y-1">
        <div className="text-lg font-bold tracking-tight">{chip.label}</div>
        <p className="text-sm text-muted-foreground">{chip.detail}</p>
      </CardContent>
    </Card>
  )
}

function GreekCard({ title, leg }: { title: string; leg: GreeksLabLeg | null | undefined }) {
  if (!leg) {
    return (
      <Card>
        <CardContent className="p-4">
          <h3 className="font-semibold mb-2">{title}</h3>
          <p className="text-sm text-muted-foreground">No live premium on this strike.</p>
        </CardContent>
      </Card>
    )
  }
  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold">{title}</h3>
          <Badge variant="secondary">{leg.symbol}</Badge>
        </div>
        <div className="text-2xl font-bold">{fmt(leg.ltp)}</div>
        <div className="grid grid-cols-3 gap-2 text-sm">
          <div>
            <div className="text-muted-foreground">IV</div>
            <div className="font-medium">{fmt(leg.iv)}%</div>
          </div>
          <div>
            <div className="text-muted-foreground">Delta</div>
            <div className="font-medium">{fmt(leg.delta, 4)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Gamma</div>
            <div className="font-medium">{fmt(leg.gamma, 5)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Theta / day</div>
            <div className="font-medium">{fmt(leg.theta, 3)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Vega</div>
            <div className="font-medium">{fmt(leg.vega, 3)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">DTE</div>
            <div className="font-medium">{fmt(leg.days_to_expiry, 2)}</div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default function GreeksLab() {
  const { fnoExchanges, defaultFnoExchange, defaultUnderlyings } = useSupportedExchanges()

  const [selectedExchange, setSelectedExchange] = useState(defaultFnoExchange)
  const [underlyings, setUnderlyings] = useState<string[]>(
    defaultUnderlyings[defaultFnoExchange] || []
  )
  const [underlyingOpen, setUnderlyingOpen] = useState(false)
  const [selectedUnderlying, setSelectedUnderlying] = useState(
    defaultUnderlyings[defaultFnoExchange]?.[0] || ''
  )
  const [expiries, setExpiries] = useState<string[]>([])
  const [selectedExpiry, setSelectedExpiry] = useState('')
  const [selectedStrike, setSelectedStrike] = useState<number | null>(null)
  const [horizon, setHorizon] = useState('60')
  const [side, setSide] = useState<'ce' | 'pe'>('ce')
  const [data, setData] = useState<GreeksLabResponse | null>(null)
  const [coaBoards, setCoaBoards] = useState<CoaBoard[]>([])
  const [coaSlug, setCoaSlug] = useState<string | null>('nifty')
  const [isLoading, setIsLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const requestIdRef = useRef(0)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const coaIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pendingUnderlyingRef = useRef<string | null>(null)

  useEffect(() => {
    setSelectedExchange((prev) =>
      prev && fnoExchanges.some((ex) => ex.value === prev) ? prev : defaultFnoExchange
    )
  }, [defaultFnoExchange, fnoExchanges])

  useEffect(() => {
    const defaults = defaultUnderlyings[selectedExchange] || []
    const keep = pendingUnderlyingRef.current
    pendingUnderlyingRef.current = null
    setUnderlyings(defaults)
    setSelectedUnderlying(keep || defaults[0] || '')
    setExpiries([])
    setSelectedExpiry('')
    setSelectedStrike(null)
    setData(null)

    let cancelled = false
    const fetchUnderlyings = async () => {
      try {
        const response = await greeksLabApi.getUnderlyings(selectedExchange)
        if (cancelled) return
        if (response.status === 'success' && response.underlyings.length > 0) {
          setUnderlyings(response.underlyings)
          setSelectedUnderlying((prev) =>
            prev && response.underlyings.includes(prev) ? prev : response.underlyings[0]
          )
        }
      } catch {
        // Keep defaults
      }
    }
    fetchUnderlyings()
    return () => {
      cancelled = true
    }
  }, [selectedExchange, defaultUnderlyings])

  useEffect(() => {
    if (!selectedUnderlying) return
    setExpiries([])
    setSelectedExpiry('')
    setSelectedStrike(null)
    setData(null)

    let cancelled = false
    const fetchExpiries = async () => {
      try {
        const response = await greeksLabApi.getExpiries(selectedExchange, selectedUnderlying)
        if (cancelled) return
        if (response.status === 'success' && response.expiries.length > 0) {
          setExpiries(response.expiries)
          setSelectedExpiry(response.expiries[0])
        }
      } catch {
        if (!cancelled) {
          setExpiries([])
          setSelectedExpiry('')
        }
      }
    }
    fetchExpiries()
    return () => {
      cancelled = true
    }
  }, [selectedUnderlying, selectedExchange])

  const fetchSnapshot = useCallback(async () => {
    if (!selectedExpiry) return
    const requestId = ++requestIdRef.current
    setIsLoading(true)
    try {
      const response = await greeksLabApi.getSnapshot({
        underlying: selectedUnderlying,
        exchange: selectedExchange,
        expiry_date: convertExpiryForAPI(selectedExpiry),
        strike: selectedStrike ?? undefined,
        horizon_minutes: Number(horizon),
      })
      if (requestIdRef.current !== requestId) return
      if (response.status === 'success') {
        setData(response)
        if (selectedStrike === null && response.selected_strike != null) {
          setSelectedStrike(response.selected_strike)
        }
      } else {
        showToast.error(response.message || 'Failed to fetch Greeks')
      }
    } catch {
      if (requestIdRef.current !== requestId) return
      showToast.error('Failed to fetch Greeks')
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false)
    }
  }, [selectedUnderlying, selectedExpiry, selectedExchange, selectedStrike, horizon])

  const fetchCoa = useCallback(async () => {
    try {
      const response = await greeksLabApi.getCoaBoards()
      if (response.status === 'success' && response.boards) {
        setCoaBoards(response.boards)
      }
    } catch {
      // Board is optional; Greeks still load
    }
  }, [])

  const selectCoaBoard = useCallback((board: CoaBoard) => {
    setCoaSlug(board.slug)
    if (board.greeks_exchange && board.greeks_underlying) {
      pendingUnderlyingRef.current = board.greeks_underlying
      setSelectedExchange(board.greeks_exchange)
      setSelectedUnderlying(board.greeks_underlying)
    }
  }, [])

  const { armed, alarming, alarmLabel, snoozeLeftMs, arm, silence, test } = useCoaBuySiren(
    coaBoards,
    selectCoaBoard
  )

  useEffect(() => {
    fetchCoa()
  }, [fetchCoa])

  // biome-ignore lint/correctness/useExhaustiveDependencies: expiry/strike/horizon drive the fetch; fetchSnapshot identity is enough
  useEffect(() => {
    if (selectedExpiry) {
      fetchSnapshot()
    }
  }, [selectedExpiry, selectedStrike, horizon])

  useEffect(() => {
    if (!autoRefresh) return
    intervalRef.current = setInterval(() => {
      if (!armed) fetchCoa()
      if (selectedExpiry) fetchSnapshot()
    }, AUTO_REFRESH_INTERVAL)
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [autoRefresh, fetchSnapshot, fetchCoa, selectedExpiry, armed])

  useEffect(() => {
    if (!armed) return
    fetchCoa()
    coaIntervalRef.current = setInterval(fetchCoa, COA_ARMED_POLL_MS)
    return () => {
      if (coaIntervalRef.current) {
        clearInterval(coaIntervalRef.current)
        coaIntervalRef.current = null
      }
    }
  }, [armed, fetchCoa])

  const whatif = data?.projections?.[side].whatif || []
  const liveLtp = data?.watched?.[side]?.ltp
  const strikes = data?.chain?.map((row) => row.strike) || []

  return (
    <div className="py-6 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Lab</h1>
          <p className="text-sm text-muted-foreground mt-1">
            CoA map first. Alerts stay armed. Silence a siren to snooze 5 minutes, then the desk
            read and next-hour line.
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <Select value={selectedExchange} onValueChange={setSelectedExchange}>
            <SelectTrigger className="w-[100px]">
              <SelectValue placeholder="Exchange" />
            </SelectTrigger>
            <SelectContent>
              {fnoExchanges.map((ex) => (
                <SelectItem key={ex.value} value={ex.value}>
                  {ex.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Popover open={underlyingOpen} onOpenChange={setUnderlyingOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                aria-expanded={underlyingOpen}
                className="w-[160px] justify-between"
              >
                {selectedUnderlying || 'Underlying'}
                <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-48 p-0" align="start">
              <Command>
                <CommandInput placeholder="Search underlying..." />
                <CommandList>
                  <CommandEmpty>No underlying found</CommandEmpty>
                  <CommandGroup>
                    {underlyings.map((u) => (
                      <CommandItem
                        key={u}
                        value={u}
                        onSelect={() => {
                          setSelectedUnderlying(u)
                          setUnderlyingOpen(false)
                        }}
                      >
                        <Check
                          className={`mr-2 h-4 w-4 ${selectedUnderlying === u ? 'opacity-100' : 'opacity-0'}`}
                        />
                        {u}
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </CommandList>
              </Command>
            </PopoverContent>
          </Popover>

          <Select
            value={selectedExpiry}
            onValueChange={setSelectedExpiry}
            disabled={expiries.length === 0}
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue placeholder="Expiry" />
            </SelectTrigger>
            <SelectContent>
              {expiries.map((e) => (
                <SelectItem key={e} value={e}>
                  {e}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={selectedStrike === null ? '' : String(selectedStrike)}
            onValueChange={(value) => setSelectedStrike(Number(value))}
            disabled={strikes.length === 0}
          >
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder="Strike" />
            </SelectTrigger>
            <SelectContent>
              {strikes.map((strike) => (
                <SelectItem key={strike} value={String(strike)}>
                  {strike}
                  {strike === data?.atm_strike ? ' ATM' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={horizon} onValueChange={setHorizon}>
            <SelectTrigger className="w-[110px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="15">15 min</SelectItem>
              <SelectItem value="30">30 min</SelectItem>
              <SelectItem value="60">60 min</SelectItem>
              <SelectItem value="120">2 hours</SelectItem>
            </SelectContent>
          </Select>

          <Button
            variant={autoRefresh ? 'default' : 'outline'}
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            {autoRefresh ? 'Live: ON' : 'Live: OFF'}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              fetchCoa()
              fetchSnapshot()
            }}
            disabled={isLoading}
          >
            {isLoading ? 'Loading...' : 'Refresh'}
          </Button>
          <Button variant={armed ? 'default' : 'outline'} size="sm" onClick={() => void arm()}>
            {armed
              ? 'Alerts armed'
              : snoozeLeftMs > 0
                ? `Snoozed ${fmtSnooze(snoozeLeftMs)}`
                : 'Alerts armed'}
          </Button>
          <Button variant="outline" size="sm" onClick={test}>
            Test siren
          </Button>
          {alarming ? (
            <Button variant="destructive" size="sm" onClick={silence}>
              Silence
            </Button>
          ) : null}
        </div>
      </div>

      {alarming ? (
        <div className="rounded-xl bg-rose-600 text-white px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-bold tracking-widest uppercase">CoA buy tagged</div>
            <p className="text-sm font-semibold">{alarmLabel}</p>
            <p className="text-xs text-white/80">Hands-confirmed. Not a recommendation.</p>
          </div>
          <Button variant="secondary" size="sm" onClick={silence}>
            Silence
          </Button>
        </div>
      ) : null}

      <CoaBoardGrid boards={coaBoards} activeSlug={coaSlug} onSelect={selectCoaBoard} />
      <CoaActiveCard
        boards={coaBoards}
        slug={coaSlug}
        armed={armed}
        alarming={alarming}
        snoozeLeftMs={snoozeLeftMs}
      />

      {data?.status === 'success' && (
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">Spot {fmt(data.spot_price, 1)}</Badge>
          <Badge variant="secondary">ATM {data.atm_strike}</Badge>
          <Badge variant="secondary">ATM IV {fmt(data.atm_iv)}%</Badge>
          <Badge variant="secondary">
            ±1σ / {data.horizon_minutes}m {fmt(data.expected_move)}
          </Badge>
          <Badge variant="outline">Live feed every 15s</Badge>
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        {data?.insights?.disclaimer ||
          data?.disclaimer ||
          'Desk read from the live ATM straddle plus 5m ADX/ATR and India VIX. Not an order.'}
      </p>

      {data?.insights && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <InsightCard chip={data.insights.decay} />
          <InsightCard chip={data.insights.quadshield} />
          <InsightCard chip={data.insights.session} />
          <InsightCard chip={data.insights.trend} />
        </div>
      )}

      <ReadoutCard line={data?.readout?.line} sitStill={data?.readout?.sit_still} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <GreekCard title="Call" leg={data?.watched?.ce} />
        <GreekCard title="Put" leg={data?.watched?.pe} />
      </div>

      <Card>
        <CardContent className="p-4 space-y-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <h3 className="font-semibold text-sm">
              What-if at +{data?.horizon_minutes || 60} min (model premium)
            </h3>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={side === 'ce' ? 'default' : 'outline'}
                onClick={() => setSide('ce')}
              >
                Call
              </Button>
              <Button
                size="sm"
                variant={side === 'pe' ? 'default' : 'outline'}
                onClick={() => setSide('pe')}
              >
                Put
              </Button>
            </div>
          </div>
          {whatif.length > 0 && data?.spot_shocks_pct && data.iv_shocks_pts ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="text-left p-2">Spot \ IV</th>
                    {data.iv_shocks_pts.map((ivPts) => (
                      <th key={ivPts} className="p-2 text-right">
                        {ivPts > 0 ? '+' : ''}
                        {ivPts} vol
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.spot_shocks_pct.map((spotPct, rowIndex) => (
                    <tr key={spotPct} className="border-t">
                      <td className="p-2 font-medium">
                        {spotPct > 0 ? '+' : ''}
                        {spotPct}%
                      </td>
                      {(whatif[rowIndex] || []).map((price, colIndex) => {
                        const delta = liveLtp != null && price != null ? price - liveLtp : 0
                        const tone =
                          price == null
                            ? ''
                            : delta > 0
                              ? 'text-emerald-500'
                              : delta < 0
                                ? 'text-red-500'
                                : ''
                        return (
                          <td
                            key={`${spotPct}-${data.iv_shocks_pts?.[colIndex]}`}
                            className={`p-2 text-right ${tone}`}
                          >
                            {fmt(price)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No what-if grid for this strike.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-4 overflow-x-auto">
          <h3 className="font-semibold text-sm mb-3">Live chain Greeks</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted-foreground">
                <th className="text-right p-2">CE θ</th>
                <th className="text-right p-2">CE Δ</th>
                <th className="text-right p-2">CE IV</th>
                <th className="text-right p-2">CE LTP</th>
                <th className="text-center p-2">Strike</th>
                <th className="text-left p-2">PE LTP</th>
                <th className="text-left p-2">PE IV</th>
                <th className="text-left p-2">PE Δ</th>
                <th className="text-left p-2">PE θ</th>
              </tr>
            </thead>
            <tbody>
              {(data?.chain || []).map((row) => {
                const active = row.strike === data?.selected_strike
                return (
                  <tr
                    key={row.strike}
                    className={`border-t cursor-pointer hover:bg-muted/40 ${active ? 'bg-muted/60' : ''}`}
                    onClick={() => setSelectedStrike(row.strike)}
                  >
                    <td className="p-2 text-right">{fmt(row.ce?.theta, 2)}</td>
                    <td className="p-2 text-right">{fmt(row.ce?.delta, 3)}</td>
                    <td className="p-2 text-right">{fmt(row.ce?.iv, 1)}</td>
                    <td className="p-2 text-right">{fmt(row.ce?.ltp)}</td>
                    <td className="p-2 text-center font-semibold">
                      {row.strike}
                      {row.strike === data?.atm_strike ? ' *' : ''}
                    </td>
                    <td className="p-2">{fmt(row.pe?.ltp)}</td>
                    <td className="p-2">{fmt(row.pe?.iv, 1)}</td>
                    <td className="p-2">{fmt(row.pe?.delta, 3)}</td>
                    <td className="p-2">{fmt(row.pe?.theta, 2)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  )
}
