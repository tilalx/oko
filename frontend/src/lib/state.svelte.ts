import type { CurrentBreakdown, ExchangeEdge, ForecastPoint, HistoryPoint } from './api'
import {
  COLORBLIND_INTENSITY_STOPS,
  COLORBLIND_PRICE_STOPS,
  INTENSITY_STOPS,
  PRICE_STOPS,
  WINDOW_HOURS,
} from './constants'
import { detectLocale, detectTimeZone, detectHourCycle } from './i18n'

const HOUR_MS = 3_600_000
/** How far a point's timestamp may sit from a queried time and still count
 * as "at" it -- half the hourly data resolution, so a scrub position never
 * silently borrows a neighboring hour's value. */
const POINT_MATCH_TOLERANCE_MS = 30 * 60 * 1000

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
  /** Absolute scrub position, as an epoch-ms timestamp -- meaningful
   * identically across every zone, unlike an index into a zone-specific
   * array (whose length varies with how gappy that zone's ENTSO-E history
   * is). See Timebar.svelte. */
  horizonTime = $state(Date.now())
  /** Whether the timeline is pinned to "now" (vs user-scrubbed). */
  horizonAtNow = $state(true)
  playing = $state(false)

  /** Wall-clock "now", ticked independently of the ~5min data refresh so
   * the Timebar's window and "now" marker track live. */
  nowMs = $state(Date.now())
  /** Timebar zoom preset -- see WINDOW_HOURS. */
  windowGranularity = $state<'day' | 'week' | 'month'>('day')

  /** `nowMs` floored to the top of the hour -- data is hourly-resolution,
   * so window bounds and "at now" comparisons anchor here rather than to
   * the constantly-ticking millisecond clock. */
  nowHourMs = $derived(Math.floor(this.nowMs / HOUR_MS) * HOUR_MS)
  windowStartMs = $derived(this.nowHourMs - WINDOW_HOURS[this.windowGranularity].before * HOUR_MS)
  windowEndMs = $derived(this.nowHourMs + WINDOW_HOURS[this.windowGranularity].after * HOUR_MS)

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

  /** Lifecycle-inclusive intensity when available, falling back to direct
   * for a zone whose lifecycle model hasn't bootstrapped yet -- OKO's UI
   * shows one carbon-intensity number, not a direct/lifecycle choice. */
  pointValue(point: HistoryPoint | ForecastPoint | undefined | null): number | null {
    if (!point) return null
    return point.value_lifecycle ?? point.value ?? null
  }

  /** The unified point closest to `timeMs`, or `null` if the nearest one
   * is more than half an hour away -- an absolute timestamp is meaningful
   * identically for every zone, so (unlike an index) this needs no
   * per-zone re-anchoring when the selected zone changes. */
  pointAtTime(zone: string, timeMs: number): HistoryPoint | ForecastPoint | null {
    const points = this.unifiedPoints(zone)
    let best: HistoryPoint | ForecastPoint | null = null
    let bestDiff = Infinity
    for (const point of points) {
      const diff = Math.abs(new Date(point.timestamp).getTime() - timeMs)
      if (diff < bestDiff) {
        best = point
        bestDiff = diff
      }
    }
    return bestDiff <= POINT_MATCH_TOLERANCE_MS ? best : null
  }

  zoneValueAtTime(zone: string, timeMs: number): number | null {
    return this.pointValue(this.pointAtTime(zone, timeMs))
  }
}

export const oko = new OkoState()
