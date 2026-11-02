import { cva } from 'class-variance-authority'
import { ArrowDownToLine, ArrowUpToLine, Crosshair, TrendingDown, TrendingUp } from 'lucide-react'
import { buildCoaMap, type CoaBoard, type CoaMapMark, type CoaMapTone } from '@/api/greeks-lab'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

const chipTone = cva('flex min-w-0 flex-col gap-0.5 rounded-lg border px-2.5 py-2', {
  variants: {
    tone: {
      call: 'border-emerald-500/35 bg-emerald-500/10',
      floor: 'border-border/80 bg-background/60',
      ceiling: 'border-border/80 bg-background/60',
      put: 'border-red-500/35 bg-red-500/10',
      spot: 'border-primary/40 bg-primary/10',
    },
  },
  defaultVariants: { tone: 'floor' },
})

const valueTone: Record<CoaMapTone, string> = {
  call: 'text-emerald-500',
  floor: 'text-foreground',
  spot: 'text-foreground',
  ceiling: 'text-foreground',
  put: 'text-red-500',
}

const tickTone: Record<CoaMapTone, string> = {
  call: 'bg-emerald-400',
  floor: 'bg-foreground/70',
  spot: 'bg-primary',
  ceiling: 'bg-foreground/70',
  put: 'bg-red-400',
}

function fmtMapPx(value: number): string {
  return value.toLocaleString('en-IN', {
    maximumFractionDigits: value >= 100 ? 0 : 2,
  })
}

function fmtDelta(value: number, last: number | null): string | null {
  if (last == null) return null
  const delta = value - last
  if (Math.abs(delta) < 1e-9) return 'at last'
  const formatted = Math.abs(delta).toLocaleString('en-IN', {
    maximumFractionDigits: Math.abs(delta) >= 100 ? 0 : 2,
  })
  return delta > 0 ? `+${formatted}` : `−${formatted}`
}

function badgeShift(pct: number): string {
  if (pct < 22) return 'translate-x-0'
  if (pct > 78) return '-translate-x-full'
  return '-translate-x-1/2'
}

function lastStance(last: number | null, floor?: number | null, ceiling?: number | null) {
  if (last == null) return 'inside' as const
  if (floor != null && last < floor) return 'call' as const
  if (ceiling != null && last > ceiling) return 'put' as const
  return 'inside' as const
}

function LevelIcon({ tone }: { tone: CoaMapTone }) {
  const className = 'size-3.5'
  if (tone === 'call') return <TrendingUp className={cn(className, 'text-emerald-500')} />
  if (tone === 'put') return <TrendingDown className={cn(className, 'text-red-500')} />
  if (tone === 'floor') return <ArrowDownToLine className={className} />
  if (tone === 'ceiling') return <ArrowUpToLine className={className} />
  return <Crosshair className={className} />
}

function LevelChip({ mark, last }: { mark: CoaMapMark; last: number | null }) {
  const delta = fmtDelta(mark.value, last)
  return (
    <div className={chipTone({ tone: mark.tone })}>
      <div className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        <LevelIcon tone={mark.tone} />
        <span className="truncate">{mark.label}</span>
      </div>
      <div className={cn('text-sm font-semibold tabular-nums sm:text-base', valueTone[mark.tone])}>
        {fmtMapPx(mark.value)}
      </div>
      {delta ? <div className="text-[10px] tabular-nums text-muted-foreground">{delta}</div> : null}
    </div>
  )
}

export function CoaMap({ board }: { board: CoaBoard }) {
  const map = buildCoaMap(board)
  if (!map) return null
  const lastMark = map.marks.find((mark) => mark.tone === 'spot')
  const last = lastMark?.value ?? null
  const levels = map.marks.filter((mark) => mark.tone !== 'spot').sort((a, b) => a.value - b.value)
  const stance = lastStance(last, board.support, board.resistance)
  const summary = map.marks.map((mark) => `${mark.label} ${fmtMapPx(mark.value)}`).join('. ')

  return (
    <div
      className="space-y-3 rounded-xl border bg-muted/25 p-3"
      role="img"
      aria-label={`CoA map. ${summary}`}
    >
      <div className="relative pt-8">
        <div className="relative h-4">
          <div className="absolute inset-0 overflow-hidden rounded-full bg-muted">
            {map.inside ? (
              <div
                className="absolute inset-y-0 bg-foreground/15"
                style={{ left: `${map.inside.left}%`, width: `${map.inside.width}%` }}
              />
            ) : null}
            {map.callZone ? (
              <div
                className="absolute inset-y-0 bg-emerald-500/80"
                style={{
                  left: `${map.callZone.left}%`,
                  width: `${Math.max(map.callZone.width, 1.5)}%`,
                }}
              />
            ) : null}
            {map.putZone ? (
              <div
                className="absolute inset-y-0 bg-red-500/80"
                style={{
                  left: `${map.putZone.left}%`,
                  width: `${Math.max(map.putZone.width, 1.5)}%`,
                }}
              />
            ) : null}
          </div>
          {levels.map((mark) => (
            <Tooltip key={mark.key}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="absolute top-1/2 z-10 flex h-5 w-3 -translate-x-1/2 -translate-y-1/2 items-center justify-center"
                  style={{ left: `${mark.pct}%` }}
                  aria-label={`${mark.label} ${fmtMapPx(mark.value)}`}
                >
                  <span className={cn('h-5 w-0.5 rounded-full', tickTone[mark.tone])} />
                </button>
              </TooltipTrigger>
              <TooltipContent>
                {mark.label} {fmtMapPx(mark.value)}
              </TooltipContent>
            </Tooltip>
          ))}
          {map.spotPct != null && lastMark ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="absolute top-0 z-20 h-4 w-0"
                  style={{ left: `${map.spotPct}%` }}
                  aria-label={`Last ${fmtMapPx(lastMark.value)}`}
                >
                  <span
                    className={cn(
                      'absolute bottom-full mb-1.5 whitespace-nowrap',
                      badgeShift(map.spotPct)
                    )}
                  >
                    <Badge
                      className={cn(
                        'gap-1 tabular-nums shadow-sm',
                        stance === 'call' && 'border-transparent bg-emerald-500 text-white',
                        stance === 'put' && 'border-transparent bg-red-500 text-white'
                      )}
                    >
                      <Crosshair />
                      Last {fmtMapPx(lastMark.value)}
                    </Badge>
                  </span>
                  <span
                    className={cn(
                      'absolute left-0 top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background shadow',
                      stance === 'call' && 'bg-emerald-500',
                      stance === 'put' && 'bg-red-500',
                      stance === 'inside' && 'bg-primary'
                    )}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent>
                {stance === 'call'
                  ? 'Last is in the call-buy zone'
                  : stance === 'put'
                    ? 'Last is in the put-buy zone'
                    : 'Last is inside the wait band'}
              </TooltipContent>
            </Tooltip>
          ) : null}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {levels.map((mark) => (
          <LevelChip key={mark.key} mark={mark} last={last} />
        ))}
      </div>
    </div>
  )
}
