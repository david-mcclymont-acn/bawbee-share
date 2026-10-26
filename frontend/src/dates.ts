// Dates arrive as ISO "2026-10-30". Parse them as local calendar days (never via UTC, which
// can shift a day), and format in en-GB.

export function parseDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1)
}

export function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function today(): Date {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

const dayMonth = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})
const longDate = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})
const monthName = new Intl.DateTimeFormat('en-GB', { month: 'long' })
const monthYear = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric' })
const shortMonthYear = new Intl.DateTimeFormat('en-GB', { month: 'short', year: 'numeric' })

/** "Fri 30 Oct" */
export const formatDay = (iso: string) => dayMonth.format(parseDate(iso))
/** "30 October 2026" */
export const formatLongDate = (iso: string) => longDate.format(parseDate(iso))
/** "October" */
export const formatMonth = (iso: string) => monthName.format(parseDate(iso))
/** "October 2026" */
export const formatMonthYear = (iso: string) => monthYear.format(parseDate(iso))
/** "Nov 2022" */
export const formatShortMonthYear = (iso: string) => shortMonthYear.format(parseDate(iso))

export function addMonths(year: number, month: number, delta: number): [number, number] {
  const index = year * 12 + (month - 1) + delta
  return [Math.floor(index / 12), (index % 12) + 1]
}

export function formatTimestamp(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso))
}
