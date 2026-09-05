// ---------------------------------------------------------------------
// Zone polygon centroids (area-weighted, shoelace formula) -- used to
// anchor cross-border flow lines. Computed directly from the GeoJSON ring
// coordinates rather than a bounding-box center, so elongated / multi-part
// zones (e.g. FR is a MultiPolygon) get a sensible anchor.
// ---------------------------------------------------------------------

type Ring = [number, number][] // [lng, lat]
export type LatLng = [number, number]

function ringArea(ring: Ring): number {
  let sum = 0
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = ring[i]
    const [x2, y2] = ring[i + 1]
    sum += x1 * y2 - x2 * y1
  }
  return sum / 2
}

function ringCentroid(ring: Ring): [number, number] {
  let cx = 0
  let cy = 0
  let area = 0
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = ring[i]
    const [x2, y2] = ring[i + 1]
    const cross = x1 * y2 - x2 * y1
    area += cross
    cx += (x1 + x2) * cross
    cy += (y1 + y2) * cross
  }
  area /= 2
  if (area === 0) return ring[0]
  return [cx / (6 * area), cy / (6 * area)]
}

/** Returns [lng, lat] -- the caller flips to Leaflet's [lat, lng] order. */
function polygonCentroid(coordinates: any, type: string): [number, number] | null {
  const rings: Ring[] = type === 'MultiPolygon' ? coordinates.map((polygon: Ring[]) => polygon[0]) : [coordinates[0]]
  let best: [number, number] | null = null
  let bestArea = -Infinity
  for (const ring of rings) {
    const area = Math.abs(ringArea(ring))
    if (area > bestArea) {
      bestArea = area
      best = ringCentroid(ring)
    }
  }
  return best
}

/** Every boundary ring (all rings, all parts of a MultiPolygon) as a closed
 * [lat, lng] polyline. Kept as *rings* rather than a flat vertex cloud so
 * the border finder below can walk a boundary in order -- a point averaged
 * out of an unordered cloud can land anywhere, a point interpolated along a
 * ring is always on the boundary. */
function extractBoundaryRings(coordinates: any, type: string): LatLng[][] {
  const rings: Ring[] = type === 'MultiPolygon' ? coordinates.flatMap((polygon: Ring[]) => polygon) : coordinates
  return rings.map((ring) => ring.map(([lng, lat]) => [lat, lng] as LatLng))
}

export function computeZoneGeometry(geojson: any): {
  centroids: Record<string, LatLng>
  boundaries: Record<string, LatLng[][]>
} {
  const centroids: Record<string, LatLng> = {}
  const boundaries: Record<string, LatLng[][]> = {}
  for (const feature of geojson.features) {
    const zone = feature.properties.zone
    const centroid = polygonCentroid(feature.geometry.coordinates, feature.geometry.type)
    if (centroid) centroids[zone] = [centroid[1], centroid[0]]
    boundaries[zone] = extractBoundaryRings(feature.geometry.coordinates, feature.geometry.type)
  }
  return { centroids, boundaries }
}

// ---------------------------------------------------------------------
// Cross-border flow arrows: bearing + border-crossing anchor point.
// ---------------------------------------------------------------------

export function flowBearingDegrees([lat1, lng1]: [number, number], [lat2, lng2]: [number, number]): number {
  const rad = Math.PI / 180
  const dLng = (lng2 - lng1) * rad
  const y = Math.sin(dLng) * Math.cos(lat2 * rad)
  const x = Math.cos(lat1 * rad) * Math.sin(lat2 * rad) - Math.sin(lat1 * rad) * Math.cos(lat2 * rad) * Math.cos(dLng)
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
}

/** How far (in degrees of latitude, ~17 km) a vertex of one zone may sit
 * from the other zone's boundary and still count as lying *on* the shared
 * border. Has to absorb the disagreement between two independently
 * simplified polygons tracing the same real-world border, without pulling
 * in a facing coastline across a strait. */
const BORDER_TOLERANCE_DEGREES = 0.15

/** Grid cell size for the per-zone segment index. Must be >=
 * BORDER_TOLERANCE_DEGREES so a proximity query only ever has to look at
 * the cells immediately around the query point. */
const GRID_CELL_DEGREES = 0.5

type Segment = [LatLng, LatLng]

/** Longitude degrees shrink toward the poles; comparing raw degrees would
 * make the tolerance (and every length below) latitude-dependent. All
 * distance math here works in this locally-equirectangular space instead:
 * y = lat, x = lng * cos(lat). */
function lngScaleAt(lat: number): number {
  return Math.cos((lat * Math.PI) / 180)
}

function distSq(a: LatLng, b: LatLng, lngScale: number): number {
  const dy = a[0] - b[0]
  const dx = (a[1] - b[1]) * lngScale
  return dy * dy + dx * dx
}

