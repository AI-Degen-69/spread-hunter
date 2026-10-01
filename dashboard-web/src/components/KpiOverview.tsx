import React from 'react'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import type { KpiReport, SystemStatus } from '@/lib/api'
import { TrendingUp, Coins, Layers, Zap } from 'lucide-react'

interface KpiOverviewProps {
  kpi: KpiReport | null
  status: SystemStatus | null
  loading: boolean
}

export const KpiOverview: React.FC<KpiOverviewProps> = ({
  kpi,
  status,
  loading,
}) => {
  if (loading && !kpi) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => (
          <Card key={i} className="p-4">
            <Skeleton className="h-4 w-24 mb-2" />
            <Skeleton className="h-8 w-32 mb-1" />
            <Skeleton className="h-3 w-40" />
          </Card>
        ))}
      </div>
    )
  }

  const pnl = kpi?.realized_pnl ?? 0
  const isPositive = pnl >= 0
  const activePairs = kpi?.active_pairs ?? 0
  const totalPairs = kpi?.total_pairs ?? 0
  const startingCapital = status?.starting_capital
  const funnelCount = kpi?.funnel?.final_count ?? 0

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      {/* Realized PnL Card */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Realized PnL
          </CardTitle>
          <TrendingUp className="size-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="flex items-baseline justify-between gap-2">
            <span
              className={`text-2xl font-bold font-mono ${
                isPositive ? 'text-emerald-500' : 'text-rose-500'
              }`}
            >
              {isPositive ? '+' : ''}${pnl.toFixed(2)}
            </span>
            <Badge variant={isPositive ? 'secondary' : 'destructive'}>
              {isPositive ? 'In Profit' : 'Drawdown'}
            </Badge>
          </div>
          <CardDescription className="text-xs mt-1">
            Settled pair returns merged into USDC
          </CardDescription>
        </CardContent>
      </Card>

      {/* Pairs & Positions Card */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Active / Total Pairs
          </CardTitle>
          <Layers className="size-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-2xl font-bold font-mono">
              {activePairs}{' '}
              <span className="text-base text-muted-foreground font-normal">
                / {totalPairs}
              </span>
            </span>
            <Badge variant="outline" className="font-mono">
              {totalPairs > 0
                ? `${Math.round((activePairs / totalPairs) * 100)}% active`
                : '0%'}
            </Badge>
          </div>
          <CardDescription className="text-xs mt-1">
            Assembled spread pairs in lifecycle
          </CardDescription>
        </CardContent>
      </Card>

      {/* Account Capital Card */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Capital Baseline
          </CardTitle>
          <Coins className="size-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-2xl font-bold font-mono">
              {startingCapital !== undefined && startingCapital !== null
                ? `$${startingCapital.toFixed(2)}`
                : '—'}
            </span>
            <Badge variant="outline" className="text-xs">
              Baseline
            </Badge>
          </div>
          <CardDescription className="text-xs mt-1">
            Dynamic risk bounds scale with this capital
          </CardDescription>
        </CardContent>
      </Card>

      {/* Graduated Markets Card */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Graduated Markets
          </CardTitle>
          <Zap className="size-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-2xl font-bold font-mono">
              {funnelCount}
            </span>
            <Badge variant="secondary" className="text-xs">
              Top Picks
            </Badge>
          </div>
          <CardDescription className="text-xs mt-1">
            Passed liquidity, volume, & spread gates
          </CardDescription>
        </CardContent>
      </Card>
    </div>
  )
}
