import React from 'react'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from '@/components/ui/card'
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import type { KpiReport } from '@/lib/api'
import { ExternalLink, Filter, Info } from 'lucide-react'

interface MarketFunnelProps {
  kpi: KpiReport | null
  loading: boolean
}

export const MarketFunnel: React.FC<MarketFunnelProps> = ({
  kpi,
  loading,
}) => {
  const funnel = kpi?.funnel
  const graduated = funnel?.graduated ?? []

  if (loading && !kpi) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-6 w-48 mb-2" />
          <Skeleton className="h-4 w-72" />
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-2">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Census & Gates Banner */}
      {funnel?.census && (
        <Alert className="bg-card border-border">
          <Info className="size-4" />
          <AlertTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Market Census
          </AlertTitle>
          <AlertDescription className="text-xs text-muted-foreground mt-1">
            {funnel.census}
          </AlertDescription>
        </Alert>
      )}

      {/* Graduated Markets Table Card */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base font-semibold flex items-center gap-2">
              <Filter className="size-4 text-primary" />
              <span>Selection Funnel — Graduated Markets</span>
            </CardTitle>
            <CardDescription className="text-xs mt-1">
              Markets satisfying all liquidity, depth, and spread criteria
            </CardDescription>
          </div>
          <Badge variant="outline" className="font-mono">
            {graduated.length} Passed
          </Badge>
        </CardHeader>
        <CardContent>
          {graduated.length === 0 ? (
            <div className="text-center py-8 text-sm text-muted-foreground">
              No graduated markets found in the latest scan cycle.
            </div>
          ) : (
            <div className="rounded-md border border-border overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Market Title</TableHead>
                    <TableHead className="text-right">24h Vol</TableHead>
                    <TableHead className="text-right">Spread</TableHead>
                    <TableHead className="text-right">Est. Daily Inc.</TableHead>
                    <TableHead className="text-right">Days Left</TableHead>
                    <TableHead className="text-center">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {graduated.map((m) => (
                    <TableRow key={m.condition_id}>
                      <TableCell className="font-medium max-w-md">
                        <div className="truncate font-semibold" title={m.title}>
                          {m.title}
                        </div>
                        <div className="text-[11px] text-muted-foreground font-mono truncate">
                          {m.slug}
                        </div>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        ${Math.round(m.volume).toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {(m.spread * 100).toFixed(1)}¢
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-emerald-500 font-semibold">
                        {m.est_income !== undefined && m.est_income !== null
                          ? `$${m.est_income.toFixed(3)}/d`
                          : '-'}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {m.days_to_resolve !== undefined &&
                        m.days_to_resolve !== null
                          ? `${m.days_to_resolve.toFixed(1)}d`
                          : '-'}
                      </TableCell>
                      <TableCell className="text-center">
                        {m.url ? (
                          <a
                            href={m.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                          >
                            <span>Open</span>
                            <ExternalLink className="size-3" />
                          </a>
                        ) : (
                          <span className="text-muted-foreground text-xs">-</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
