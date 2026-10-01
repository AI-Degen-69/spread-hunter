export interface SystemStatus {
  bot_state?: string
  scan_interval_sec?: number
  registry_unreadable?: boolean
  db_path?: string
  db_mode?: string
  db_is_production?: boolean
  starting_capital?: number
  timestamp?: number
  services?: {
    filter?: ServiceInfo
    query?: ServiceInfo
    decide?: ServiceInfo
    dash?: ServiceInfo
  }
  shadow_run?: {
    run_id?: string
    pid?: number
    started_at?: number
    minutes?: number
    interval?: number
    cadence_sec?: number
    heartbeat_age_sec?: number
    elapsed_sec?: number
    running?: boolean
    ended?: boolean
    finished?: boolean
  }
}

export interface ServiceInfo {
  name?: string
  running?: boolean
  pid?: number | null
  started_at?: number | null
  uptime_sec?: number | null
}

export interface ScanStateResponse {
  scan_state: 'SCANNING' | 'IDLE' | 'STALLED' | string
  cadence_sec?: number | null
  stale_threshold_sec?: number | null
  seconds_since_heartbeat?: number | null
  seconds_since_scan?: number | null
  last_scan_ts?: number | null
  decisions_logged?: number
  skip_reasons?: Array<{ reason: string; count: number }>
  pass_reasons?: Array<{ reason: string; count: number }>
  telemetry_error?: string | null
}

export interface MarketFunnelItem {
  condition_id: string
  slug: string
  url: string
  title: string
  volume: number
  spread: number
  days_to_resolve: number
  source: string
  est_income?: number
  est_capital?: number
  return_pct_day?: number
  fills?: number
  pnl?: number
}

export interface KpiReport {
  timestamp?: number
  total_pairs?: number
  active_pairs?: number
  realized_pnl?: number
  unrealized_pnl?: number
  total_volume?: number
  win_rate?: number
  capital_utilization?: number
  funnel?: {
    snapshot_age?: number
    source?: string
    census?: string
    gates?: string
    final_count?: number
    graduated?: MarketFunnelItem[]
  }
}

export async function fetchSystemStatus(): Promise<SystemStatus | null> {
  try {
    const res = await fetch('/api/system/status')
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export async function fetchScanState(): Promise<ScanStateResponse | null> {
  try {
    const res = await fetch('/api/scan-state')
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export async function fetchKpi(): Promise<KpiReport | null> {
  try {
    const res = await fetch('/api/kpi')
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}
