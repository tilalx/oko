// ---------------------------------------------------------------------
// Zone polygon centroids (area-weighted, shoelace formula) -- used to
// anchor cross-border flow lines. Computed directly from the GeoJSON ring
// coordinates rather than a bounding-box center, so elongated / multi-part
// zones (e.g. FR is a MultiPolygon) get a sensible anchor.
// ---------------------------------------------------------------------

type Ring = [number, number][] // [lng, lat]

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

/** Every ring vertex (all rings, all parts of a MultiPolygon) as [lat, lng]
 * -- used to find the real border-crossing point between two zones, unlike
 * the single interior centroid above. */
function extractBoundaryPoints(coordinates: any, type: string): [number, number][] {
  const rings: Ring[] = type === 'MultiPolygon' ? coordinates.flatMap((polygon: Ring[]) => polygon) : coordinates
  const points: [number, number][] = []
  for (const ring of rings) {
    for (const [lng, lat] of ring) points.push([lat, lng])
  }
  return points
}

export function computeZoneGeometry(geojson: any): {
  centroids: Record<string, [number, number]>
  boundaryPoints: Record<string, [number, number][]>
} {
  const centroids: Record<string, [number, number]> = {}
  const boundaryPoints: Record<string, [number, number][]> = {}
  for (const feature of geojson.features) {
    const zone = feature.properties.zone
    const centroid = polygonCentroid(feature.geometry.coordinates, feature.geometry.type)
    if (centroid) centroids[zone] = [centroid[1], centroid[0]]
    boundaryPoints[zone] = extractBoundaryPoints(feature.geometry.coordinates, feature.geometry.type)
  }
  return { centroids, boundaryPoints }
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

/** Radius (degrees) around the actual closest-approach point within which
 * a vertex still counts as part of the same shared border run. A long
 * straight shared border only has vertices at its two corner ends (a
 * straight line needs no vertices in between) -- taking just the single
 * closest vertex pair always snaps the marker to whichever corner happens
 * to be nearest. Pulling in every vertex near that closest-approach point
 * picks up both ends of that long edge (plus any other nearby corners),
 * so averaging them lands the marker along the border, not pinned to one
 * corner.
 *
 * Anchored to the *location* of the closest approach, not just its
 * *distance* -- a zone with a complex, multi-part coastline (e.g. Norway's
 * or Sweden's fjord/archipelago boundaries) can have several separate
 * points that are each individually close to the other zone, at different
 * locations; including all of them (as a distance-only threshold would)
 * averages across disjoint border segments and can land the marker in open
 * water between them. */
const BORDER_CLUSTER_RADIUS_DEGREES = 0.15

/** Vertices of each zone's boundary that lie within
 * BORDER_CLUSTER_RADIUS_DEGREES of the true closest-approach point between
 * the two zones, averaged together -- the shared border "run", not just
 * the single nearest vertex pair and not any other coincidentally-close
 * vertex elsewhere on a complex coastline. O(|A|*|B|) vertices; called
 * once per pair and memoized by the caller (see makeBorderPointFinder) --
 * each zone has only tens to a few hundred vertices, so this is negligible
 * even across all borders. */
/** A border crossing's anchor (for icon placement/priority) plus the two
 * extremes of the local shared-border run, as a straight-line
 * approximation an icon can slide along to avoid overlapping a
 * neighboring crossing's icon (see MapView's drawFlowLines) without
 * drifting off the actual border. */
export interface BorderSegment {
  anchor: [number, number]
  start: [number, number]
  end: [number, number]
}

function computeBorderPoint(
  boundaryPoints: Record<string, [number, number][]>,
  zoneA: string,
  zoneB: string
): BorderSegment | null {
  const pointsA = boundaryPoints[zoneA]
  const pointsB = boundaryPoints[zoneB]
  if (!pointsA || !pointsB) return null

  let bestA: [number, number] | null = null
  let bestB: [number, number] | null = null
  let bestDistSq = Infinity
  for (const a of pointsA) {
    for (const b of pointsB) {
      const d = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
      if (d < bestDistSq) {
        bestDistSq = d
        bestA = a
        bestB = b
      }
    }
  }
  if (!bestA || !bestB) return null
  const anchor: [number, number] = [(bestA[0] + bestB[0]) / 2, (bestA[1] + bestB[1]) / 2]

  const radiusSq = BORDER_CLUSTER_RADIUS_DEGREES ** 2
  const nearAnchor = (p: [number, number]) => (p[0] - anchor[0]) ** 2 + (p[1] - anchor[1]) ** 2 <= radiusSq
  // The radius is fixed, but the closest approach between two zones isn't
  // always within it (coarse/simplified zone geometry, or zones that don't
  // truly share a border) -- fall back to the closest pair itself so the
  // cluster is never empty (which would otherwise average to NaN).
  const clusterPoints = [...pointsA.filter(nearAnchor), ...pointsB.filter(nearAnchor)]
  if (clusterPoints.length === 0) clusterPoints.push(bestA, bestB)

  const lat = clusterPoints.reduce((sum, p) => sum + p[0], 0) / clusterPoints.length
  const lng = clusterPoints.reduce((sum, p) => sum + p[1], 0) / clusterPoints.length

  // The segment's two ends -- the pair of cluster points with the greatest
  // mutual distance -- approximate the shared border as a straight line an
  // icon can slide along. Falls back to the anchor itself (a zero-length
  // segment) when the cluster is a single point.
  let start: [number, number] = clusterPoints[0]
  let end: [number, number] = clusterPoints[0]
  let bestSpanSq = -1
  for (const p of clusterPoints) {
    for (const q of clusterPoints) {
      const d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
      if (d > bestSpanSq) {
        bestSpanSq = d
        start = p
        end = q
      }
    }
  }

  return { anchor: [lat, lng], start, end }
}

/** Memoizing wrapper factory -- one cache per zone geometry (re-created
 * whenever the GeoJSON is (re-)loaded). */
export function makeBorderPointFinder(boundaryPoints: Record<string, [number, number][]>) {
  const cache: Record<string, BorderSegment | null> = {}
  return function getBorderPoint(zoneA: string, zoneB: string): BorderSegment | null {
    const key = [zoneA, zoneB].sort().join('|')
    if (!(key in cache)) cache[key] = computeBorderPoint(boundaryPoints, zoneA, zoneB)
    return cache[key]
  }
}
