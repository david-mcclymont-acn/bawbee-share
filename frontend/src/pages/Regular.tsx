import { useState } from 'react'
import { Link } from 'react-router'
import { inkFor, shortName } from '../accounts.ts'
import {
  api,
  useAccounts,
  useBudgetMutation,
  useRegularPayments,
  type RegularPayment,
} from '../api.ts'
import { formatDay, formatLongDate } from '../dates.ts'
import { BillLineSelect, NotListed } from '../lines.tsx'
import { Rosette } from '../marks.tsx'
import { ErrorText, Field, Loading, Money, PageHeading } from '../ui.tsx'

const CADENCE: Record<RegularPayment['cadence'], string> = {
  weekly: 'a week',
  monthly: 'a month',
  quarterly: 'a quarter',
  annual: 'a year',
}

function ordinal(n: number): string {
  const tens = n % 100
  if (tens >= 11 && tens <= 13) return `${n}th`
  return `${n}${({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'}`
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Which bill this payee is: for one the matcher missed, or matched to the wrong line. */
function LinkForm({ r, onClose }: { r: RegularPayment; onClose: () => void }) {
  const [lineId, setLineId] = useState(r.line_id ? String(r.line_id) : '')
  const link = useBudgetMutation(() =>
    api.post(`/api/recurring/${r.id}/link`, { line_id: Number(lineId) }),
  )
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault()
        if (lineId) link.mutate(undefined, { onSuccess: onClose })
      }}
    >
      <Field label={`Which bill is ${r.payee}?`} hint={<NotListed />}>
        {(id, hintId) => (
          <BillLineSelect
            id={id}
            account={r.account}
            value={lineId}
            onChange={setLineId}
            none="Choose a bill"
            describedBy={hintId}
          />
        )}
      </Field>
      <p className="field-hint">
        All of {r.payee}’s payments on this account move to it, and the next ones follow.
      </p>
      <ErrorText error={link.error} />
      <div className="row-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={!lineId || String(r.line_id) === lineId || link.isPending}
        >
          {link.isPending ? 'Linking…' : 'Match it to this bill'}
        </button>
        <button type="button" className="button button-quiet" onClick={onClose}>
          Back
        </button>
      </div>
    </form>
  )
}

function Status({ r, onLink }: { r: RegularPayment; onLink: () => void }) {
  const suggest = useBudgetMutation(() => api.post(`/api/recurring/${r.id}/suggest`))
  const linkButton = (text: string) => (
    <button type="button" className="ledger-revert" onClick={onLink}>
      {text}
      <span className="visually-hidden">: {r.payee}</span>
    </button>
  )
  if (r.line_label) {
    return (
      <span className="regular-line">
        {r.line_label.toLowerCase() === r.payee.toLowerCase()
          ? 'In the budget'
          : `In the budget as ${r.line_label}`}{' '}
        · {linkButton('Wrong bill?')}
      </span>
    )
  }
  if (r.status === 'ignored') {
    return (
      <span className="regular-off">
        Set aside as not a bill ·{' '}
        <button
          type="button"
          className="ledger-revert"
          disabled={suggest.isPending}
          onClick={() => suggest.mutate(undefined)}
        >
          Suggest it again<span className="visually-hidden">: {r.payee}</span>
        </button>{' '}
        · {linkButton('It’s a bill in the budget')}
      </span>
    )
  }
  return (
    <span className="regular-new">
      Not in the budget · <Link to="/inbox#regulars-title">Decide in the inbox</Link> ·{' '}
      {linkButton('It’s a bill in the budget')}
    </span>
  )
}

/** Every payee paid on a steady rhythm, as the bank data shows it. */
export default function Regular() {
  const regular = useRegularPayments()
  const accounts = useAccounts()
  const [linking, setLinking] = useState<number | null>(null)
  if (regular.isPending || accounts.isPending) return <Loading what="regular payments" />
  if (regular.isError) return <ErrorText error={regular.error} />
  const rows = regular.data
  const groups = (accounts.data ?? [])
    .map((a) => ({ account: a, rows: rows.filter((r) => r.account === a.key) }))
    .filter((g) => g.rows.length > 0)

  return (
    <>
      <PageHeading
        title="Regular payments"
        lead="Payees paid on a steady rhythm, as the bank data shows them: the bills the budget knows, and any it doesn’t."
      />
      {rows.length === 0 ? (
        <p className="empty">
          Nothing yet. It takes three payments to see a rhythm, so this fills in as bank data comes
          in. <Link to="/banks">Bank links</Link>
        </p>
      ) : (
        groups.map(({ account, rows: list }) => {
          const ink = inkFor(account)
          const monthly = list
            .filter((r) => r.cadence === 'monthly' && r.status !== 'ignored')
            .reduce((sum, r) => sum + r.amount_pence, 0)
          return (
            <section
              key={account.key}
              className="register regular-register"
              data-ink={ink}
              aria-labelledby={`regular-${account.key}`}
            >
              <div className="regular-head">
                <span className="regular-mark" aria-hidden="true">
                  {ink && <Rosette ink={ink} size={40} />}
                </span>
                <h2 id={`regular-${account.key}`}>{shortName(account.name)}</h2>
                <span className="section-aside">
                  {plural(list.length, 'regular payment', 'regular payments')} ·{' '}
                  <Money pence={monthly} /> a month
                </span>
              </div>
              <ul className="regular-list">
                {list.map((r) => (
                  <li key={r.id}>
                    <div className="regular-what">
                      <span className="regular-payee">{r.payee}</span>
                      <Status r={r} onLink={() => setLinking(r.id)} />
                      {r.previous_amount_pence !== null && r.price_changed_on && (
                        <span className="regular-change">
                          Was <Money pence={r.previous_amount_pence} /> until{' '}
                          {formatDay(r.price_changed_on)}
                        </span>
                      )}
                      {linking === r.id && <LinkForm r={r} onClose={() => setLinking(null)} />}
                    </div>
                    <span className="regular-amount">
                      <span>
                        <Money pence={r.amount_pence} /> {CADENCE[r.cadence]}
                      </span>
                      <span className="regular-when">
                        {r.day_of_month && r.cadence !== 'weekly'
                          ? `around the ${ordinal(r.day_of_month)} · `
                          : ''}
                        last {formatLongDate(r.last_seen)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )
        })
      )}
    </>
  )
}