/** Squared distance from a point to a *segment* -- not to its endpoints. A
 * long straight shared border carries no vertices between its two corners,
 * so vertex-to-vertex proximity alone would call the middle of such a
 * border "far away". */
function distSqToSegment(p: LatLng, [a, b]: Segment, lngScale: number): number {
  const py = p[0] - a[0]
  const px = (p[1] - a[1]) * lngScale
  const vy = b[0] - a[0]
  const vx = (b[1] - a[1]) * lngScale
  const lenSq = vx * vx + vy * vy
  if (lenSq === 0) return py * py + px * px
  const t = Math.max(0, Math.min(1, (px * vx + py * vy) / lenSq))
  const dy = py - t * vy
  const dx = px - t * vx
  return dy * dy + dx * dx
}

/** A zone's boundary segments bucketed into a fixed lat/lng grid, so
 * "is this point on that zone's boundary?" costs a handful of segment
 * tests instead of a scan of the whole zone. Built once per zone, lazily,
 * and shared by every border that zone takes part in. */
type SegmentIndex = Map<string, Segment[]>

function cellKey(latCell: number, lngCell: number): string {
  return `${latCell}:${lngCell}`
}

function buildSegmentIndex(rings: LatLng[][]): SegmentIndex {
  const index: SegmentIndex = new Map()
  for (const ring of rings) {
    for (let i = 0; i < ring.length - 1; i++) {
      const segment: Segment = [ring[i], ring[i + 1]]
      const latFrom = Math.floor(Math.min(segment[0][0], segment[1][0]) / GRID_CELL_DEGREES)
      const latTo = Math.floor(Math.max(segment[0][0], segment[1][0]) / GRID_CELL_DEGREES)
      const lngFrom = Math.floor(Math.min(segment[0][1], segment[1][1]) / GRID_CELL_DEGREES)
      const lngTo = Math.floor(Math.max(segment[0][1], segment[1][1]) / GRID_CELL_DEGREES)
      // Bounding-box cells, not an exact rasterization: a few extra
      // candidates cost one distance test each, and every cell the segment
      // truly crosses is covered.
      for (let latCell = latFrom; latCell <= latTo; latCell++) {
        for (let lngCell = lngFrom; lngCell <= lngTo; lngCell++) {
          const key = cellKey(latCell, lngCell)
          const bucket = index.get(key)
          if (bucket) bucket.push(segment)
          else index.set(key, [segment])
        }
      }
    }
  }
  return index
}

/** Whether `p` lies within BORDER_TOLERANCE_DEGREES of the indexed
 * boundary. The tolerance is a latitude distance; in longitude it spans
 * more degrees the further from the equator, hence the widened cell
 * range. */
function nearBoundary(index: SegmentIndex, p: LatLng): boolean {
  const lngScale = lngScaleAt(p[0])
  const lngReach = BORDER_TOLERANCE_DEGREES / Math.max(lngScale, 0.01)
  const latCell = Math.floor(p[0] / GRID_CELL_DEGREES)
  const lngCell = Math.floor(p[1] / GRID_CELL_DEGREES)
  const lngCells = Math.ceil(lngReach / GRID_CELL_DEGREES)
  const toleranceSq = BORDER_TOLERANCE_DEGREES ** 2
  for (let dLat = -1; dLat <= 1; dLat++) {
    for (let dLng = -lngCells; dLng <= lngCells; dLng++) {
      const bucket = index.get(cellKey(latCell + dLat, lngCell + dLng))
      if (!bucket) continue
      for (const segment of bucket) {
        if (distSqToSegment(p, segment, lngScale) <= toleranceSq) return true
      }
    }
  }
  return false
}

/** The maximal runs of consecutive vertices of `rings` that lie on the
 * indexed boundary -- i.e. the shared border(s), in boundary order. Rings
 * are closed, so a run may wrap around the ring's start. */
function sharedBorderRuns(rings: LatLng[][], index: SegmentIndex): LatLng[][] {
  const runs: LatLng[][] = []
  for (const ring of rings) {
    const n = ring.length - 1 // last vertex repeats the first
    if (n < 2) continue
    const shared = Array.from({ length: n }, (_, i) => nearBoundary(index, ring[i]))
    if (shared.every((s) => !s)) continue
    // Start walking just after a gap so a run that straddles index 0 is
    // collected as one run rather than two. (A ring entirely on the border
    // -- an enclave -- has no gap; it is emitted whole below.)
    let start = shared.findIndex((s) => !s)
    if (start === -1) {
      runs.push(ring.slice(0, n))
      continue
    }
    let current: LatLng[] = []
    for (let step = 0; step < n; step++) {
      const i = (start + step) % n
      if (shared[i]) current.push(ring[i])
      else if (current.length) {
        runs.push(current)
        current = []
      }
    }
    if (current.length) runs.push(current)
  }
  return runs
}

function polylineLength(points: LatLng[]): number {
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    total += Math.sqrt(distSq(points[i], points[i + 1], lngScaleAt(points[i][0])))
  }
  return total
}

