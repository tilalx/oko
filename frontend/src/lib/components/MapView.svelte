<script lang="ts">
  import L from 'leaflet'
  import 'leaflet/dist/leaflet.css'
  import { oko } from '$lib/state.svelte'
  import { colorForIntensity, rgbForIntensity } from '$lib/color'
  import { CATEGORY_META, NO_DATA_COLOR } from '$lib/constants'
  import { t, formatNumber } from '$lib/i18n'
  import { computeZoneGeometry, flowBearingDegrees, makeBorderPointFinder } from '$lib/geo'
  import { getZonesGeoJson, getWorldCountriesGeoJson, type ExchangeEdge } from '$lib/api'
  import { formatFullDateTime } from '$lib/format'

  let { onZoneClick }: { onZoneClick: (zone: string) => void } = $props()

  //: The map has no edges -- panning past +/-180deg longitude should show
  //: the same borders again, not empty space. Leaflet's own worldCopyJump
  //: only snaps the *view* back into the primary copy; it doesn't draw
  //: our vector layers (borders, flow arrows) at the repeated copies, so
  //: those are drawn explicitly at each of these longitude offsets.
  //: Three copies comfortably cover any reasonable viewport width at
  //: MIN_ZOOM (see init) without a visible seam.
  const WORLD_COPY_OFFSETS = [-360, 0, 360]
  const MIN_ZOOM = 2
  const MAX_ZOOM = 10

  let mapEl: HTMLDivElement
  let map: L.Map
  let zoneLayers: Record<string, L.Path[]> = {}
  let previousSelectedZone: string | null = null
  /** `color` is the CSS variable value currently written onto the marker's
   * element -- see restyleFlowLines. `count`/`bearing` are kept so a zoom
   * can rebuild the icon at a new scale without redoing placement. */
  let flowLineLayers: {
    marker: L.Marker
    exporterZone: string
    count: number
    bearing: number
    color: string | null
  }[] = []
  let getBorderPoint: ReturnType<typeof makeBorderPointFinder> = () => null

  //: The rest-of-world backdrop is ~450 static paths (~32k vertices) that
  //: are never restyled, but as SVG they'd be that many DOM nodes for the
  //: browser to re-project on every zoom and hit-test on every mousemove.
  //: One canvas per map holds them all; the 144 data-zone paths stay on
  //: SVG, where per-path setStyle and focus handling are cheaper.
  let worldRenderer: L.Canvas

  //: Draw in the map's initial setView zoom -- see flowIconZoomScale below.
  const FLOW_ICON_BASE_ZOOM = 4
  const CHEVRON_PATH = 'M2,7 L8,1 L14,7'
  const CHEVRON_STEP = 6

  function dominantSource(breakdown: Record<string, number> | undefined | null) {
    if (!breakdown) return null
    let best: { category: string; pct: number } | null = null
    for (const [category, pct] of Object.entries(breakdown)) {
      if (!best || pct > best.pct) best = { category, pct }
    }
    return best
  }

  /** A zone the backend has never returned data for (absent from
   * `/zones`) is permanently inert -- distinct from a zone that merely
   * has no value *at this instant* (colorForIntensity's own null
   * fallback already handles that case). Rendered dim and non-selectable
   * regardless of horizonTime/selection. */
  function hasData(zone: string): boolean {
    return oko.allZonesSet.has(zone)
  }

  function zoneStyle(zone: string): L.PathOptions {
    if (!hasData(zone)) {
      return { fillColor: NO_DATA_COLOR, fillOpacity: 0.35, color: 'rgba(255,255,255,0.12)', weight: 1 }
    }
    const selected = zone === oko.selectedZone
    return {
      fillColor: colorForIntensity(
        oko.zoneValueAtTime(zone, oko.horizonTime),
        oko.activeIntensityStops
      ),
      fillOpacity: selected ? 0.95 : 0.75,
      color: selected ? '#eef1ec' : 'rgba(255,255,255,0.35)',
      weight: selected ? 2.5 : 1,
    }
  }

  function zoneTooltip(zone: string): string {
    if (!hasData(zone)) return `<b>${zone}</b><br>${t('mapView.noData')}`
    const value = oko.zoneValueAtTime(zone, oko.horizonTime)
    const name = t(`zones.${zone}`) || zone
    const header = `<b>${zone}</b><br>${name}<br><span class="oko-num">${
      value != null ? formatNumber(Math.round(value), { locale: oko.locale }) + ' gCO2eq/kWh' : t('mapView.noData')
    }</span>`
    const current = oko.zoneCurrent[zone]
    if (!current) return header
    const dominant = dominantSource(current.power_breakdown_percent)
    const meta = dominant ? CATEGORY_META[dominant.category] : null
    return (
      header +
      `<div class="hover-metrics">
        <span>${dominant ? `${meta ? meta.icon : ''} <span class="oko-num">${formatNumber(Math.round(dominant.pct), { locale: oko.locale })}%</span> ${dominant.category}` : '—'}</span>
        <span>${t('nowPanel.carbonFree')} <b class="oko-num">${formatNumber(Math.round(current.fossil_free_percent), { locale: oko.locale })}%</b></span>
        <span>${t('nowPanel.renewable')} <b class="oko-num">${formatNumber(Math.round(current.renewable_percent), { locale: oko.locale })}%</b></span>
      </div>`
    )
  }

  /** Re-runs a layer's bound tooltip *content function* if -- and only if
   * -- that tooltip is currently open. Leaflet evaluates function content
   * when a tooltip opens and (even when `sticky`) never again while it
   * stays open, so scrubbing the timeline under a held-open tooltip would
   * otherwise leave it showing the value from whenever it was opened.
   * `update()` no-ops on a tooltip that isn't on the map, so this costs
   * nothing for the layers nobody is hovering. */
  function refreshOpenTooltip(layer: L.Layer) {
    layer.getTooltip()?.update()
  }

  //: Tooltip content is bound as a *function* (see init), so it's built
  //: only for a tooltip that's actually open -- a repaint touches fills,
  //: not the 144 tooltips of which at most one is on screen. The style is
  //: computed once per zone and shared by that zone's world copies.
  function repaintMap() {
    for (const zone in zoneLayers) {
      const style = zoneStyle(zone)
      for (const layer of zoneLayers[zone]) {
        layer.setStyle(style)
        refreshOpenTooltip(layer)
      }
    }
  }

  function flowIconZoomScale(): number {
    if (!map) return 1
    return Math.min(2.5, Math.max(0.6, Math.pow(1.25, map.getZoom() - FLOW_ICON_BASE_ZOOM)))
  }

  //: How long one full tail-to-apex pulse wave takes -- must match the
  //: `oko-chevron-pulse` keyframe duration below so the per-chevron
  //: stagger lines up with the actual animation cycle.
  const CHEVRON_PULSE_SECONDS = 1.8

  //: Minimum hoverable hit box, regardless of the visual arrow's own
  //: (often much thinner) size -- precisely hovering a few px of dashed
  //: line on a real map is unreasonable otherwise.
  const FLOW_ICON_MIN_HIT = 26

  /** The chevron icon's own visual size and its (possibly larger, per
   * FLOW_ICON_MIN_HIT) hoverable box. */
  function chevronIconBox(count: number, zoomScale: number): { width: number; height: number; hitW: number; hitH: number } {
    const unscaledHeight = 8 + (count - 1) * CHEVRON_STEP
    const width = 16 * zoomScale
    const height = unscaledHeight * zoomScale
    return { width, height, hitW: Math.max(FLOW_ICON_MIN_HIT, width), hitH: Math.max(FLOW_ICON_MIN_HIT, height) }
  }

  /** The arrow's carbon-intensity color is *not* baked into the markup --
   * it's the only part that changes as the timeline is scrubbed, and it
   * rides a CSS variable so a repaint can rewrite it on the existing
   * element (see restyleFlowLines) instead of rebuilding the icon. That
   * also lets the chevron pulse animation run continuously rather than
   * restarting every frame. */
  function buildChevronIcon(count: number, bearingDeg: number, zoomScale: number): L.DivIcon {
    const unscaledHeight = 8 + (count - 1) * CHEVRON_STEP
    const { width, height, hitW, hitH } = chevronIconBox(count, zoomScale)
    // Outline pass stays static (it's just contrast backing so the arrow
    // reads over any basemap brightness); only the colored pass pulses,
    // and gently -- a calm opacity breathe, not a bright flash. i=0 is
    // the leading (apex) chevron, i=count-1 the trailing one --
    // staggering each one's animation-delay so the trailing chevron
    // brightens first and the highlight travels forward toward the apex
    // reads as motion in the flow's direction. Stroke widths are 25%
    // thinner than the original 4.2/2.2.
    const outline = Array.from(
      { length: count },
      (_, i) => `<path d="${CHEVRON_PATH}" transform="translate(0,${i * CHEVRON_STEP})" />`
    ).join('')
    const pulseStep = CHEVRON_PULSE_SECONDS / (2 * count)
    const highlighted = Array.from({ length: count }, (_, i) => {
      const delay = (count - 1 - i) * pulseStep
      return `<path d="${CHEVRON_PATH}" transform="translate(0,${i * CHEVRON_STEP})" class="chevron-pulse" style="animation-delay:-${delay}s" />`
    }).join('')
    return L.divIcon({
      className: 'flow-arrow-icon',
      html: `<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%">
        <svg width="${width}" height="${height}" viewBox="0 0 16 ${unscaledHeight}" style="transform:rotate(${bearingDeg}deg)" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <g stroke="#0c0d0c" stroke-width="3.15" stroke-opacity="0.5">${outline}</g>
          <g stroke="var(--oko-flow-color)" stroke-width="1.65">${highlighted}</g>
        </svg>
      </div>`,
      iconSize: [hitW, hitH],
      iconAnchor: [hitW / 2, hitH / 2],
    })
  }

  /** Same recipe as `zoneTooltip` -- a border's latest flow, direction,
   * and the exporting zone's own carbon intensity (swatch-colored with
   * the same scale the zone fill uses). */
  function flowTooltip(edge: ExchangeEdge, exporter: string, importer: string): string {
    const magnitudeGw = formatNumber(Math.abs(edge.net_flow_mw) / 1000, { locale: oko.locale, minimumFractionDigits: 2, maximumFractionDigits: 2 })
    const exportValue = oko.zoneValueAtTime(exporter, oko.horizonTime)
    const swatchColor = colorForIntensity(exportValue, oko.activeIntensityStops)
    const exporterName = t(`zones.${exporter}`) || exporter
    const importerName = t(`zones.${importer}`) || importer
    return `<b>${exporterName} → ${importerName}</b><br>
      ${formatFullDateTime(new Date(edge.timestamp), oko.use24h, oko.locale)}
      <div class="flow-metrics">
        <span>${t('mapView.crossBorderExport')}: <b class="oko-num">${magnitudeGw} GW</b></span>
        <span>${t('mapView.carbonIntensityOfExport')}:
          <span class="swatch" style="background:${swatchColor}"></span>
          <b class="oko-num">${exportValue != null ? formatNumber(Math.round(exportValue), { locale: oko.locale }) : '—'} gCO2eq/kWh</b>
        </span>
      </div>`
  }

  function clearFlowLines() {
    if (!map) return
    for (const { marker } of flowLineLayers) map.removeLayer(marker)
    flowLineLayers = []
  }

  /** Places one marker per border crossing, at that crossing's own border
   * anchor -- and nowhere else. An arrow is a fact about a border, so it
   * belongs on that border at every zoom; it never slides sideways to make
   * room for a neighbor.
   *
   * (An earlier version resolved overlaps by displacing icons in pixel
   * space. Any such displacement has to be converted back into a lat/lng
   * to be drawn, which bakes a pixel distance measured at one zoom into
   * the geography -- so the arrow both sat off its border and appeared to
   * wander as the view changed. Crowding is now handled purely by the icon
   * size shrinking as you zoom out; see resizeFlowIcons.)
   *
   * Nothing here depends on the scrub position or on the current view: an
   * `ExchangeEdge` is a single latest-snapshot reading, so magnitude and
   * direction are fixed. Runs only on new exchange data and on the
   * visibility toggle. */
  function layoutFlowLines() {
    clearFlowLines()
    if (!oko.flowLinesVisible || !map) return
    const maxMagnitude = oko.exchangesData.reduce((max, e) => Math.max(max, Math.abs(e.net_flow_mw)), 0) || 1
    const zoomScale = flowIconZoomScale()

    for (const edge of oko.exchangesData) {
      const from = oko.zoneCentroids[edge.zone_from]
      const to = oko.zoneCentroids[edge.zone_to]
      const anchor = getBorderPoint(edge.zone_from, edge.zone_to)
      if (!from || !to || !anchor || edge.net_flow_mw === 0) continue

      const scale = Math.abs(edge.net_flow_mw) / maxMagnitude
      const forward = edge.net_flow_mw > 0
      const exporter = forward ? from : to
      const importer = forward ? to : from
      const exporterZone = forward ? edge.zone_from : edge.zone_to
      const importerZone = forward ? edge.zone_to : edge.zone_from
      const bearing = flowBearingDegrees(exporter, importer)
      const count = 1 + Math.round(scale * 2)
      const [lat, lng] = anchor

      for (const offset of WORLD_COPY_OFFSETS) {
        const marker = L.marker([lat, lng + offset], {
          icon: buildChevronIcon(count, bearing, zoomScale),
        })
          // Built on hover, not now -- the tooltip quotes the exporting
          // zone's intensity at the *current* scrub position, and this
          // marker now outlives many scrub positions.
          .bindTooltip(() => flowTooltip(edge, exporterZone, importerZone), {
            sticky: true,
            className: 'flow-tooltip',
          })
          .addTo(map)
        flowLineLayers.push({ marker, exporterZone, count, bearing, color: null })
      }
    }

    restyleFlowLines()
  }

  /** Zoom scales the chevrons but must not move them: rebuild each icon at
   * the new scale in place, leaving the marker's position alone. setIcon
   * replaces the element, so the color variable has to be re-applied --
   * clearing `color` makes restyleFlowLines do exactly that. */
  function resizeFlowIcons() {
    const zoomScale = flowIconZoomScale()
    for (const entry of flowLineLayers) {
      entry.marker.setIcon(buildChevronIcon(entry.count, entry.bearing, zoomScale))
      entry.color = null
    }
    restyleFlowLines()
  }

  /** The scrub-time half of the flow arrows: rewrite each marker's color
   * variable in place, skipping the ones whose color didn't change. */
  function restyleFlowLines() {
    for (const entry of flowLineLayers) {
      const color = colorForIntensity(
        oko.zoneValueAtTime(entry.exporterZone, oko.horizonTime),
        oko.activeIntensityStops
      )
      refreshOpenTooltip(entry.marker)
      if (color === entry.color) continue
      const el = entry.marker.getElement()
      // Not in the DOM yet -- leave `color` unset so the next repaint retries.
      if (!el) continue
      el.style.setProperty('--oko-flow-color', color)
      entry.color = color
    }
  }

  // Zoom is triggered from MapActions' icon-button column instead of
  // Leaflet's native topright zoom control -- that control used to sit
  // in the same corner as TopBadge with no shared layout awareness,
  // overlapping it. One button stack, one icon language.
  export function zoomIn() {
    map?.zoomIn()
  }
  export function zoomOut() {
    map?.zoomOut()
  }

  /** Country codes already covered by a data zone -- e.g. 'DK-DK1' covers
   * 'DK', 'GB-NIR' covers 'GB'. 'DE-LU' is the one zone code that's itself
   * two country codes (a merged German-Luxembourg bidding zone). Used to
   * skip drawing a redundant rest-of-world border under a zone we already
   * render in detail. */
  function coveredCountryCodes(zoneFeatures: any[]): Set<string> {
    const codes = new Set<string>()
    for (const feature of zoneFeatures) {
      const zone = feature.properties.zone as string
      codes.add(zone.split('-')[0])
      if (zone === 'DE-LU') codes.add('LU')
    }
    return codes
  }

  const restOfWorldStyle: L.PathOptions = {
    fillColor: NO_DATA_COLOR,
    fillOpacity: 0.18,
    color: 'rgba(255,255,255,0.08)',
    weight: 1,
  }

  /** Deep-clones a FeatureCollection with every longitude shifted by
   * `offsetLng` -- draws a second/third copy of the same borders at the
   * neighboring world-wrap positions (see WORLD_COPY_OFFSETS). */
  function shiftGeoJsonLng(geojson: any, offsetLng: number): any {
    if (offsetLng === 0) return geojson
    const shiftCoords = (coords: any): any =>
      typeof coords[0] === 'number' ? [coords[0] + offsetLng, coords[1]] : coords.map(shiftCoords)
    return {
      ...geojson,
      features: geojson.features.map((feature: any) => ({
        ...feature,
        geometry: { ...feature.geometry, coordinates: shiftCoords(feature.geometry.coordinates) },
      })),
    }
  }

  export async function init() {
    map = L.map(mapEl, {
      scrollWheelZoom: true,
      zoomControl: false,
      worldCopyJump: true,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
    }).setView([52, 12], 4)
    // Placement is view-independent (see layoutPoint), so zoom only needs
    // to rescale the icons -- and pan needs nothing at all.
    map.on('zoomend', resizeFlowIcons)
    worldRenderer = L.canvas({ padding: 0.5 })

    const [geojson, worldGeojson] = await Promise.all([getZonesGeoJson(), getWorldCountriesGeoJson()])

    const covered = coveredCountryCodes(geojson.features)
    const restOfWorld = {
      ...worldGeojson,
      features: worldGeojson.features.filter((f: any) => !covered.has(f.properties.iso2)),
    }

    const { centroids, boundaries } = computeZoneGeometry(geojson)
    oko.zoneCentroids = centroids
    getBorderPoint = makeBorderPointFinder(boundaries)

    for (const offset of WORLD_COPY_OFFSETS) {
      L.geoJSON(shiftGeoJsonLng(restOfWorld, offset), {
        renderer: worldRenderer,
        style: () => restOfWorldStyle,
        onEachFeature: (feature, layer) => {
          layer.bindTooltip(`<b>${feature.properties.name}</b><br>${t('mapView.noData')}`, {
            sticky: true,
            className: 'zone-tooltip',
          })
        },
      }).addTo(map)

      L.geoJSON(shiftGeoJsonLng(geojson, offset), {
        style: (feature: any) => zoneStyle(feature.properties.zone),
        onEachFeature: (feature, layer) => {
          const zone = feature.properties.zone
          ;(zoneLayers[zone] ??= []).push(layer as L.Path)
          // Bound as a function so it's rebuilt on open, reflecting the
          // scrub position at hover time -- a repaint never has to walk
          // every layer writing tooltip HTML nobody is looking at.
          layer.bindTooltip(() => zoneTooltip(zone), { sticky: true, className: 'zone-tooltip' })
          // Gate inside the handler, not around the binding: init() runs
          // concurrently with the /zones fetch (see App.svelte), so
          // `allZones` is still empty here and testing hasData() now would
          // leave every zone permanently unclickable. At click time it's
          // populated.
          layer.on('click', () => {
            if (hasData(zone)) onZoneClick(zone)
          })
        },
      }).addTo(map)
    }

    layoutFlowLines()
  }

  // Dragging the Timebar slider fires many horizonTime updates per second.
  // Coalesce to at most one repaint per animation frame; repaintMap and
  // restyleFlowLines always read the *current* oko state when they finally
  // run, so no intermediate scrub position is lost -- only the redundant
  // in-between repaints are skipped.
  let repaintScheduled = false
  function scheduleRepaint() {
    if (repaintScheduled) return
    repaintScheduled = true
    requestAnimationFrame(() => {
      repaintScheduled = false
      repaintMap()
      restyleFlowLines()
    })
  }

  $effect(() => {
    // Re-color on any input that changes a zone's fill or a flow arrow's
    // color (both keyed off a zone's own carbon intensity) -- horizonTime,
    // colorblindPalette (via activeIntensityStops), and allZones arriving
    // (which decides which zones are grayed-out/non-interactive). Arrow
    // *placement* doesn't depend on any of these; see layoutFlowLines.
    void oko.horizonTime
    void oko.activeIntensityStops
    void oko.allZones
    scheduleRepaint()
  })

  $effect(() => {
    // Selecting a zone only changes the border weight/color of the
    // previously- and newly-selected zone -- not every zone's fill, and
    // not the flow arrows (which don't depend on selection at all). Restyle
    // just those (at most two) zones instead of the full repaintMap().
    const zone = oko.selectedZone
    const previous = previousSelectedZone
    if (previous) {
      const style = zoneStyle(previous)
      for (const layer of zoneLayers[previous] || []) layer.setStyle(style)
    }
    const style = zoneStyle(zone)
    for (const layer of zoneLayers[zone] || []) layer.setStyle(style)
    previousSelectedZone = zone
  })

  $effect(() => {
    void oko.flowLinesVisible
    void oko.exchangesData
    layoutFlowLines()
  })
