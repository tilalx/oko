/** `Date.prototype.toLocale*String` builds a fresh `Intl.DateTimeFormat`
 * on every call, which dominates the cost of formatting a handful of
 * fixed shapes repeatedly (map tooltips, Timebar ruler labels, panels).
 * Cache one formatter per (locale, options) pair instead. */
const dateFormats = new Map<string, Intl.DateTimeFormat>()

function formatter(locale: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}|${JSON.stringify(options)}`
  let format = dateFormats.get(key)
  if (!format) {
    format = new Intl.DateTimeFormat(locale, options)
    dateFormats.set(key, format)
  }
  return format
}

export function formatDate(d: Date, locale: string = 'en'): string {
  return formatter(locale, { month: 'short', day: 'numeric', year: 'numeric' }).format(d)
}

export function formatTime(d: Date, use24h: boolean, locale: string = 'en'): string {
  return formatter(locale, { hour: '2-digit', minute: '2-digit', hour12: !use24h }).format(d)
}

export function formatWeekdayTime(d: Date, use24h: boolean, locale: string = 'en'): string {
  return `${formatter(locale, { weekday: 'short' }).format(d)} ${formatTime(d, use24h, locale)}`
}

export function formatFullDateTime(d: Date, use24h: boolean, locale: string = 'en'): string {
  return `${formatDate(d, locale)}, ${formatTime(d, use24h, locale)}`
}
