export const CONFIDENCE_COLOR: Record<string, string> = {
  high: '#46c491',
  medium: '#e0b74f',
  low: '#e0704f',
}

export const ZONE_NAMES: Record<string, string> = {
  'DE-LU': 'Germany / Luxembourg',
  FR: 'France',
  CH: 'Switzerland',
  AT: 'Austria',
  CZ: 'Czechia',
  PL: 'Poland',
  'DK-DK1': 'Denmark (West)',
  'DK-DK2': 'Denmark (East)',
  NL: 'Netherlands',
  BE: 'Belgium',
  ES: 'Spain',
  'IT-NO': 'Italy (North)',
  HU: 'Hungary',
  SI: 'Slovenia',
  SK: 'Slovakia',
  FI: 'Finland',
  'NO-NO1': 'Norway (Southeast)',
  'NO-NO2': 'Norway (South)',
  'NO-NO3': 'Norway (Central)',
  'NO-NO4': 'Norway (North)',
  'NO-NO5': 'Norway (West)',
  'SE-SE1': 'Sweden (North)',
  'SE-SE2': 'Sweden (North-Central)',
  'SE-SE3': 'Sweden (South-Central)',
  'SE-SE4': 'Sweden (South)',
  EE: 'Estonia',
  LV: 'Latvia',
  LT: 'Lithuania',
  GB: 'Great Britain',
  'GB-NIR': 'Northern Ireland',
  IE: 'Ireland',
  PT: 'Portugal',
  BG: 'Bulgaria',
  RO: 'Romania',
  GR: 'Greece',
  HR: 'Croatia',
  RS: 'Serbia',
  BA: 'Bosnia and Herzegovina',
  ME: 'Montenegro',
  MK: 'North Macedonia',
  AL: 'Albania',
  XK: 'Kosovo',
  'IT-CNO': 'Italy (Central North)',
  'IT-CSO': 'Italy (Central South)',
  'IT-SO': 'Italy (South)',
  'IT-SAR': 'Italy (Sardinia)',
  'IT-SIC': 'Italy (Sicily)',
  MT: 'Malta',
}

export const CATEGORY_ORDER = [
  'biomass',
  'geothermal',
  'hydro',
  'solar',
  'wind',
  'nuclear',
  'gas',
  'coal',
  'oil',
  'unknown',
]

/** Mirrors oko.emissions.calculator.RENEWABLE_CATEGORIES /
 * FOSSIL_FREE_CATEGORIES -- used to derive renewable/carbon-free share
 * client-side from a point's own power_breakdown_percent, so those
 * gauges work at any scrubbed history/forecast position, not just the
 * live "now" hour (the only point the server precomputes them for). */
export const RENEWABLE_CATEGORIES = new Set(['wind', 'solar', 'hydro', 'biomass', 'geothermal'])
export const FOSSIL_FREE_CATEGORIES = new Set([...RENEWABLE_CATEGORIES, 'nuclear'])

/** Path data for each category's icon, shared with `Icon.svelte`'s
 * `PATHS` map (kept as a standalone svg string here too, since MapView's
 * Leaflet tooltip is built as a raw HTML string, not a Svelte component
 * tree, so it can't render `<Icon>` directly). */
const CATEGORY_ICON_PATH: Record<string, string> = {
  biomass: '<path d="M12 21c0-6 3-9 7-11-2 5-2 9-7 11Z"/><path d="M12 21c0-7-3-10-7-12 1 6 2 10 7 12Z"/>',
  geothermal: '<path d="M12 3c2 3 3 5 3 7a3 3 0 1 1-6 0c0-2 1-4 3-7Z"/><path d="M6 21c0-3 2.5-5 6-5s6 2 6 5"/>',
  hydro: '<path d="M12 3c3 4 6 7.5 6 11a6 6 0 0 1-12 0c0-3.5 3-7 6-11Z"/>',
  solar:
    '<circle cx="12" cy="12" r="4"/><path d="M12 3v2.5M12 18.5V21M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M3 12h2.5M18.5 12H21M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"/>',
  wind: '<path d="M3 8h9a2.5 2.5 0 1 0-2.2-3.6"/><path d="M3 16h13a2.5 2.5 0 1 1-2.2 3.6"/><path d="M3 12h6a2 2 0 1 0-1.8-2.8"/>',
  nuclear:
    '<circle cx="12" cy="12" r="1.6"/><path d="M12 12 6.3 8.7A6 6 0 0 0 12 18Z"/><path d="M12 12l5.7-3.3A6 6 0 0 0 6.3 8.7Z"/><path d="M12 12v6.9a6 6 0 0 0 5.7-7.2Z"/>',
  gas: '<path d="M12 3s-4 4.5-4 8.5a4 4 0 0 0 8 0c0-1.4-.8-2.4-1.5-3.3.2 1-.2 1.8-1 2C13.8 9 12 6.8 12 3Z"/>',
  coal: '<rect x="4" y="9" width="7" height="7" rx="1"/><rect x="12" y="6" width="8" height="10" rx="1"/>',
  oil: '<path d="M7 8h6l2 3v8H5v-8Z"/><path d="M9 8V5h2v3"/>',
  unknown:
    '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.5 2.5 0 1 1 3.9 2.1c-.9.6-1.4 1.1-1.4 2.2"/><circle cx="12" cy="17" r="0.1" fill="currentColor"/>',
}

