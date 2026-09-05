import en from './locales/en.json'

type Messages = typeof en
type MessageKey = keyof Messages

function getNestedValue(obj: any, path: string): any {
  return path.split('.').reduce((acc, part) => acc?.[part], obj)
}

function interpolate(text: string, params?: Record<string, any>): string {
  if (!params) return text
  return text.replace(/\{([^}]+)\}/g, (_, key) => String(params[key] ?? `{${key}}`))
}

export function detectLocale(): string {
  if (typeof navigator === 'undefined') return 'en'
  return navigator.languages?.[0] ?? navigator.language ?? 'en'
}

export function detectTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return 'UTC'
  }
}

export function detectHourCycle(): boolean {
  try {
    const hourCycle = Intl.DateTimeFormat(undefined).resolvedOptions().hourCycle
    return hourCycle === 'h23' || hourCycle === 'h24'
  } catch {
    return false
  }
}

const locales: Record<string, Messages> = {
  en,
}

export function setLocale(locale: string): void {
  // Placeholder for future locale loading
  // For v1, only 'en' exists
}

export function t(key: string, params?: Record<string, any>): string {
  const locale = 'en' // In v1, always use English
  const messages = locales[locale] || locales['en']
  const value = getNestedValue(messages, key)

  if (typeof value !== 'string') {
    console.warn(`Missing translation key: ${key}`)
    return key
  }

  return interpolate(value, params)
}

export function formatNumber(
  value: number,
  options?: Intl.NumberFormatOptions & { locale?: string }
): string {
  const { locale = detectLocale(), ...opts } = options || {}
  return new Intl.NumberFormat(locale, opts).format(value)
}

export { detectLocale as getLocale, detectTimeZone as getTimeZone }
