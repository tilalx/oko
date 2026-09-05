<script lang="ts">
  import L from 'leaflet'
  import 'leaflet/dist/leaflet.css'
  import { oko } from '$lib/state.svelte'
  import { colorForIntensity, rgbForIntensity } from '$lib/color'
  import { CATEGORY_META } from '$lib/constants'
  import { t, formatNumber } from '$lib/i18n'
  import { computeZoneGeometry, flowBearingDegrees, makeBorderPointFinder } from '$lib/geo'
  import { getZonesGeoJson, type ExchangeEdge } from '$lib/api'
  import { formatFullDateTime } from '$lib/format'

  let { onZoneClick }: { onZoneClick: (zone: string) => void } = $props()

  let mapEl: HTMLDivElement
  let map: L.Map
  let geoLayer: L.GeoJSON
  let flowLineLayers: { marker: L.Marker; edge: ExchangeEdge; exporterZone: string; importerZone: string }[] = []
  let getBorderPoint: ReturnType<typeof makeBorderPointFinder> = () => null

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

  function zoneStyle(zone: string): L.PathOptions {
    const selected = zone === oko.selectedZone
    return {
      fillColor: colorForIntensity(
        oko.zoneValueAtUnifiedIndex(zone, oko.horizonIndexForZone(zone)),
        oko.activeIntensityStops
      ),
      fillOpacity: selected ? 0.95 : 0.75,
      color: selected ? '#eef1ec' : 'rgba(255,255,255,0.35)',
      weight: selected ? 2.5 : 1,
    }
  }

  function zoneTooltip(zone: string): string {
    const value = oko.zoneValueAtUnifiedIndex(zone, oko.horizonIndexForZone(zone))
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

  function repaintMap() {
    if (!geoLayer) return
    geoLayer.eachLayer((layer: any) => {
      const zone = layer.feature.properties.zone
      layer.setStyle(zoneStyle(zone))
      layer.setTooltipContent(zoneTooltip(zone))
    })
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

  function buildChevronIcon(count: number, bearingDeg: number, zoomScale: number, color: string): L.DivIcon {
    const unscaledHeight = 8 + (count - 1) * CHEVRON_STEP
    const width = 16 * zoomScale
    const height = unscaledHeight * zoomScale
    const hitW = Math.max(FLOW_ICON_MIN_HIT, width)
    const hitH = Math.max(FLOW_ICON_MIN_HIT, height)
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
          <g stroke="${color}" stroke-width="1.65">${highlighted}</g>
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
    const exportValue = oko.zoneValueAtUnifiedIndex(exporter, oko.horizonIndexForZone(exporter))
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

  function drawFlowLines() {
    clearFlowLines()
    if (!oko.flowLinesVisible || !map) return
    const maxMagnitude = oko.exchangesData.reduce((max, e) => Math.max(max, Math.abs(e.net_flow_mw)), 0) || 1
    const zoomScale = flowIconZoomScale()
    for (const edge of oko.exchangesData) {
      const from = oko.zoneCentroids[edge.zone_from]
      const to = oko.zoneCentroids[edge.zone_to]
      const border = getBorderPoint(edge.zone_from, edge.zone_to)
      if (!from || !to || !border || edge.net_flow_mw === 0) continue

      const magnitude = Math.abs(edge.net_flow_mw)
      const scale = magnitude / maxMagnitude
      const forward = edge.net_flow_mw > 0
      const exporter = forward ? from : to
      const importer = forward ? to : from
      const exporterZone = forward ? edge.zone_from : edge.zone_to
      const importerZone = forward ? edge.zone_to : edge.zone_from
      const bearing = flowBearingDegrees(exporter, importer)
      const count = 1 + Math.round(scale * 2)
      const exportValue = oko.zoneValueAtUnifiedIndex(exporterZone, oko.horizonIndexForZone(exporterZone))
      const color = colorForIntensity(exportValue, oko.activeIntensityStops)

      const marker = L.marker(border, { icon: buildChevronIcon(count, bearing, zoomScale, color) })
        .bindTooltip(flowTooltip(edge, exporterZone, importerZone), { sticky: true, className: 'flow-tooltip' })
        .addTo(map)
      flowLineLayers.push({ marker, edge, exporterZone, importerZone })
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

  export async function init() {
    map = L.map(mapEl, { scrollWheelZoom: true, zoomControl: false }).setView([52, 12], 4)
    map.on('zoomend', drawFlowLines)
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 9, minZoom: 3, attribution: 'Tiles &copy; Esri &mdash; Esri, TomTom, FAO, NOAA, USGS' }
    ).addTo(map)

    const geojson = await getZonesGeoJson()
    const { centroids, boundaryPoints } = computeZoneGeometry(geojson)
    oko.zoneCentroids = centroids
    oko.zoneBoundaryPoints = boundaryPoints
    getBorderPoint = makeBorderPointFinder(boundaryPoints)

    geoLayer = L.geoJSON(geojson, {
      style: (feature: any) => zoneStyle(feature.properties.zone),
      onEachFeature: (feature, layer) => {
        const zone = feature.properties.zone
        layer.bindTooltip(zoneTooltip(zone), { sticky: true, className: 'zone-tooltip' })
        layer.on('click', () => onZoneClick(zone))
      },
    }).addTo(map)

    drawFlowLines()
  }

  $effect(() => {
    // Re-render on any input that changes a zone's fill/tooltip or a flow
    // arrow's color/tooltip (both keyed off the exporting zone's own
    // carbon intensity) -- horizonIndex, selectedZone, colorblindPalette
    // (via activeIntensityStops), exchangesData.
    void oko.horizonIndex
    void oko.selectedZone
    void oko.activeIntensityStops
    void oko.exchangesData
    repaintMap()
    drawFlowLines()
  })

  $effect(() => {
    void oko.flowLinesVisible
    void oko.exchangesData
    drawFlowLines()
  })

  $effect(() => {
    if (mapEl) mapEl.classList.toggle('tiles-light', oko.tilesLight)
  })
</script>

<div bind:this={mapEl} class="absolute inset-0 bg-[#05060a]"></div>

<style>
  :global(#app .leaflet-tile-pane) {
    filter: grayscale(1) invert(1) brightness(0.8) contrast(1.15);
    transition: filter 0.2s ease;
  }
  :global(#app .tiles-light .leaflet-tile-pane) {
    filter: none;
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
