import { HISTORY_SCRUB_HOURS } from './constants'

// Mirrors oko/api/schemas.py exactly -- these are response shapes, not a
// second source of truth for the payload.

export interface ForecastPoint {
  timestamp: string
  value: number
  value_lifecycle: number | null
  confidence: 'high' | 'medium' | 'low'
  power_breakdown_percent: Record<string, number> | null
  price_eur_per_mwh: number | null
}

export interface CurrentBreakdown {
  timestamp: string
  power_breakdown_percent: Record<string, number>
  renewable_percent: number
  fossil_free_percent: number
  emissions_breakdown_percent: Record<string, number>
}

export interface ForecastPayload {
  zone: string
  generated_at: string
  model_version: string
  unit: 'gCO2eq/kWh'
  training_rows: number
  current: CurrentBreakdown | null
  forecast: ForecastPoint[]
  attribution: string[]
  source: string
}

export interface HistoryPoint {
  timestamp: string
  value: number
  value_lifecycle: number | null
  method: 'flow_trace' | 'one_hop_fallback' | null
  power_breakdown_percent: Record<string, number> | null
  price_eur_per_mwh: number | null
}

export interface ZoneStatus {
  zone: string
  available: boolean
}

export interface ExchangeEdge {
  zone_from: string
  zone_to: string
  timestamp: string
  net_flow_mw: number
}

export interface ExchangesPayload {
  generated_at: string
  exchanges: ExchangeEdge[]
  source: string
}

export function forecastUrl(zone: string): string {
  return zone === 'DE-LU' ? '/de.json' : `/${zone}.json`
}

export async function getZones(): Promise<string[]> {
  try {
    const res = await fetch('/zones', { cache: 'no-store' })
    if (!res.ok) return ['DE-LU']
    const payload: { zones: ZoneStatus[] } = await res.json()
    return payload.zones.map((z) => z.zone)
  } catch {
    return ['DE-LU']
  }
}

export async function getForecast(zone: string): Promise<{ payload: ForecastPayload | null; status: number }> {
  const res = await fetch(forecastUrl(zone), { cache: 'no-store' })
  if (!res.ok) return { payload: null, status: res.status }
  return { payload: await res.json(), status: res.status }
}

export async function getHistory(zone: string, hours = HISTORY_SCRUB_HOURS): Promise<HistoryPoint[]> {
  try {
    const res = await fetch(`/history/${zone}?hours=${hours}`, { cache: 'no-store' })
    return res.ok ? await res.json() : []
  } catch {
    return []
  }
}

export async function getExchanges(): Promise<ExchangeEdge[]> {
  try {
    const res = await fetch('/exchanges.json', { cache: 'no-store' })
    if (!res.ok) return []
    const payload: ExchangesPayload = await res.json()
    return payload.exchanges || []
  } catch {
    return []
  }
}

export async function getZonesGeoJson(): Promise<any> {
  const res = await fetch('/zones.geojson', { cache: 'force-cache' })
  return res.json()
}
