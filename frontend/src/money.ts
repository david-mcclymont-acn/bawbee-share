// Money is integer pence everywhere; this is the only place it becomes text (PLAN.md §6).

const gbp = new Intl.NumberFormat('en-GB', {
  style: 'currency',
  currency: 'GBP',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** 119246 -> "£1,192.46"; -13005 -> "−£130.05" (a true minus sign). */
export function formatPence(pence: number): string {
  if (!Number.isInteger(pence)) throw new Error(`Not whole pence: ${pence}`)
  const text = gbp.format(Math.abs(pence) / 100)
  return pence < 0 ? `−${text}` : text
}

/** "1192.46" or "£1,192.46" -> 119246. Parses the digits, so no float rounding creeps in. */
export function parsePounds(input: string): number | null {
  const cleaned = input.replace(/[£,\s]/g, '')
  const match = /^(\d+)(?:\.(\d{0,2}))?$/.exec(cleaned)
  if (!match) return null
  const whole = Number(match[1])
  const fraction = Number((match[2] ?? '').padEnd(2, '0'))
  return whole * 100 + fraction
}

/** 119246 -> "1192.46": what goes in an input, or on the clipboard for a bank app. */
export function penceToInput(pence: number | null | undefined): string {
  if (pence == null) return ''
  return `${Math.trunc(pence / 100)}.${String(Math.abs(pence % 100)).padStart(2, '0')}`
}