function categoryIcon(category: string, color: string): string {
  const path = CATEGORY_ICON_PATH[category] || CATEGORY_ICON_PATH.unknown
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px">${path}</svg>`
}

export const CATEGORY_META: Record<string, { icon: string; color: string }> = {
  biomass: { icon: categoryIcon('biomass', '#8bc34a'), color: '#8bc34a' },
  geothermal: { icon: categoryIcon('geothermal', '#e07a55'), color: '#e07a55' },
  hydro: { icon: categoryIcon('hydro', '#5b9bd5'), color: '#5b9bd5' },
  solar: { icon: categoryIcon('solar', '#f4d35e'), color: '#f4d35e' },
  wind: { icon: categoryIcon('wind', '#6fcccb'), color: '#6fcccb' },
  nuclear: { icon: categoryIcon('nuclear', '#b18cd9'), color: '#b18cd9' },
  gas: { icon: categoryIcon('gas', '#c58b5e'), color: '#c58b5e' },
  coal: { icon: categoryIcon('coal', '#7a7a7a'), color: '#7a7a7a' },
  oil: { icon: categoryIcon('oil', '#8a6d3b'), color: '#8a6d3b' },
  unknown: { icon: categoryIcon('unknown', '#7a8079'), color: '#7a8079' },
}

/** Hours of recent observed history prepended to the forecast horizon so
 * the timeline can be scrubbed into the past as well as the future -- see
 * `unifiedPoints`/`nowSeamIndex` in state.svelte.ts. */
export const HISTORY_SCRUB_HOURS = 48

/** How often to re-fetch every zone's forecast+history in the background. */
export const AUTO_REFRESH_INTERVAL_MS = 5 * 60 * 1000

/** Sampled pixel-for-pixel from the reference legend bar (green -> gold ->
 * rust -> a wide dark-brown plateau around 800-1100 -> fading to black at
 * 1500), not hand-picked -- keep in sync with that bar if it changes. */
export const INTENSITY_STOPS: [number, [number, number, number]][] = [
  [0, [49, 162, 99]],
  [100, [180, 211, 85]],
  [200, [234, 215, 74]],
  [300, [215, 176, 67]],
  [400, [194, 139, 59]],
  [500, [175, 102, 52]],
  [600, [156, 68, 45]],
  [700, [107, 50, 28]],
  [800, [59, 35, 9]],
  [1100, [59, 35, 9]],
  [1200, [46, 28, 6]],
  [1300, [34, 22, 6]],
  [1400, [22, 16, 0]],
  [1500, [0, 0, 0]],
]

/** Viridis-derived -- perceptually uniform and robust to red/green color
 * vision deficiency, unlike the default red/green ramp above. */
export const COLORBLIND_INTENSITY_STOPS: [number, [number, number, number]][] = [
  [0, [68, 1, 84]],
  [200, [59, 82, 139]],
  [350, [33, 145, 140]],
  [500, [94, 201, 98]],
  [900, [253, 231, 37]],
  [1500, [255, 250, 190]],
]

/** Day-ahead price color ramp, same shape as INTENSITY_STOPS (cheap ->
 * green, expensive -> brown) but over EUR/MWh's domain -- negative prices
 * clip to the greenest stop, same as INTENSITY_STOPS clips at 0. */
export const PRICE_STOPS: [number, [number, number, number]][] = [
  [-50, [49, 162, 99]],
  [0, [130, 190, 90]],
  [50, [180, 211, 85]],
  [100, [234, 215, 74]],
  [150, [215, 176, 67]],
  [200, [194, 139, 59]],
  [300, [156, 68, 45]],
  [400, [107, 50, 28]],
]

/** Viridis-derived, mirrors COLORBLIND_INTENSITY_STOPS over the price domain. */
export const COLORBLIND_PRICE_STOPS: [number, [number, number, number]][] = [
  [-50, [68, 1, 84]],
  [50, [59, 82, 139]],
  [100, [33, 145, 140]],
  [150, [94, 201, 98]],
  [250, [253, 231, 37]],
  [400, [255, 250, 190]],
]

export const NO_DATA_COLOR = '#5c645d'
