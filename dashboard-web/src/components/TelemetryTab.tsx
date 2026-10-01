import React from 'react'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import type { SystemStatus, ScanStateResponse } from '@/lib/api'
import { Server, Activity, ShieldAlert, Clock } from 'lucide-react'

interface TelemetryTabProps {
  status: SystemStatus | null
  scanState: ScanStateResponse | null
}

export const TelemetryTab: React.FC<TelemetryTabProps> = ({
  status,
  scanState,
}) => {
  const services = status?.services ?? {}
  const skipReasons = scanState?.skip_reasons ?? []
  const shadow = status?.shadow_run

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Service Health Card */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Server className="size-4 text-primary" />
            <span>Process Fleet & Services</span>
          </CardTitle>
          <CardDescription className="text-xs">
            Live process supervision and telemetry heartbeat
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {Object.entries(services).map(([key, svc]) => (
            <div
              key={key}
              className="flex items-center justify-between p-2.5 rounded-lg border border-border bg-card/50"
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-xs">
                  {svc.name ?? key}
                </span>
                {svc.pid && (
                  <span className="text-[11px] font-mono text-muted-foreground">
                    PID {svc.pid}
                  </span>
                )}
              </div>
              <Badge variant={svc.running ? 'default' : 'secondary'} className="text-xs font-mono">
                {svc.running ? 'RUNNING' : 'STOPPED'}
              </Badge>
            </div>
          ))}

          {shadow && (
            <div className="mt-2 p-3 rounded-lg border border-border/80 bg-accent/20 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold flex items-center gap-1.5">
                  <Clock className="size-3 text-primary" />
                  Shadow Rehearsal ({shadow.run_id ?? 'shadow-01'})
                </span>
                <Badge variant={shadow.running ? 'default' : 'outline'} className="text-xs">
                  {shadow.running ? 'Active' : 'Ended'}
                </Badge>
              </div>
              <div className="text-[11px] text-muted-foreground grid grid-cols-2 gap-1 font-mono">
                <div>Cadence: {shadow.cadence_sec?.toFixed(1) ?? '-'}s</div>
                <div>Heartbeat: {shadow.heartbeat_age_sec?.toFixed(0) ?? '-'}s ago</div>
                <div>Duration: {shadow.minutes ?? '-'}m</div>
                <div>Elapsed: {Math.round((shadow.elapsed_sec ?? 0) / 60)}m</div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Decision Rationale & Filters */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Activity className="size-4 text-primary" />
            <span>Scanner Filter Decisions</span>
          </CardTitle>
          <CardDescription className="text-xs">
            Per-cycle rejection and pass rationale counts
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="text-xs font-medium text-muted-foreground flex justify-between">
            <span>Decisions Logged</span>
            <span className="font-mono font-bold text-foreground">
              {scanState?.decisions_logged ?? 0}
            </span>
          </div>

          <div className="flex flex-col gap-2 mt-2">
            {skipReasons.length === 0 ? (
              <div className="text-xs text-muted-foreground py-4 text-center">
                No cycle skip reasons recorded yet.
              </div>
            ) : (
              skipReasons.map((sk, idx) => (
                <div
                  key={idx}
                  className="flex flex-col gap-1 p-2 rounded-md bg-muted/40 border border-border/40 text-xs"
                >
                  <div className="flex items-center justify-between font-mono text-[11px]">
                    <span className="truncate max-w-[280px]" title={sk.reason}>
                      {sk.reason}
                    </span>
                    <Badge variant="outline" className="text-[10px] ml-2">
                      {sk.count}
                    </Badge>
                  </div>
                  <Progress value={Math.min(100, sk.count * 10)} className="h-1" />
                </div>
              ))
            )}
          </div>

          {scanState?.telemetry_error && (
            <div className="p-2.5 rounded-lg border border-destructive/30 bg-destructive/10 text-xs text-destructive flex items-center gap-2 mt-2">
              <ShieldAlert className="size-4" />
              <span>{scanState.telemetry_error}</span>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
