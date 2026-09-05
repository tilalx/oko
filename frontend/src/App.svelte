<script lang="ts">
  import { onMount } from 'svelte'
  import { oko } from '$lib/state.svelte'
  import * as api from '$lib/api'
  import { AUTO_REFRESH_INTERVAL_MS, HISTORY_SCRUB_HOURS } from '$lib/constants'
  import { t } from '$lib/i18n'
  import { formatFullDateTime } from '$lib/format'
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

  let mapView: MapView | undefined = $state()
  let status = $state(t('app.loading'))
  let payload = $state<ForecastPayload | null>(null)

  /** One request for every zone's forecast + history, instead of 2*N
   * individual per-zone requests -- see GET /api/bulk. Browsers cap
   * concurrent connections per origin, so with dozens of zones most of
   * those individual requests just queue behind each other. */
  async function fetchAllForecasts(zones: string[]) {
    const zoneData = await api.getBulkZoneData(HISTORY_SCRUB_HOURS)
    for (const zone of zones) {
      const entry = zoneData[zone]
      oko.zoneForecasts[zone] = entry?.forecast?.forecast || []
      oko.zoneCurrent[zone] = entry?.forecast?.current || null
      oko.zoneHistory[zone] = entry?.history || []
    }
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
    status = t('app.loading')
    const { payload: fp, status: httpStatus } = await api.getForecast(zone)
    if (!fp) {
      status =
        httpStatus === 503
          ? t('app.noForecastAvailable')
          : httpStatus === 404
            ? t('app.unknownZone')
            : t('app.fetchError', { statusCode: httpStatus })
      payload = null
      return
    }
    await fetchLatestObserved(zone)
    oko.zoneForecasts[zone] = fp.forecast || []
    oko.zoneCurrent[zone] = fp.current || null
    // Only resnap to live "now" if the user is still pinned to it; a user
    // who scrubbed away from "now" keeps their absolute scrub position --
    // it's a timestamp, meaningful unchanged across zones and refreshes.
    if (oko.horizonAtNow) {
      oko.horizonTime = oko.nowHourMs
    }
    oko.lastCurrent = fp.current
    payload = fp
    status = fp.forecast?.length
      ? `Updated ${formatFullDateTime(new Date(fp.generated_at), oko.use24h, oko.locale)} · model ${fp.model_version}`
      : t('app.forecastEmpty')
  }

  function setSelectedZone(zone: string) {
    // horizonTime is an absolute timestamp, meaningful identically across
    // every zone -- unlike the old array-index scrub position, switching
    // zones needs no reconciliation at all. A user's scrub position (or
    // "at now" pin) survives the switch unchanged.
    oko.selectedZone = zone
    if (oko.horizonAtNow) oko.horizonTime = oko.nowHourMs
    oko.cardVisible = true
    loadForecast(zone)
  }

  async function refreshAllZoneData() {
    await fetchAllForecasts(oko.allZones)
    oko.exchangesData = await api.getExchanges()
    if (oko.horizonAtNow) oko.horizonTime = oko.nowHourMs
    oko.lastCurrent = oko.zoneCurrent[oko.selectedZone] || oko.lastCurrent
  }

  onMount(() => {
    let interval: ReturnType<typeof setInterval>
    let nowTicker: ReturnType<typeof setInterval>
    ;(async () => {
      // Map init (fetching/parsing zones.geojson) has no dependency on
      // zone/forecast data -- run it concurrently with the zone list and
      // exchanges fetch instead of serializing it after the forecast
      // round trip.
      const [zones] = await Promise.all([
        api.getZones(),
        mapView?.init(),
        api.getExchanges().then((e) => {
          oko.exchangesData = e
        }),
      ])
      oko.allZones = zones
      await fetchAllForecasts(zones)
      // First-paint tooltips/fills anchored at "now", not at horizonTime's
      // initial (page-load) value -- otherwise every zone's tooltip and
      // fill color is anchored to whatever moment the tab was opened until
      // the user drags the slider or switches zones.
      oko.horizonTime = oko.nowHourMs
      oko.horizonAtNow = true
      setSelectedZone('DE-LU')
      interval = setInterval(refreshAllZoneData, AUTO_REFRESH_INTERVAL_MS)
      nowTicker = setInterval(() => (oko.nowMs = Date.now()), 60_000)
    })()
    return () => {
      clearInterval(interval)
      clearInterval(nowTicker)
    }
  })
</script>

<div class="flex h-screen w-screen" id="app">
  <Sidebar />
  <div class="relative flex-1">
    <MapView bind:this={mapView} onZoneClick={setSelectedZone} />
    <div class="absolute top-[1.1rem] right-[1.1rem] z-[500] flex flex-col items-end gap-[0.6rem]">
      <TopBadge />
      <MapActions {mapView} />
    </div>
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