/** The point halfway along the polyline, by arc length -- interpolated on
 * the line itself, so the result is always *on* the border rather than
 * somewhere near it. */
function midpointAlong(points: LatLng[]): LatLng {
  const half = polylineLength(points) / 2
  if (half === 0) return points[0]
  let walked = 0
  for (let i = 0; i < points.length - 1; i++) {
    const step = Math.sqrt(distSq(points[i], points[i + 1], lngScaleAt(points[i][0])))
    if (walked + step >= half) {
      const t = step === 0 ? 0 : (half - walked) / step
      return [
        points[i][0] + (points[i + 1][0] - points[i][0]) * t,
        points[i][1] + (points[i + 1][1] - points[i][1]) * t,
      ]
    }
    walked += step
  }
  return points[points.length - 1]
}

/** Closest approach between two boundaries, as the midpoint of the nearest
 * vertex/segment pair. Used only for zone pairs that share no border at
 * all -- subsea interconnectors (GB-NL, DK-SE, ...) -- where the honest
 * anchor is the water between the two coasts. */
function closestApproachMidpoint(ringsA: LatLng[][], ringsB: LatLng[][]): LatLng | null {
  let best: LatLng | null = null
  let bestDistSq = Infinity
  for (const ring of ringsA) {
    for (const a of ring) {
      const lngScale = lngScaleAt(a[0])
      for (const ringB of ringsB) {
        for (let i = 0; i < ringB.length - 1; i++) {
          const segment: Segment = [ringB[i], ringB[i + 1]]
          const d = distSqToSegment(a, segment, lngScale)
          if (d < bestDistSq) {
            bestDistSq = d
            // Project onto the segment so the midpoint spans the real gap,
            // not the gap to whichever B vertex happened to be nearest.
            const closest = closestPointOnSegment(a, segment, lngScale)
            best = [(a[0] + closest[0]) / 2, (a[1] + closest[1]) / 2]
          }
        }
      }
    }
  }
  return best
}

function closestPointOnSegment(p: LatLng, [a, b]: Segment, lngScale: number): LatLng {
  const py = p[0] - a[0]
  const px = (p[1] - a[1]) * lngScale
  const vy = b[0] - a[0]
  const vx = (b[1] - a[1]) * lngScale
  const lenSq = vx * vx + vy * vy
  if (lenSq === 0) return a
  const t = Math.max(0, Math.min(1, (px * vx + py * vy) / lenSq))
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
}

/** Where a border crossing's arrow belongs: the midpoint (by arc length)
 * of the longest stretch of boundary the two zones actually share.
 *
 * Both sides are tried and the longer shared run wins, because the two
 * polygons are simplified independently -- one may carry a dozen vertices
 * along a border where the other carries two.
 *
 * The result sits *on* a real boundary polyline by construction. The
 * previous version averaged a cloud of nearby vertices from both zones,
 * which drifts off the border wherever it curves, and around a tripoint
 * mixed in vertices belonging to a different neighbour's border entirely. */
function computeBorderPoint(
  boundaries: Record<string, LatLng[][]>,
  indexFor: (zone: string) => SegmentIndex | null,
  zoneA: string,
  zoneB: string
): LatLng | null {
  const ringsA = boundaries[zoneA]
  const ringsB = boundaries[zoneB]
  const indexA = indexFor(zoneA)
  const indexB = indexFor(zoneB)
  if (!ringsA || !ringsB || !indexA || !indexB) return null

  let bestRun: LatLng[] | null = null
  let bestLength = -1
  for (const run of [...sharedBorderRuns(ringsA, indexB), ...sharedBorderRuns(ringsB, indexA)]) {
    const length = polylineLength(run)
    if (length > bestLength) {
      bestLength = length
      bestRun = run
    }
  }
  if (bestRun) return midpointAlong(bestRun)

  return closestApproachMidpoint(ringsA, ringsB)
}

/** Memoizing wrapper factory -- one cache per zone geometry (re-created
 * whenever the GeoJSON is (re-)loaded). Both the per-pair anchors and the
 * per-zone segment indexes are built on first use. */
export function makeBorderPointFinder(boundaries: Record<string, LatLng[][]>) {
  const anchors: Record<string, LatLng | null> = {}
  const indexes: Record<string, SegmentIndex | null> = {}
  const indexFor = (zone: string): SegmentIndex | null => {
    if (!(zone in indexes)) indexes[zone] = boundaries[zone] ? buildSegmentIndex(boundaries[zone]) : null
    return indexes[zone]
  }
  return function getBorderPoint(zoneA: string, zoneB: string): LatLng | null {
    const key = [zoneA, zoneB].sort().join('|')
    if (!(key in anchors)) anchors[key] = computeBorderPoint(boundaries, indexFor, zoneA, zoneB)
    return anchors[key]
  }
}
