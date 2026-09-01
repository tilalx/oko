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

/** Extra distance (degrees) beyond the closest approach within which a
 * vertex still counts as part of the same shared border run. A long
 * straight shared border only has vertices at its two corner ends (a
 * straight line needs no vertices in between) -- taking just the single
 * closest vertex pair always snaps the marker to whichever corner happens
 * to be nearest. Pulling in every vertex within this margin picks up both
 * ends of that long edge (plus any other nearby corners), so averaging
 * them lands the marker along the border, not pinned to one corner. */
const BORDER_CLUSTER_MARGIN_DEGREES = 0.15

/** Vertices of each zone's boundary that lie within
 * BORDER_CLUSTER_MARGIN_DEGREES of the other zone's closest approach,
 * averaged together -- the shared border "run" between two zones, not
 * just their single nearest vertex pair. O(|A|*|B|) vertices; called once
 * per pair and memoized by the caller (see makeBorderPointCache) -- each
 * zone has only tens to a few hundred vertices, so this is negligible
 * even across all borders. */
function computeBorderPoint(
  boundaryPoints: Record<string, [number, number][]>,
  zoneA: string,
  zoneB: string
): [number, number] | null {
  const pointsA = boundaryPoints[zoneA]
  const pointsB = boundaryPoints[zoneB]
  if (!pointsA || !pointsB) return null

  const nearestDistSq = (point: [number, number], others: [number, number][]) => {
    let best = Infinity
    for (const o of others) {
      const d = (point[0] - o[0]) ** 2 + (point[1] - o[1]) ** 2
      if (d < best) best = d
    }
    return best
  }

  const distsA = pointsA.map((a) => nearestDistSq(a, pointsB))
  const distsB = pointsB.map((b) => nearestDistSq(b, pointsA))
  const minDistSq = Math.min(...distsA, ...distsB)
  if (!isFinite(minDistSq)) return null

  const thresholdSq = (Math.sqrt(minDistSq) + BORDER_CLUSTER_MARGIN_DEGREES) ** 2
  const clusterPoints: [number, number][] = []
  pointsA.forEach((a, i) => distsA[i] <= thresholdSq && clusterPoints.push(a))
  pointsB.forEach((b, i) => distsB[i] <= thresholdSq && clusterPoints.push(b))

  const lat = clusterPoints.reduce((sum, p) => sum + p[0], 0) / clusterPoints.length
  const lng = clusterPoints.reduce((sum, p) => sum + p[1], 0) / clusterPoints.length
  return [lat, lng]
}

/** Memoizing wrapper factory -- one cache per zone geometry (re-created
 * whenever the GeoJSON is (re-)loaded). */
export function makeBorderPointFinder(boundaryPoints: Record<string, [number, number][]>) {
  const cache: Record<string, [number, number] | null> = {}
  return function getBorderPoint(zoneA: string, zoneB: string): [number, number] | null {
    const key = [zoneA, zoneB].sort().join('|')
    if (!(key in cache)) cache[key] = computeBorderPoint(boundaryPoints, zoneA, zoneB)
    return cache[key]
  }
}
