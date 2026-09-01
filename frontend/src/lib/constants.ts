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

export const CATEGORY_META: Record<string, { icon: string; color: string }> = {
  biomass: { icon: '🌱', color: '#8bc34a' },
  geothermal: { icon: '♨️', color: '#e07a55' },
  hydro: { icon: '💧', color: '#5b9bd5' },
  solar: { icon: '☀️', color: '#f4d35e' },
  wind: { icon: '🌬️', color: '#6fcccb' },
  nuclear: { icon: '☢️', color: '#b18cd9' },
  gas: { icon: '🔥', color: '#c58b5e' },
  coal: { icon: '⬛', color: '#7a7a7a' },
  oil: { icon: '🛢️', color: '#8a6d3b' },
  unknown: { icon: '❓', color: '#7a8079' },
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

export const NO_DATA_COLOR = '#5c645d'
