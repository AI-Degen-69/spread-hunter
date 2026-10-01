import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { SystemStatus, ScanStateResponse } from '@/lib/api'
import { RefreshCw, Activity, ShieldCheck, Database } from 'lucide-react'

interface HeaderProps {
  status: SystemStatus | null
  scanState: ScanStateResponse | null
  loading: boolean
  onRefresh: () => void
}

function basename(path: string): string {
  return path.split(/[/\\]/).pop() ?? path
}

export const Header: React.FC<HeaderProps> = ({
  status,
  scanState,
  loading,
  onRefresh,
}) => {
  const isScanning = scanState?.scan_state === 'SCANNING'
  const isEngineRunning =
    status?.bot_state === 'RUNNING' || status?.shadow_run?.running

  return (
    <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-50">
      <div className="container mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="size-8 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center text-primary font-bold text-base">
            SH
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-semibold tracking-tight text-foreground">
                Spread Hunter
              </h1>
              <Badge variant="outline" className="text-xs font-mono">
                {status?.db_mode ?? 'SHADOW'}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              Vite + React Dashboard (shadcn)
            </p>
          </div>
        </div>

        <div className="flex items-center flex-wrap gap-2">
          {/* Scan status pill */}
          <Badge
            variant={isScanning ? 'default' : 'destructive'}
            className="flex items-center gap-1.5 py-1 px-2.5 font-mono text-xs"
          >
            <Activity className="size-3 animate-pulse" />
            <span>{scanState?.scan_state ?? 'SCAN UNKNOWN'}</span>
              {scanState?.cadence_sec !== undefined &&
                scanState?.cadence_sec !== null && (
              <span className="opacity-70 text-[10px]">
                ({scanState.cadence_sec.toFixed(0)}s)
              </span>
            )}
          </Badge>

          {/* Engine status pill */}
          <Badge
            variant={isEngineRunning ? 'default' : 'destructive'}
            className="flex items-center gap-1.5 py-1 px-2.5 font-mono text-xs"
          >
            <ShieldCheck className="size-3" />
            <span>{isEngineRunning ? 'ENGINE RUNNING' : 'ENGINE DOWN'}</span>
          </Badge>

          {/* Database pill */}
          {status?.db_path && (
            <Badge
              variant="secondary"
              className="hidden md:flex items-center gap-1.5 py-1 px-2.5 font-mono text-xs text-muted-foreground"
            >
              <Database className="size-3" />
              <span className="truncate max-w-[200px]" title={status.db_path}>
                {basename(status.db_path)}
              </span>
            </Badge>
          )}

          {/* Manual refresh button */}
          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={loading}
            className="h-7 text-xs gap-1.5"
          >
            <RefreshCw
              data-icon="inline-start"
              className={loading ? 'animate-spin' : ''}
            />
            Refresh
          </Button>
        </div>
      </div>
    </header>
  )
}
