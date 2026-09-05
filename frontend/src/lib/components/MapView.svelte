<script lang="ts">
  import L from 'leaflet'
  import 'leaflet/dist/leaflet.css'
  import { oko } from '$lib/state.svelte'
  import { colorForIntensity, rgbForIntensity } from '$lib/color'
  import { CATEGORY_META, NO_DATA_COLOR } from '$lib/constants'
  import { t, formatNumber } from '$lib/i18n'
  import { computeZoneGeometry, flowBearingDegrees, makeBorderPointFinder, type BorderSegment } from '$lib/geo'
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

  /** A zone the backend has never returned data for (absent from
   * `/zones`) is permanently inert -- distinct from a zone that merely
   * has no value *at this instant* (colorForIntensity's own null
   * fallback already handles that case). Rendered dim and non-selectable
   * regardless of horizonTime/selection. */
  function hasData(zone: string): boolean {
    return oko.allZones.includes(zone)
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

  function repaintMap() {
    for (const zone in zoneLayers) {
      for (const layer of zoneLayers[zone]) {
        layer.setStyle(zoneStyle(zone))
        layer.setTooltipContent(zoneTooltip(zone))
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
   * FLOW_ICON_MIN_HIT) hoverable/collision box -- shared between the icon
   * builder and the placement pass below so both agree on how much space
   * an icon actually occupies. */
  function chevronIconBox(count: number, zoomScale: number): { width: number; height: number; hitW: number; hitH: number } {
    const unscaledHeight = 8 + (count - 1) * CHEVRON_STEP
    const width = 16 * zoomScale
    const height = unscaledHeight * zoomScale
    return { width, height, hitW: Math.max(FLOW_ICON_MIN_HIT, width), hitH: Math.max(FLOW_ICON_MIN_HIT, height) }
  }

  function buildChevronIcon(count: number, bearingDeg: number, zoomScale: number, color: string): L.DivIcon {
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

  interface FlowCandidate {
    edge: ExchangeEdge
    exporterZone: string
    importerZone: string
    bearing: number
    count: number
    color: string
    box: { hitW: number; hitH: number }
    segment: BorderSegment
  }

  /** A point `t` of the way along a border segment -- t=0 is the segment's
   * midpoint (very close to, but not always exactly, its `anchor`), t=+-1
   * its two extremes. Lets an icon slide along the *real* border line
   * instead of drifting perpendicular off it when avoiding a collision. */
  function pointAlongSegment(segment: BorderSegment, t: number): [number, number] {
    const u = (t + 1) / 2
    return [
      segment.start[0] + (segment.end[0] - segment.start[0]) * u,
      segment.start[1] + (segment.end[1] - segment.start[1]) * u,
    ]
  }

  //: Offsets (as fractions of the start<->end segment) tried in order when
  //: an icon's ideal position collides with an already-placed one --
  //: nearest to the anchor first, walking outward, alternating sides.
  const SEGMENT_SLIDE_STEPS = [0, 0.3, -0.3, 0.6, -0.6, 1, -1]

  function rectsOverlap(
    ax: number,
    ay: number,
    aw: number,
    ah: number,
    bx: number,
    by: number,
    bw: number,
    bh: number
  ): boolean {
    return Math.abs(ax - bx) * 2 < aw + bw && Math.abs(ay - by) * 2 < ah + bh
  }

  function drawFlowLines() {
    clearFlowLines()
    if (!oko.flowLinesVisible || !map) return
    const maxMagnitude = oko.exchangesData.reduce((max, e) => Math.max(max, Math.abs(e.net_flow_mw)), 0) || 1
    const zoomScale = flowIconZoomScale()

    const candidates: FlowCandidate[] = []
    for (const edge of oko.exchangesData) {
      const from = oko.zoneCentroids[edge.zone_from]
      const to = oko.zoneCentroids[edge.zone_to]
      const segment = getBorderPoint(edge.zone_from, edge.zone_to)
      if (!from || !to || !segment || edge.net_flow_mw === 0) continue

      const magnitude = Math.abs(edge.net_flow_mw)
      const scale = magnitude / maxMagnitude
      const forward = edge.net_flow_mw > 0
      const exporter = forward ? from : to
      const importer = forward ? to : from
      const exporterZone = forward ? edge.zone_from : edge.zone_to
      const importerZone = forward ? edge.zone_to : edge.zone_from
      const bearing = flowBearingDegrees(exporter, importer)
      const count = 1 + Math.round(scale * 2)
      const exportValue = oko.zoneValueAtTime(exporterZone, oko.horizonTime)
      const color = colorForIntensity(exportValue, oko.activeIntensityStops)

      candidates.push({
        edge,
        exporterZone,
        importerZone,
        bearing,
        count,
        color,
        box: chevronIconBox(count, zoomScale),
        segment,
      })
    }

    // Bigger flows claim their ideal (anchor) position first; smaller ones
    // flex out of the way along their own border when that collides with
    // an already-placed icon. Tie-broken by zone-pair key for determinism.
    candidates.sort((a, b) => {
      const magnitudeDiff = Math.abs(b.edge.net_flow_mw) - Math.abs(a.edge.net_flow_mw)
      if (magnitudeDiff !== 0) return magnitudeDiff
      const keyA = [a.edge.zone_from, a.edge.zone_to].sort().join('|')
      const keyB = [b.edge.zone_from, b.edge.zone_to].sort().join('|')
      return keyA.localeCompare(keyB)
    })

    const placedRects: { x: number; y: number; w: number; h: number }[] = []
    const isClear = (px: L.Point, w: number, h: number) =>
      !placedRects.some((rect) => rectsOverlap(px.x, px.y, w, h, rect.x, rect.y, rect.w, rect.h))

    for (const candidate of candidates) {
      const { hitW, hitH } = candidate.box
      let resolved: [number, number] | null = null
      let resolvedPx: L.Point | null = null

      // First choice: slide along the *real* border this crossing sits on.
      for (const t of SEGMENT_SLIDE_STEPS) {
        const point = t === 0 ? candidate.segment.anchor : pointAlongSegment(candidate.segment, t)
        const px = map.latLngToContainerPoint(point)
        if (isClear(px, hitW, hitH)) {
          resolved = point
          resolvedPx = px
          break
        }
      }

      // The segment is too short to give any clearance (common at
      // tripoints, where 3+ zones' borders meet almost at a single spot)
      // -- nudge perpendicular to the flow direction instead, still
      // anchored at the crossing, just offset to the side, with growing
      // distance until something clears.
      if (!resolved) {
        const anchorPx = map.latLngToContainerPoint(candidate.segment.anchor)
        const bearingRad = (candidate.bearing * Math.PI) / 180
        const dir = { x: Math.cos(bearingRad), y: Math.sin(bearingRad) }
        for (const steps of [1, -1, 2, -2, 3, -3]) {
          const dist = steps * hitH * 0.7
          const px = L.point(anchorPx.x + dir.x * dist, anchorPx.y + dir.y * dist)
          if (isClear(px, hitW, hitH)) {
            const latlng = map.containerPointToLatLng(px)
            resolved = [latlng.lat, latlng.lng]
            resolvedPx = px
            break
          }
        }
      }

      // Genuinely nothing clears (a very crowded cluster) -- render at the
      // anchor anyway; a rare, minor overlap beats silently dropping a
      // real border crossing.
      if (!resolved || !resolvedPx) {
        resolved = candidate.segment.anchor
        resolvedPx = map.latLngToContainerPoint(resolved)
      }
      placedRects.push({ x: resolvedPx.x, y: resolvedPx.y, w: hitW, h: hitH })

      for (const offset of WORLD_COPY_OFFSETS) {
        const marker = L.marker([resolved[0], resolved[1] + offset], {
          icon: buildChevronIcon(candidate.count, candidate.bearing, zoomScale, candidate.color),
        })
          .bindTooltip(flowTooltip(candidate.edge, candidate.exporterZone, candidate.importerZone), {
            sticky: true,
            className: 'flow-tooltip',
          })
          .addTo(map)
        flowLineLayers.push({ marker, edge: candidate.edge, exporterZone: candidate.exporterZone, importerZone: candidate.importerZone })
      }
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
    map.on('zoomend', drawFlowLines)

    const [geojson, worldGeojson] = await Promise.all([getZonesGeoJson(), getWorldCountriesGeoJson()])

    const covered = coveredCountryCodes(geojson.features)
    const restOfWorld = {
      ...worldGeojson,
      features: worldGeojson.features.filter((f: any) => !covered.has(f.properties.iso2)),
    }

    const { centroids, boundaryPoints } = computeZoneGeometry(geojson)
    oko.zoneCentroids = centroids
    oko.zoneBoundaryPoints = boundaryPoints
    getBorderPoint = makeBorderPointFinder(boundaryPoints)

    for (const offset of WORLD_COPY_OFFSETS) {
      L.geoJSON(shiftGeoJsonLng(restOfWorld, offset), {
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
          layer.bindTooltip(zoneTooltip(zone), { sticky: true, className: 'zone-tooltip' })
          if (hasData(zone)) layer.on('click', () => onZoneClick(zone))
        },
      }).addTo(map)
    }

    drawFlowLines()
  }

  // Dragging the Timebar slider fires many horizonTime updates per second
  // -- each one triggers a full repaint of every zone plus every flow
  // arrow, which is far more work than a single frame budget allows if run
  // synchronously per update. Coalesce to at most one repaint per animation
  // frame; repaintMap/drawFlowLines always read the *current* oko state
  // when they finally run, so no intermediate scrub position is lost --
  // only the redundant in-between repaints are skipped.
  let repaintScheduled = false
  function scheduleRepaint() {
    if (repaintScheduled) return
    repaintScheduled = true
    requestAnimationFrame(() => {
      repaintScheduled = false
      repaintMap()
      drawFlowLines()
    })
  }

  $effect(() => {
    // Re-render on any input that changes a zone's fill/tooltip or a flow
    // arrow's color/tooltip (both keyed off the exporting zone's own
    // carbon intensity) -- horizonTime, colorblindPalette (via
    // activeIntensityStops), exchangesData, and allZones arriving (which
    // decides which zones are grayed-out/non-interactive).
    void oko.horizonTime
    void oko.activeIntensityStops
    void oko.exchangesData
    void oko.allZones
    scheduleRepaint()
  })

  $effect(() => {
    // Selecting a zone only changes the border weight/color of the
    // previously- and newly-selected zone -- not every zone's fill, and
    // not the flow arrows (which don't depend on selection at all). Restyle
    // just those (at most two) layers instead of the full repaintMap().
    const zone = oko.selectedZone
    const previous = previousSelectedZone
    if (previous) {
      for (const layer of zoneLayers[previous] || []) {
        layer.setStyle(zoneStyle(previous))
        layer.setTooltipContent(zoneTooltip(previous))
      }
    }
    for (const layer of zoneLayers[zone] || []) {
      layer.setStyle(zoneStyle(zone))
      layer.setTooltipContent(zoneTooltip(zone))
    }
    previousSelectedZone = zone
  })

  $effect(() => {
    void oko.flowLinesVisible
    void oko.exchangesData
    drawFlowLines()
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
