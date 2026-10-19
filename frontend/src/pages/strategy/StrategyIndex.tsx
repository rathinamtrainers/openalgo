import {
  ArrowLeftRight,
  Check,
  Clock,
  Copy,
  Eye,
  Plus,
  RefreshCw,
  Settings,
  TrendingDown,
  TrendingUp,
  Webhook,
  Zap,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getStrategyBooks, type StrategyBook } from '@/api/strategy-books'
import { strategyApi } from '@/api/strategy'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { Strategy } from '@/types/strategy'
import { showToast } from '@/utils/toast'

export default function StrategyIndex() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [books, setBooks] = useState<StrategyBook[]>([])
  const [loading, setLoading] = useState(true)
  const [booksLoading, setBooksLoading] = useState(true)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [hostConfig, setHostConfig] = useState<{
    host_server: string
    is_localhost: boolean
  } | null>(null)

  const fetchStrategies = async () => {
    try {
      setLoading(true)
      const data = await strategyApi.getStrategies()
      setStrategies(data)
    } catch (_error) {
      showToast.error('Failed to load strategies', 'strategy')
    } finally {
      setLoading(false)
    }
  }

  // Fetch host configuration on mount
  useEffect(() => {
    const fetchHostConfig = async () => {
      try {
        const response = await fetch('/api/config/host', { credentials: 'include' })
        const data = await response.json()
        setHostConfig(data)
      } catch (_error) {
        // Fallback to window.location.origin if config fetch fails
        setHostConfig({
          host_server: window.location.origin,
          is_localhost:
            window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1',
        })
      }
    }
    fetchHostConfig()
  }, [])

  const fetchBooks = async () => {
    try {
      setBooksLoading(true)
      setBooks(await getStrategyBooks())
    } catch {
      showToast.error('Failed to load running books', 'strategy')
    } finally {
      setBooksLoading(false)
    }
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: one-time fetch on mount
  useEffect(() => {
    fetchStrategies()
    fetchBooks()
  }, [])

  const bookBadge = (status: StrategyBook['run_status']) => {
    if (status === 'live-entered') return { label: 'Live-Entered', variant: 'default' as const }
    if (status === 'active') return { label: 'Active', variant: 'default' as const }
    if (status === 'parked') return { label: 'Parked', variant: 'secondary' as const }
    return { label: 'Mind down', variant: 'destructive' as const }
  }

  // Get webhook URL using host config
  const getWebhookUrl = (webhookId: string): string => {
    const baseUrl = hostConfig?.host_server || window.location.origin
    return `${baseUrl}/strategy/webhook/${webhookId}`
  }

  const copyWebhookUrl = async (webhookId: string) => {
    const url = getWebhookUrl(webhookId)
    try {
      await navigator.clipboard.writeText(url)
      setCopiedId(webhookId)
      showToast.success('Webhook URL copied to clipboard', 'clipboard')
      setTimeout(() => setCopiedId(null), 2000)
    } catch {
      showToast.error('Failed to copy URL', 'clipboard')
    }
  }

  const getTradingModeIcon = (mode: string) => {
    switch (mode) {
      case 'LONG':
        return <TrendingUp className="h-4 w-4 text-green-500" />
      case 'SHORT':
        return <TrendingDown className="h-4 w-4 text-red-500" />
      case 'BOTH':
        return <ArrowLeftRight className="h-4 w-4 text-blue-500" />
      default:
        return null
    }
  }

  const getPlatformLabel = (platform: string) => {
    const labels: Record<string, string> = {
      tradingview: 'TradingView',
      amibroker: 'Amibroker',
      python: 'Python',
      metatrader: 'Metatrader',
      excel: 'Excel',
      others: 'Others',
    }
    return labels[platform] || platform
  }

  if (loading) {
    return (
      <div className="container mx-auto py-6 space-y-6">
        <div className="flex justify-between items-center">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-10 w-32" />
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto py-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">My Strategies</h1>
          <p className="text-muted-foreground">
            Running Strike Desk books, then webhook templates
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              fetchStrategies()
              fetchBooks()
            }}
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button onClick={() => navigate('/strategy/new')}>
            <Plus className="h-4 w-4 mr-2" />
            New Strategy
          </Button>
        </div>
      </div>

      <Tabs defaultValue="running">
        <TabsList>
          <TabsTrigger value="running">Running</TabsTrigger>
          <TabsTrigger value="webhooks">Webhooks</TabsTrigger>
        </TabsList>
        <TabsContent value="running" className="space-y-4 pt-2">
          <p className="text-sm text-muted-foreground">
            Status only. Click a book for its event timeline. Analyzer fills stay in ZeroTouchAlgo
            Analyze. Hands books fill on Tradetron. This page does not execute.
          </p>
          {booksLoading ? (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-40" />
              ))}
            </div>
          ) : books.length === 0 ? (
            <Card className="py-10">
              <CardContent className="text-center text-muted-foreground">
                No Strike Desk books registered.
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {books.map((book) => {
                const badge = bookBadge(book.run_status)
                const href = book.audit_href || `/strategy/books/${book.id}`
                const card = (
                  <Card className="relative overflow-hidden hover:border-primary/50 transition-colors">
                    <div
                      className={`absolute top-0 left-0 right-0 h-1 ${
                        book.run_status === 'live-entered'
                          ? 'bg-green-500'
                          : book.run_status === 'active'
                            ? 'bg-blue-500'
                            : book.run_status === 'parked'
                              ? 'bg-gray-400'
                              : 'bg-red-500'
                      }`}
                    />
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="space-y-1">
                          <CardTitle className="text-lg">{book.name}</CardTitle>
                          <CardDescription>
                            {book.kind_label} · {book.underlying}
                          </CardDescription>
                        </div>
                        <Badge variant={badge.variant}>{badge.label}</Badge>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-2 text-sm">
                      <p className="text-muted-foreground">{book.detail}</p>
                      <div className="flex flex-wrap gap-2">
                        <Badge variant="outline" className="font-normal">
                          {book.execution_label}
                        </Badge>
                        {book.tt_sid != null && (
                          <Badge variant="outline" className="font-normal font-mono">
                            SID {book.tt_sid}
                          </Badge>
                        )}
                        <Badge variant="outline" className="font-normal">
                          mind {book.mind}
                        </Badge>
                        <Badge variant="outline" className="font-normal">
                          timeline
                        </Badge>
                      </div>
                    </CardContent>
                  </Card>
                )
                return (
                  <Link key={book.id} to={href} className="block h-full">
                    {card}
                  </Link>
                )
              })}
            </div>
          )}
        </TabsContent>
        <TabsContent value="webhooks" className="space-y-6 pt-2">
      <Alert>
        <Webhook className="h-4 w-4" />
        <AlertTitle>Webhook Integration</AlertTitle>
        <AlertDescription>
          Create strategies to receive alerts from TradingView, Amibroker, Python scripts, and more.
          Use keywords in your alert: <code className="bg-muted px-1 rounded">BUY</code>,{' '}
          <code className="bg-muted px-1 rounded">SELL</code>,{' '}
          <code className="bg-muted px-1 rounded">SHORT</code>,{' '}
          <code className="bg-muted px-1 rounded">COVER</code>
        </AlertDescription>
      </Alert>

      {strategies.length === 0 ? (
        <Card className="py-12">
          <CardContent className="flex flex-col items-center justify-center text-center">
            <Zap className="h-12 w-12 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">No Strategies Yet</h3>
            <p className="text-muted-foreground mb-4">
              Create your first webhook strategy to start receiving trading alerts.
            </p>
            <Button onClick={() => navigate('/strategy/new')}>
              <Plus className="h-4 w-4 mr-2" />
              Create Strategy
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {strategies.map((strategy) => (
            <Card key={strategy.id} className="relative overflow-hidden">
              {/* Status indicator bar */}
              <div
                className={`absolute top-0 left-0 right-0 h-1 ${
                  strategy.is_active ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'
                }`}
              />

              <CardHeader className="pb-3">
                <div className="flex items-start justify-between">
                  <div className="space-y-1">
                    <CardTitle className="text-lg">{strategy.name}</CardTitle>
                    <CardDescription>{getPlatformLabel(strategy.platform)}</CardDescription>
                  </div>
                  <Badge variant={strategy.is_active ? 'default' : 'secondary'}>
                    {strategy.is_active ? 'Active' : 'Inactive'}
                  </Badge>
                </div>
              </CardHeader>

              <CardContent className="space-y-4">
                {/* Strategy Info */}
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div className="flex items-center gap-2">
                    {getTradingModeIcon(strategy.trading_mode)}
                    <span className="text-muted-foreground">{strategy.trading_mode}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="font-normal">
                      {strategy.is_intraday ? 'Intraday' : 'Positional'}
                    </Badge>
                  </div>
                </div>

                {/* Trading Hours (if intraday) */}
                {strategy.is_intraday && strategy.start_time && (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Clock className="h-4 w-4" />
                    <span>
                      {strategy.start_time} - {strategy.end_time}
                      {strategy.squareoff_time && ` (SqOff: ${strategy.squareoff_time})`}
                    </span>
                  </div>
                )}

                {/* Webhook URL Copy */}
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1 justify-start text-xs font-mono truncate"
                    onClick={() => copyWebhookUrl(strategy.webhook_id)}
                  >
                    {copiedId === strategy.webhook_id ? (
                      <Check className="h-3 w-3 mr-2 text-green-500" />
                    ) : (
                      <Copy className="h-3 w-3 mr-2" />
                    )}
                    <span className="truncate">.../{strategy.webhook_id.slice(0, 8)}...</span>
                  </Button>
                </div>

                {/* Action Buttons */}
                <div className="flex gap-2 pt-2">
                  <Button variant="outline" size="sm" className="flex-1" asChild>
                    <Link to={`/strategy/${strategy.id}/configure`}>
                      <Settings className="h-4 w-4 mr-2" />
                      Symbols
                    </Link>
                  </Button>
                  <Button variant="default" size="sm" className="flex-1" asChild>
                    <Link to={`/strategy/${strategy.id}`}>
                      <Eye className="h-4 w-4 mr-2" />
                      View
                    </Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Keywords Reference */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Alert Keywords Reference</CardTitle>
          <CardDescription>
            Include these keywords in your alert message to trigger orders
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="space-y-1">
              <Badge className="bg-green-500">BUY</Badge>
              <p className="text-sm text-muted-foreground">Open long position</p>
            </div>
            <div className="space-y-1">
              <Badge className="bg-red-500">SELL</Badge>
              <p className="text-sm text-muted-foreground">Close long position</p>
            </div>
            <div className="space-y-1">
              <Badge className="bg-orange-500">SHORT</Badge>
              <p className="text-sm text-muted-foreground">Open short position</p>
            </div>
            <div className="space-y-1">
              <Badge className="bg-blue-500">COVER</Badge>
              <p className="text-sm text-muted-foreground">Close short position</p>
            </div>
          </div>
        </CardContent>
      </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
