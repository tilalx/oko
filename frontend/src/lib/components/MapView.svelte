<script lang="ts">
  import L from 'leaflet'
  import 'leaflet/dist/leaflet.css'
  import { oko } from '$lib/state.svelte'
  import { colorForIntensity, rgbForIntensity } from '$lib/color'
  import { countryFlagEmoji } from '$lib/format'
  import { CATEGORY_META, ZONE_NAMES } from '$lib/constants'
  import { computeZoneGeometry, flowBearingDegrees, makeBorderPointFinder } from '$lib/geo'
  import { getZonesGeoJson } from '$lib/api'

  let { onZoneClick }: { onZoneClick: (zone: string) => void } = $props()

  let mapEl: HTMLDivElement
  let map: L.Map
  let geoLayer: L.GeoJSON
  let flowLineLayers: L.Marker[] = []
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
    const name = ZONE_NAMES[zone] || zone
    const header = `<b>${countryFlagEmoji(zone)} ${zone}</b><br>${name}<br>${
      value != null ? Math.round(value) + ' gCO2eq/kWh' : 'no data yet'
    }`
    const current = oko.zoneCurrent[zone]
    if (!current) return header
    const dominant = dominantSource(current.power_breakdown_percent)
    const meta = dominant ? CATEGORY_META[dominant.category] : null
    return (
      header +
      `<div class="hover-metrics">
        <span>${dominant ? `${meta ? meta.icon : ''} ${Math.round(dominant.pct)}% ${dominant.category}` : '—'}</span>
        <span>Carbon-free <b>${Math.round(current.fossil_free_percent)}%</b></span>
        <span>Renewable <b>${Math.round(current.renewable_percent)}%</b></span>
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

  //: How long one full tail-to-apex pulse wave takes -- kept short enough
  //: to visibly read as "flow", not so short it's just flickering.
  const CHEVRON_PULSE_SECONDS = 1.2

  function buildChevronIcon(count: number, bearingDeg: number, zoomScale: number): L.DivIcon {
    const unscaledHeight = 8 + (count - 1) * CHEVRON_STEP
    const width = 16 * zoomScale
    const height = unscaledHeight * zoomScale
    // Outline pass stays static (it's just contrast backing); only the
    // white pass pulses. i=0 is the leading (apex) chevron, i=count-1 the
    // trailing one -- staggering each one's animation-delay so the
    // trailing chevron brightens first and the highlight travels forward
    // toward the apex reads as motion in the flow's direction.
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
      html: `<svg width="${width}" height="${height}" viewBox="0 0 16 ${unscaledHeight}" style="transform:rotate(${bearingDeg}deg)" fill="none" stroke-linecap="round" stroke-linejoin="round">
        <g stroke="#1a1a1a" stroke-width="4.2" stroke-opacity="0.55">${outline}</g>
        <g stroke="#ffffff" stroke-width="2.2">${highlighted}</g>
      </svg>`,
      iconSize: [width, height],
      iconAnchor: [width / 2, height / 2],
    })
  }

  function clearFlowLines() {
    if (!map) return
    for (const layer of flowLineLayers) map.removeLayer(layer)
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
      const bearing = flowBearingDegrees(exporter, importer)
      const count = 1 + Math.round(scale * 2)

      const marker = L.marker(border, { interactive: false, icon: buildChevronIcon(count, bearing, zoomScale) }).addTo(map)
      flowLineLayers.push(marker)
    }
  }

  export async function init() {
    map = L.map(mapEl, { scrollWheelZoom: true, zoomControl: false }).setView([52, 12], 4)
    L.control.zoom({ position: 'topright' }).addTo(map)
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
    // Re-render on any input that changes a zone's fill/tooltip or the
    // flow chevrons -- horizonIndex, selectedZone, activeLayer,
    // colorblindPalette (via activeIntensityStops), exchangesData.
    void oko.horizonIndex
    void oko.selectedZone
    void oko.activeLayer
    void oko.activeIntensityStops
    void oko.exchangesData
    repaintMap()
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
  :global(#app .leaflet-control-zoom) {
    border: none;
    margin-top: 0.25rem !important;
  }
  :global(#app .leaflet-control-zoom a) {
    background: var(--card);
    color: var(--foreground);
    border-color: var(--border);
    width: 30px;
    height: 30px;
    line-height: 30px;
  }
  :global(#app .leaflet-control-zoom a:hover) {
    background: #24271f;
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
    pointer-events: none;
  }
  :global(.flow-arrow-icon .chevron-pulse) {
    animation: oko-chevron-pulse 1.2s linear infinite;
  }
  @keyframes oko-chevron-pulse {
    0%,
    100% {
      opacity: 0.3;
    }
    50% {
      opacity: 1;
    }
  }
</style>
