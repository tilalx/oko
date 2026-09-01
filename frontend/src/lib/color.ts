import { NO_DATA_COLOR } from './constants'

export function rgbToHex([r, g, b]: readonly number[]): string {
  const toHex = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0')
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

export function rgbForIntensity(
  value: number | null | undefined,
  stops: readonly [number, readonly number[]][]
): number[] | null {
  if (value == null) return null
  if (value <= stops[0][0]) return [...stops[0][1]]
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1] = stops[i - 1]
    const [v2, c2] = stops[i]
    if (value <= v2) {
      const t = (value - v1) / (v2 - v1)
      return c1.map((c, idx) => c + t * (c2[idx] - c))
    }
  }
  return [...stops[stops.length - 1][1]]
}

export function colorForIntensity(
  value: number | null | undefined,
  stops: readonly [number, readonly number[]][]
): string {
  const rgb = rgbForIntensity(value, stops)
  return rgb ? rgbToHex(rgb) : NO_DATA_COLOR
}

export function readableTextColor(rgb: readonly number[] | null | undefined): string {
  const c = rgb ?? [0, 0, 0]
  const luminance = (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255
  return luminance > 0.6 ? '#14150f' : '#f4f6f2'
}
