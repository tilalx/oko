<script lang="ts">
  import { onMount } from 'svelte'
  import { oko } from '$lib/state.svelte'
  import * as api from '$lib/api'
  import { AUTO_REFRESH_INTERVAL_MS, HISTORY_SCRUB_HOURS } from '$lib/constants'
  import Sidebar from '$lib/components/Sidebar.svelte'
  import MapView from '$lib/components/MapView.svelte'
  import TopBadge from '$lib/components/TopBadge.svelte'
  import MapActions from '$lib/components/MapActions.svelte'
  import MapLegend from '$lib/components/MapLegend.svelte'
  import ZoneCard from '$lib/components/ZoneCard.svelte'
  import Timebar from '$lib/components/Timebar.svelte'
  import ApiDocs from './ApiDocs.svelte'
  import type { ForecastPayload } from '$lib/api'

  // Hash-based route -- '#docs' shows the API reference in place of the
  // map, everything else (including no hash) shows the map. No router
  // lib needed for two routes; oko.route lives in shared state so
  // Sidebar can highlight the active nav item too.
  onMount(() => {
    const onHashChange = () => (oko.route = window.location.hash.slice(1))
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  })

  let mapView: MapView
  let status = $state('Loading forecast…')
  let payload = $state<ForecastPayload | null>(null)

  async function fetchAllForecasts(zones: string[]) {
    const [forecastResults, historyResults] = await Promise.all([
      Promise.allSettled(zones.map((zone) => api.getForecast(zone))),
      Promise.allSettled(zones.map((zone) => api.getHistory(zone, HISTORY_SCRUB_HOURS))),
    ])
    zones.forEach((zone, i) => {
      const result = forecastResults[i]
      const fp = result.status === 'fulfilled' ? result.value.payload : null
      oko.zoneForecasts[zone] = fp?.forecast || []
      oko.zoneCurrent[zone] = fp?.current || null
    })
    zones.forEach((zone, i) => {
      const result = historyResults[i]
      oko.zoneHistory[zone] = (result.status === 'fulfilled' && result.value) || []
    })
  }

  /** The export's 'current' block carries the latest observed hour's power
   * mix but no intensity scalar -- the forecast model predicts a single
   * value, not a per-source mix, so the observed *intensity* only exists
   * in the raw history table. Re-fetch it fresh for the zone being
   * (re-)selected -- rather than trusting the once-per-pageload bulk
   * prefetch in fetchAllForecasts -- so a long-lived tab still shows a
   * live "now" value, and refresh zoneHistory[zone] in the same call so
   * the unified timeline's history portion for this zone stays in sync
   * too. */
  async function fetchLatestObserved(zone: string) {
    const points = await api.getHistory(zone, HISTORY_SCRUB_HOURS)
    if (points.length) {
      oko.zoneHistory[zone] = points
      return points[points.length - 1]
    }
    const cached = oko.zoneHistory[zone]
    return cached && cached.length ? cached[cached.length - 1] : null
  }

  async function loadForecast(zone: string) {
    status = 'Loading forecast…'
    const { payload: fp, status: httpStatus } = await api.getForecast(zone)
    if (!fp) {
      status =
        httpStatus === 503
          ? "No forecast available yet for this zone (bootstrapping, or the last run couldn't reach its data sources)."
          : httpStatus === 404
            ? 'Unknown zone.'
            : `Failed to load forecast (HTTP ${httpStatus}).`
      payload = null
      return
    }
    await fetchLatestObserved(zone)
    oko.zoneForecasts[zone] = fp.forecast || []
    oko.zoneCurrent[zone] = fp.current || null
    // fetchLatestObserved may have changed historyLength(zone) by a point
    // or two since setSelectedZone's earlier estimate -- only resnap to
    // "now" against the now-fresh data if the user is still pinned to
    // live; a user who scrubbed away from "now" keeps their position.
    if (oko.horizonAtNow) {
      oko.horizonIndex = oko.nowSeamIndex(zone)
    } else {
      const maxIndex = Math.max(0, oko.unifiedPoints(zone).length - 1)
      oko.horizonIndex = Math.min(oko.horizonIndex, maxIndex)
    }
    oko.lastCurrent = fp.current
    payload = fp
    status = fp.forecast?.length
      ? `Updated ${new Date(fp.generated_at).toLocaleString()} · model ${fp.model_version}`
      : 'Forecast is empty.'
  }

  function setSelectedZone(zone: string) {
    const prevZone = oko.selectedZone
    const wasAtNow = oko.horizonAtNow
    // Preserve the scrub position (as an offset from "now") across zone
    // switches instead of snapping back to live -- a user scrubbing the
    // timeline shouldn't lose their place just by picking another country.
    const offset = oko.horizonIndex - oko.nowSeamIndex(prevZone)
    oko.selectedZone = zone
    if (wasAtNow) {
      oko.horizonIndex = oko.nowSeamIndex(zone)
    } else {
      const maxIndex = Math.max(0, oko.unifiedPoints(zone).length - 1)
      oko.horizonIndex = Math.min(maxIndex, Math.max(0, oko.nowSeamIndex(zone) + offset))
    }
    oko.cardVisible = true
    loadForecast(zone)
  }

  async function refreshAllZoneData() {
    await fetchAllForecasts(oko.allZones)
    oko.exchangesData = await api.getExchanges()
    if (oko.horizonAtNow) oko.horizonIndex = oko.nowSeamIndex(oko.selectedZone)
    oko.lastCurrent = oko.zoneCurrent[oko.selectedZone] || oko.lastCurrent
  }

  onMount(() => {
    let interval: ReturnType<typeof setInterval>
    ;(async () => {
      oko.allZones = await api.getZones()
      await Promise.all([fetchAllForecasts(oko.allZones), api.getExchanges().then((e) => (oko.exchangesData = e))])
      // First-paint tooltips/fills anchored at "now", not at horizonIndex's
      // initial value of 0 -- otherwise every zone's tooltip and fill
      // color is anchored to the oldest history point until the user
      // drags the slider or switches zones.
      oko.horizonIndex = oko.nowSeamIndex(oko.selectedZone)
      oko.horizonAtNow = true
      await mapView.init()
      setSelectedZone('DE-LU')
      interval = setInterval(refreshAllZoneData, AUTO_REFRESH_INTERVAL_MS)
    })()
    return () => clearInterval(interval)
  })
</script>

<div class="flex h-screen w-screen" id="app">
  <Sidebar />
  <div class="relative flex-1">
    <MapView bind:this={mapView} onZoneClick={setSelectedZone} />
    <TopBadge />
    <MapActions />
    <MapLegend />
    <ZoneCard {status} {payload} onSelectZone={setSelectedZone} />
    <Timebar />
    {#if oko.route === 'docs'}
      <div class="absolute inset-0 z-[900]">
        <ApiDocs />
      </div>
    {/if}
  </div>
</div>
