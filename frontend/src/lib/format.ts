export function formatDate(d: Date, locale: string = 'en'): string {
  return d.toLocaleDateString(locale, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatTime(d: Date, use24h: boolean, locale: string = 'en'): string {
  return d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: !use24h })
}

export function formatWeekdayTime(d: Date, use24h: boolean, locale: string = 'en'): string {
  return `${d.toLocaleDateString(locale, { weekday: 'short' })} ${formatTime(d, use24h, locale)}`
}

export function formatFullDateTime(d: Date, use24h: boolean, locale: string = 'en'): string {
  return `${formatDate(d, locale)}, ${formatTime(d, use24h, locale)}`
}
