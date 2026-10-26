import { useLines, useVersions, type Line } from './api.ts'
import { formatPence } from './money.ts'

function lineLabel(v: { name: string; provider: string | null }) {
  return v.provider ? `${v.name} (${v.provider})` : v.name
}

/** Said under a bill picker: only bills can be matched to payments. */
export function NotListed() {
  return (
    <>
      Not listed? Only bills are. If it’s in the budget as a transfer or allocation, change its type
      in the Budget first.
    </>
  )
}

const byLabel = (a: Line, b: Line) => lineLabel(a.values).localeCompare(lineLabel(b.values))

/** The live budget's bills, those paid from `account` first: for matching a payment, or a
 *  payee, to one. */
export function BillLineSelect({
  id,
  account,
  value,
  onChange,
  none,
  describedBy,
}: {
  id: string
  account: string | undefined
  value: string
  onChange: (lineId: string) => void
  /** The empty choice's wording. */
  none: string
  describedBy?: string
}) {
  const versions = useVersions()
  const live = versions.data?.find((v) => v.type === 'live')
  const lines = useLines(live?.id)
  const bills = (lines.data ?? []).filter((l) => l.values.kind === 'bill')
  const here = bills.filter((l) => l.values.account === account).sort(byLabel)
  const elsewhere = bills.filter((l) => l.values.account !== account).sort(byLabel)
  const option = (l: Line) => (
    <option key={l.line_id} value={l.line_id}>
      {lineLabel(l.values)} · {formatPence(l.monthly_pence)}
    </option>
  )
  return (
    <select
      id={id}
      value={value}
      aria-describedby={describedBy}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{none}</option>
      <optgroup label="Paid from this account">{here.map(option)}</optgroup>
      <optgroup label="Other accounts">{elsewhere.map(option)}</optgroup>
    </select>
  )
}