</script>

<div bind:this={mapEl} class="absolute inset-0 bg-[#05060a]"></div>

<style>
  /* Leaflet's own container div paints its default #ddd background over
     mapEl's -- there's no tile layer to cover it anymore, so it must be
     overridden explicitly or the map reads as a light gray box. */
  :global(#app .leaflet-container) {
    background: #05060a;
  }
  :global(#app .leaflet-control-attribution) {
    background: rgba(0, 0, 0, 0.55);
    color: var(--muted-foreground);
    font-size: 0.68rem;
  }
  :global(#app .leaflet-control-attribution a) {
    color: var(--foreground);
  }
  :global(#app .leaflet-tooltip.zone-tooltip) {
    background: var(--card);
    color: var(--foreground);
    border: 1px solid var(--border);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    font-size: 0.8rem;
  }
  :global(#app .leaflet-tooltip.zone-tooltip::before) {
    display: none;
  }
  :global(#app .leaflet-tooltip.zone-tooltip .hover-metrics) {
    display: flex;
    gap: 0.6rem;
    margin-top: 0.35rem;
    font-size: 0.74rem;
    color: var(--muted-foreground);
  }
  :global(#app .leaflet-tooltip.zone-tooltip .hover-metrics b) {
    color: var(--foreground);
  }
  :global(#app .leaflet-tooltip.flow-tooltip) {
    background: var(--card);
    color: var(--foreground);
    border: 1px solid var(--border);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    font-size: 0.8rem;
  }
  :global(#app .leaflet-tooltip.flow-tooltip::before) {
    display: none;
  }
  :global(#app .leaflet-tooltip.flow-tooltip .flow-metrics) {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    margin-top: 0.4rem;
    font-size: 0.76rem;
    color: var(--muted-foreground);
  }
  :global(#app .leaflet-tooltip.flow-tooltip .swatch) {
    display: inline-block;
    width: 0.55rem;
    height: 0.55rem;
    border-radius: 2px;
    margin: 0 0.15rem -0.05rem 0;
  }
  /* Leaflet gives clicked SVG paths a tabindex for keyboard access; the
     browser's default focus ring then draws a rectangle around the path's
     bounding box (not its actual shape) -- the zone's own white selection
     outline (zoneStyle's `weight`) already shows what's selected. */
  :global(#app .leaflet-interactive:focus) {
    outline: none;
  }
  :global(.flow-arrow-icon) {
    /* Overwritten per marker by restyleFlowLines; this is only the value
       used between the element entering the DOM and its first restyle. */
    --oko-flow-color: var(--foreground);
    color: var(--foreground);
    filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.6));
    cursor: pointer;
  }
  :global(.flow-arrow-icon .chevron-pulse) {
    animation: oko-chevron-pulse 1.8s ease-in-out infinite;
  }
  /* A calm breathe, not a bright flash -- opacity stays in a narrow
     band around visible rather than swinging near-transparent. */
  @keyframes oko-chevron-pulse {
    0%,
    100% {
      opacity: 0.65;
    }
    50% {
      opacity: 1;
    }
  }
</style>
