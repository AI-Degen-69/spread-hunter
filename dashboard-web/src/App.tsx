import { useState, useEffect, useCallback } from 'react'
import { Header } from '@/components/Header'
import { KpiOverview } from '@/components/KpiOverview'
import { MarketFunnel } from '@/components/MarketFunnel'
import { TelemetryTab } from '@/components/TelemetryTab'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import {
  fetchSystemStatus,
  fetchScanState,
  fetchKpi,
  type SystemStatus,
  type ScanStateResponse,
  type KpiReport,
} from '@/lib/api'
import { LayoutDashboard, Filter, Activity } from 'lucide-react'

const POLL_INTERVAL_MS = 3000

export function App() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [scanState, setScanState] = useState<ScanStateResponse | null>(null)
  const [kpi, setKpi] = useState<KpiReport | null>(null)
  const [loading, setLoading] = useState<boolean>(true)

  const loadData = useCallback(async () => {
    try {
      const [newStatus, newScanState, newKpi] = await Promise.all([
        fetchSystemStatus(),
        fetchScanState(),
        fetchKpi(),
      ])
      if (newStatus) setStatus(newStatus)
      if (newScanState) setScanState(newScanState)
      if (newKpi) setKpi(newKpi)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
    const timer = setInterval(loadData, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [loadData])

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col font-sans">
      <Header
        status={status}
        scanState={scanState}
        loading={loading}
        onRefresh={loadData}
      />

      <main className="flex-1 container mx-auto px-4 py-6">
        <Tabs defaultValue="overview" className="flex flex-col gap-6">
          <TabsList className="grid w-full max-w-md grid-cols-3">
            <TabsTrigger value="overview" className="flex items-center gap-2">
              <LayoutDashboard className="size-4" />
              <span>Overview</span>
            </TabsTrigger>
            <TabsTrigger value="funnel" className="flex items-center gap-2">
              <Filter className="size-4" />
              <span>Funnel</span>
            </TabsTrigger>
            <TabsTrigger value="telemetry" className="flex items-center gap-2">
              <Activity className="size-4" />
              <span>Telemetry</span>
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="flex flex-col gap-6">
            <KpiOverview kpi={kpi} status={status} loading={loading} />
            <MarketFunnel kpi={kpi} loading={loading} />
          </TabsContent>

          <TabsContent value="funnel" className="flex flex-col gap-6">
            <MarketFunnel kpi={kpi} loading={loading} />
          </TabsContent>

          <TabsContent value="telemetry" className="flex flex-col gap-6">
            <TelemetryTab status={status} scanState={scanState} />
          </TabsContent>
        </Tabs>
      </main>

      <footer className="border-t border-border py-4 text-center text-xs text-muted-foreground">
        Spread Hunter Execution Engine & Dashboard • Built with Vite, React & shadcn/ui
      </footer>
    </div>
  )
}

export default App
