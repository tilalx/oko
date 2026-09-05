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

/** Data is hourly-resolution, so a point and a queried time "match" when
 * they round to the same hour -- equivalent to the +-30min tolerance a
 * nearest-point scan would apply, but resolvable with a single map lookup.
 * See `pointIndex`. */
function hourKey(timeMs: number): number {
  return Math.round(timeMs / HOUR_MS) * HOUR_MS
}

interface PointIndex {
  /** Identities of the arrays the index was built from -- rebuilt when
   * either is replaced (see App.svelte's per-zone assignments). */
  history: unknown
  forecast: unknown
  byHour: Map<number, HistoryPoint | ForecastPoint>
}

function loadBool(key: string, fallback = false): boolean {
  try {
    const stored = localStorage.getItem(key)
    return stored === null ? fallback : stored === '1'
  } catch {
    return fallback
  }
}

/** Phone-sized viewport -- where the sidebar is an off-canvas drawer over
 * the map instead of a column beside it. Read once at startup for the
 * initial collapsed default; the layout itself is CSS-driven. */
function isNarrowViewport(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches
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
  exchangesData = $state<ExchangeEdge[]>([])

  flowLinesVisible = $state(false)
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
  /** Membership set for `allZones` -- the map hit-tests "does this zone
   * have data at all?" once per rendered zone layer per repaint, which an
   * array scan makes O(zones) instead of O(1). */
  allZonesSet = $derived(new Set(this.allZones))
  /** Latest observed CurrentBreakdown for the selected zone. */
  lastCurrent = $state<CurrentBreakdown | null>(null)

  /** URL hash (no '#'), e.g. 'docs' -- the app's only route besides the map. */
  route = $state(typeof window !== 'undefined' ? window.location.hash.slice(1) : '')

  colorblindPalette = $state(loadBool('oko-colorblind'))
  use24h = $state(loadBool('oko-24h') ?? detectHourCycle())
  /** Collapsed to a rail on desktop, fully off-canvas on phones -- so it
   * defaults to collapsed there rather than covering the map on load. */
  sidebarCollapsed = $state(loadBool('oko-sidebar-collapsed', isNarrowViewport()))
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

  /** Per-zone hour -> point lookup, built on first use and rebuilt only
   * when that zone's history/forecast array is replaced. Plain (not
   * `$state`) -- it's a derived cache of data that is already reactive,
   * and nothing should re-render because a lookup table got filled in.
   *
   * Parsing each point's ISO timestamp is the single most expensive thing
   * `pointAtTime` used to do, and the map repaints every zone on every
   * scrub frame -- doing it once per data load instead of once per lookup
   * is what keeps a repaint inside a frame budget. */
  private pointIndexes = new Map<string, PointIndex>()

  private pointIndex(zone: string): Map<number, HistoryPoint | ForecastPoint> {
    const history = this.zoneHistory[zone]
    const forecast = this.zoneForecasts[zone]
    const cached = this.pointIndexes.get(zone)
    if (cached && cached.history === history && cached.forecast === forecast) return cached.byHour

    const byHour = new Map<number, HistoryPoint | ForecastPoint>()
    for (const point of [...(history || []), ...(forecast || [])]) {
      const time = new Date(point.timestamp).getTime()
      if (Number.isNaN(time)) continue
      const key = hourKey(time)
      // Two points landing in one hour bucket (sub-hourly or duplicated
      // data): keep whichever sits closer to the hour itself.
      const existing = byHour.get(key)
      if (existing && Math.abs(new Date(existing.timestamp).getTime() - key) <= Math.abs(time - key)) continue
      byHour.set(key, point)
    }
    this.pointIndexes.set(zone, { history, forecast, byHour })
    return byHour
  }

  /** The unified point at `timeMs`'s hour, or `null` if the zone has none
   * -- an absolute timestamp is meaningful identically for every zone, so
   * (unlike an index) this needs no per-zone re-anchoring when the
   * selected zone changes. */
  pointAtTime(zone: string, timeMs: number): HistoryPoint | ForecastPoint | null {
    return this.pointIndex(zone).get(hourKey(timeMs)) ?? null
  }

  zoneValueAtTime(zone: string, timeMs: number): number | null {
    return this.pointValue(this.pointAtTime(zone, timeMs))
  }
}

export const oko = new OkoState()
