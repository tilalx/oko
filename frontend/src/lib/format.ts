export function formatDate(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatTime(d: Date, use24h: boolean): string {
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: !use24h })
}

export function formatWeekdayTime(d: Date, use24h: boolean): string {
  return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${formatTime(d, use24h)}`
}

export function formatFullDateTime(d: Date, use24h: boolean): string {
  return `${formatDate(d)}, ${formatTime(d, use24h)}`
}

export function countryFlagEmoji(zone: string): string {
  const code = zone.split('-')[0].toUpperCase()
  return code.replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt(0)))
}
