import { formatShortMonthYear } from '../../dates.ts'

/** "2027-04-01" -> "2027-04" for <input type="month">, and back. */
export const toMonthInput = (iso: string) => iso.slice(0, 7)
export const fromMonthInput = (value: string) => (value ? `${value}-01` : '')

export const shortMonth = (iso: string) => formatShortMonthYear(iso)

/** Months from the 1st of one month to the 1st of another. */
export function monthsBetween(from: string, to: string): number {
  const [fy, fm] = from.split('-').map(Number) as [number, number]
  const [ty, tm] = to.split('-').map(Number) as [number, number]
  return (ty - fy) * 12 + (tm - fm)
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Dash patterns that tell forecast paths apart without borrowing an account's ink. */
export const PATH_DASHES = [undefined, '7 4', '2 3', '12 3 2 3'] as const
export const PATH_STYLE_NAMES = ['solid', 'dashed', 'dotted', 'dash-dot'] as const
