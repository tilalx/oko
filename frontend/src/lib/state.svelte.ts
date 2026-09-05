import type { CurrentBreakdown, ExchangeEdge, ForecastPoint, HistoryPoint } from './api'
import { COLORBLIND_INTENSITY_STOPS, COLORBLIND_PRICE_STOPS, INTENSITY_STOPS, PRICE_STOPS } from './constants'
import { detectLocale, detectTimeZone, detectHourCycle } from './i18n'

function loadBool(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function saveBool(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? '1' : '0')
  } catch {
    // localStorage unavailable (private browsing, etc.) -- setting just
    // won't persist across reloads.
  }
}

/** Singleton app state (Svelte 5 runes) -- replaces app.js's module-level
 * `let` globals. One instance shared by every component via `$lib/state`. */
class OkoState {
  selectedZone = $state('DE-LU')
  /** "electricity" | "emissions" -- which Now-tab breakdown is shown. */
  mixView = $state<'electricity' | 'emissions'>('electricity')

  zoneForecasts = $state<Record<string, ForecastPoint[]>>({})
  /** oldest -> latest = "now". */
  zoneHistory = $state<Record<string, HistoryPoint[]>>({})
  zoneCurrent = $state<Record<string, CurrentBreakdown | null>>({})
  zoneCentroids = $state<Record<string, [number, number]>>({})
  zoneBoundaryPoints = $state<Record<string, [number, number][]>>({})
  exchangesData = $state<ExchangeEdge[]>([])

  flowLinesVisible = $state(true)
  /** Index into the selected zone's unified [...history, ...forecast] array. */
  horizonIndex = $state(0)
  /** Whether the timeline is pinned to "now" (vs user-scrubbed). */
  horizonAtNow = $state(true)
  playing = $state(false)

  allZones = $state<string[]>([])
  /** Latest observed CurrentBreakdown for the selected zone. */
  lastCurrent = $state<CurrentBreakdown | null>(null)

  /** URL hash (no '#'), e.g. 'docs' -- the app's only route besides the map. */
  route = $state(typeof window !== 'undefined' ? window.location.hash.slice(1) : '')

  colorblindPalette = $state(loadBool('oko-colorblind'))
  use24h = $state(loadBool('oko-24h') ?? detectHourCycle())
  sidebarCollapsed = $state(loadBool('oko-sidebar-collapsed'))
  promoDismissed = $state(loadBool('oko-promo-dismissed'))
  cardVisible = $state(true)
  tilesLight = $state(false)
  locale = $state(detectLocale())
  timeZone = $state(detectTimeZone())

  activeIntensityStops = $derived(this.colorblindPalette ? COLORBLIND_INTENSITY_STOPS : INTENSITY_STOPS)
  activePriceStops = $derived(this.colorblindPalette ? COLORBLIND_PRICE_STOPS : PRICE_STOPS)

  setColorblind(value: boolean) {
    this.colorblindPalette = value
    saveBool('oko-colorblind', value)
  }
  setUse24h(value: boolean) {
    this.use24h = value
    saveBool('oko-24h', value)
  }
  setSidebarCollapsed(value: boolean) {
    this.sidebarCollapsed = value
    saveBool('oko-sidebar-collapsed', value)
  }
  dismissPromo() {
    this.promoDismissed = true
    saveBool('oko-promo-dismissed', true)
  }

  historyLength(zone: string): number {
    return (this.zoneHistory[zone] || []).length
  }

  /** [...zoneHistory, ...zoneForecasts]. zoneHistory's last point is "now";
   * zoneForecasts[0] is "+1h" -- the seam falls out of the concatenation
   * with no synthetic marker needed. */
  unifiedPoints(zone: string): (HistoryPoint | ForecastPoint)[] {
    return [...(this.zoneHistory[zone] || []), ...(this.zoneForecasts[zone] || [])]
  }

  nowSeamIndex(zone: string): number {
    return Math.max(0, this.historyLength(zone) - 1)
  }

  /** Lifecycle-inclusive intensity when available, falling back to direct
   * for a zone whose lifecycle model hasn't bootstrapped yet -- OKO's UI
   * shows one carbon-intensity number, not a direct/lifecycle choice. */
  pointValue(point: HistoryPoint | ForecastPoint | undefined | null): number | null {
    if (!point) return null
    return point.value_lifecycle ?? point.value ?? null
  }

  zoneValueAtUnifiedIndex(zone: string, index: number): number | null {
    const points = this.unifiedPoints(zone)
    if (!points.length) return null
    const clamped = Math.max(0, Math.min(index, points.length - 1))
    return this.pointValue(points[clamped])
  }

  /** horizonIndex is an absolute index into the *selected* zone's unified
   * timeline. Every other zone's unifiedPoints array can be a different
   * length (zones with gappier/laggier ENTSO-E history have a shorter
   * history portion), so reusing that same absolute index for them would
   * silently spill past their "now" seam into their forecast array --
   * e.g. showing France's forecast under DE-LU's "now" position. Instead,
   * carry the scrub position as an *offset from "now"* and re-anchor it to
   * each zone's own now-seam, so every zone shows its own observed value
   * at "now" and the same relative hour when scrubbed. */
  horizonIndexForZone(zone: string): number {
    const offset = this.horizonIndex - this.nowSeamIndex(this.selectedZone)
    return this.nowSeamIndex(zone) + offset
  }
}

export const oko = new OkoState()
