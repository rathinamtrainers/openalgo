import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getStackStatus, type StackComponent, type StackStatus } from '@/api/health'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { APP_NAME } from '@/config/branding'
import { cn } from '@/lib/utils'

const REFRESH_MS = 15000

function ledClass(status: StackComponent['status'] | 'unknown') {
  if (status === 'pass') return 'bg-green-500'
  if (status === 'warn') return 'bg-yellow-500'
  if (status === 'fail') return 'bg-red-500'
  return 'bg-gray-400 animate-pulse'
}

function textClass(status: StackComponent['status'] | 'unknown') {
  if (status === 'pass') return 'text-green-600 dark:text-green-400'
  if (status === 'warn') return 'text-yellow-600 dark:text-yellow-400'
  if (status === 'fail') return 'text-red-600 dark:text-red-400'
  return 'text-muted-foreground'
}

function useStackStatus() {
  const [stack, setStack] = useState<StackStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const data = await getStackStatus()
        if (!cancelled) setStack(data)
      } catch {
        if (!cancelled) {
          setStack({
            overall: 'fail',
            updated_at: new Date().toISOString(),
            components: [
              {
                id: 'openalgo',
                label: APP_NAME,
                status: 'fail',
                detail: 'status API unreachable',
              },
            ],
          })
        }
      }
    }
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return stack
}

export function StackStatusBar() {
  const stack = useStackStatus()
  const overall = stack?.overall ?? 'unknown'

  return (
    <div className="rounded-lg border bg-muted/40 p-3 md:p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold">Stack status</p>
          <p className="text-xs text-muted-foreground">
            Mind, Hands, broker and feed — refreshes every 15s
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn('h-3 w-3 rounded-full', ledClass(overall))} />
          <span className={cn('text-sm font-medium capitalize', textClass(overall))}>
            {overall === 'pass' ? 'All up' : overall === 'warn' ? 'Check' : 'Down'}
          </span>
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-2">
        {(stack?.components ?? []).map((row) => (
          <div key={row.id} className="rounded-md bg-background px-2.5 py-2">
            <div className="flex items-center gap-1.5">
              <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', ledClass(row.status))} />
              <span className="text-xs font-medium truncate">{row.label}</span>
            </div>
            <p className={cn('mt-1 text-[11px] leading-snug', textClass(row.status))}>{row.detail}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function StackStatusLamp() {
  const stack = useStackStatus()
  const overall = stack?.overall ?? 'unknown'

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="hidden sm:flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs hover:bg-muted"
          title="Stack status"
        >
          <span className={cn('h-2.5 w-2.5 rounded-full', ledClass(overall))} />
          <span className="hidden lg:inline font-medium">Stack</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-3">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-sm font-semibold">Key components</p>
          <Link to="/dashboard" className="text-xs text-primary hover:underline">
            Dashboard
          </Link>
        </div>
        <div className="space-y-1.5">
          {(stack?.components ?? []).map((row) => (
            <div key={row.id} className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', ledClass(row.status))} />
                <span className="text-sm truncate">{row.label}</span>
              </div>
              <span className={cn('text-xs text-right', textClass(row.status))}>{row.detail}</span>
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}
