import { AlertTriangle, CheckCircle, Download, Eye, Filter } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { StrategyPerformanceReport } from '@/api/strategy-books'
import { StrategySandboxReport } from '@/components/strategy/StrategySandboxReport'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { JsonEditor } from '@/components/ui/json-editor'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface ApiRequest {
  timestamp: string
  api_type: string
  source: string
  symbol?: string
  quantity?: number
  position_size?: number
  orderid?: string
  exchange?: string
  action?: string
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

interface AnalyzerData {
  requests: ApiRequest[]
  stats: Stats
}

function todayIst(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
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

export default function Analyzer() {
  const [data, setData] = useState<AnalyzerData | null>(null)
  const [performance, setPerformance] = useState<StrategyPerformanceReport | null>(null)
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [filterMode, setFilterMode] = useState<'till' | 'range'>('till')
  const [tillDate, setTillDate] = useState(todayIst)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState(todayIst)
  const [selectedRequest, setSelectedRequest] = useState<ApiRequest | null>(null)
  const [showDetailsDialog, setShowDetailsDialog] = useState(false)

  const filterDates = (): { start: string; end: string } => {
    if (filterMode === 'till') {
      return { start: '', end: tillDate || todayIst() }
    }
    return { start: startDate, end: endDate }
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: one-time data load on mount; subsequent fetches are triggered explicitly by the filter form
  useEffect(() => {
    const end = todayIst()
    fetchData('', end)
    fetchPerformance('', end)
  }, [])

  const fetchPerformance = async (start?: string, end?: string) => {
    try {
      const params = new URLSearchParams()
      if (start) params.append('start_date', start)
      if (end) params.append('end_date', end)
      const url = params.toString()
        ? `/analyzer/api/performance?${params}`
        : '/analyzer/api/performance'
      const response = await fetch(url, { credentials: 'include' })
      if (!response.ok) return
      const result = await response.json()
      if (result.status === 'success' && result.data?.strategies) {
        setPerformance(result.data)
      }
    } catch (_error) {}
  }

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
    const { start, end } = filterDates()
    fetchData(start, end)
    fetchPerformance(start, end)
  }

  const handleExport = () => {
    const { start, end } = filterDates()
    const params = new URLSearchParams()
    if (start) params.append('start_date', start)
    if (end) params.append('end_date', end)

    const url = params.toString() ? `/analyzer/export?${params}` : '/analyzer/export'
    window.location.href = url
  }

  const switchMode = (mode: 'till' | 'range') => {
    setFilterMode(mode)
    if (mode === 'till' && !tillDate) {
      setTillDate(todayIst())
    }
    if (mode === 'range' && !endDate) {
      setEndDate(tillDate || todayIst())
    }
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
    if (request.quantity) {
      details += ` (${request.quantity})`
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

  return (
    <div className="container mx-auto py-6 px-4">
      <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold">Sandbox</h1>
          <p className="text-sm text-muted-foreground">How the strategies did, then the request log.</p>
        </div>
        <form onSubmit={handleFilter} className="flex flex-wrap items-end gap-2">
          <Button
            type="button"
            size="sm"
            variant={filterMode === 'till' ? 'default' : 'outline'}
            onClick={() => switchMode('till')}
          >
            Till date
          </Button>
          <Button
            type="button"
            size="sm"
            variant={filterMode === 'range' ? 'default' : 'outline'}
            onClick={() => switchMode('range')}
          >
            Date range
          </Button>
          {filterMode === 'till' ? (
            <Input
              id="till_date"
              type="date"
              value={tillDate}
              onChange={(e) => setTillDate(e.target.value)}
              className="date-input-styled w-[11.5rem]"
            />
          ) : (
            <>
              <Input
                id="start_date"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="date-input-styled w-[11.5rem]"
              />
              <Input
                id="end_date"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="date-input-styled w-[11.5rem]"
              />
            </>
          )}
          <Button type="submit" size="sm">
            <Filter className="h-4 w-4 mr-1" />
            Filter
          </Button>
          <Button type="button" size="sm" variant="secondary" onClick={handleExport}>
            <Download className="h-4 w-4 mr-1" />
            CSV
          </Button>
        </form>
      </div>

      <StrategySandboxReport
        report={performance}
        selectedId={selectedStrategyId}
        onSelect={(id) => setSelectedStrategyId((current) => (current === id ? null : id))}
      />

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Request details</CardTitle>
              <CardDescription>Each placeorder and close stays its own line.</CardDescription>
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <Badge variant="outline">{requests.length} rows</Badge>
              <Badge variant="outline">{stats.issues.total} issues</Badge>
              <Badge variant="outline">{stats.symbols.length} symbols</Badge>
              <Badge variant="outline">{stats.sources.length} sources</Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Timestamp</TableHead>
                  <TableHead>API Type</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Details</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>View</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {requests.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
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
                      <TableCell className="font-medium">{getRequestDetails(request)}</TableCell>
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
