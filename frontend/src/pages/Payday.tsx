import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { inkFor, personName, shortName } from '../accounts.ts'
import {
  api,
  keys,
  useAccounts,
  useBudgetMutation,
  usePayday,
  type Account,
  type Payday as PaydayView,
  type PaydayTransfer,
} from '../api.ts'
import { useAuth } from '../auth.tsx'
import { addMonths, formatDay, formatMonth, formatTimestamp, isoDate, today } from '../dates.ts'
import { IconChevron, IconCopy, IconTick } from '../icons.tsx'
import { CoinMark, Rosette } from '../marks.tsx'
import { formatPence, parsePounds, penceToInput } from '../money.ts'
import { ErrorText, Loading, Money, MoneyInput, PageHeading, SectionHeading } from '../ui.tsx'

function useMonth(): [number, number, (delta: number) => void] {
  const now = today()
  const [ym, setYm] = useState<[number, number]>([now.getFullYear(), now.getMonth() + 1])
  return [ym[0], ym[1], (delta) => setYm(addMonths(ym[0], ym[1], delta))]
}

function monthLabel(year: number, month: number): string {
  return formatMonth(isoDate(new Date(year, month - 1, 1)))
}

function CopyButton({ pence, label }: { pence: number; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="button button-quiet"
      onClick={() => {
        void navigator.clipboard?.writeText(penceToInput(pence)).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1600)
        })
      }}
      aria-label={`Copy ${label} amount, ${formatPence(pence)}`}
    >
      <IconCopy size={18} />
      <span aria-live="polite">{copied ? 'Copied' : 'Copy'}</span>
    </button>
  )
}

/** Why this payday's figure is what it is, in words (never just "suggested"). */
function Reason({ transfer }: { transfer: PaydayTransfer }) {
  if (transfer.basis === 'changed') {
    return (
      <>
        Changed for this payday · the budget says <Money pence={transfer.budget_pence} />
      </>
    )
  }
  if (transfer.basis === 'fixed') {
    return (
      <>
        Fixed in the budget · the{' '}
        {transfer.rule === 'joint_share' ? 'calculated share' : 'calculated amount'} would be{' '}
        <Money pence={transfer.suggested_pence} />
      </>
    )
  }
  if (transfer.rule === 'joint_share')
    return <>A share of the joint bills, rounded up, plus the buffer</>
  if (transfer.rule === 'cover_account_lines') return <>Covers that account’s bills, rounded up</>
  return null
}

function TransferRow({
  transfer,
  accounts,
  onTick,
  onAmount,
  busy,
}: {
  transfer: PaydayTransfer
  accounts: Map<string, Account>
  onTick: (done: boolean) => void
  onAmount: (pence: number | null) => void
  busy: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const from = accounts.get(transfer.from_account)
  const to = accounts.get(transfer.to_account)
  const fromInk = inkFor(from)
  const toInk = inkFor(to)
  const fromName = shortName(from?.name ?? transfer.from_account)
  const toName = shortName(to?.name ?? transfer.to_account)
  const done = transfer.done_at !== null
  const label = `${fromName} to ${toName}`
  const parsed = parsePounds(value)

  return (
    <li className="transfer" data-done={done || undefined}>
      <button
        type="button"
        className="tick"
        role="checkbox"
        aria-checked={done}
        aria-label={`Sent ${formatPence(transfer.effective_pence)} from ${label}`}
        disabled={busy}
        onClick={() => onTick(!done)}
      >
        {done && <IconTick size={20} />}
      </button>
      <span className="transfer-notes" aria-hidden="true">
        {fromInk && (
          <span data-ink={fromInk}>
            <Rosette ink={fromInk} size={32} />
          </span>
        )}
        <svg className="transfer-arrow" width="16" height="10" viewBox="0 0 16 10">
          <path d="M0 5h14M10 1l4 4-4 4" />
        </svg>
        {toInk && (
          <span data-ink={toInk}>
            <Rosette ink={toInk} size={32} />
          </span>
        )}
      </span>
      <div className="transfer-what">
        <span className="transfer-to" data-ink={toInk}>
          To {toName}
        </span>
        <span className="transfer-from">
          from <span data-ink={fromInk}>{fromName}</span>
        </span>
        {done && transfer.done_via === 'bank' && transfer.done_at && (
          <span className="transfer-done">
            Sent · seen in the bank on {formatDay(transfer.done_at.slice(0, 10))}
          </span>
        )}
        {done && transfer.done_via !== 'bank' && transfer.done_by && transfer.done_at && (
          <span className="transfer-done">
            Sent · ticked by {transfer.done_by}, {formatTimestamp(transfer.done_at)}
          </span>
        )}
      </div>
      <div className="transfer-amount">
        <span className="transfer-send">Send</span>
        <Money pence={transfer.effective_pence} className="money-large" />
      </div>
      <p className="transfer-reason">
        <Reason transfer={transfer} />
      </p>
      <div className="transfer-actions">
        <CopyButton pence={transfer.effective_pence} label={label} />
        <button
          type="button"
          className="button button-quiet"
          onClick={() => {
            setValue(penceToInput(transfer.effective_pence))
            setEditing((v) => !v)
          }}
          aria-expanded={editing}
        >
          Change amount
        </button>
      </div>
      {editing && (
        <form
          className="transfer-edit"
          onSubmit={(e) => {
            e.preventDefault()
            if (parsed === null) return
            onAmount(parsed === transfer.suggested_pence ? null : parsed)
            setEditing(false)
          }}
        >
          <label>
            Amount sent this payday
            <MoneyInput value={value} onChange={setValue} autoFocus />
          </label>
          <button className="button" type="submit" disabled={parsed === null}>
            Save
          </button>
          {transfer.basis === 'changed' && (
            <button
              type="button"
              className="button button-quiet"
              onClick={() => {
                onAmount(
                  transfer.budget_pence === transfer.suggested_pence ? null : transfer.budget_pence,
                )
                setEditing(false)
              }}
            >
              Use the budget’s {formatPence(transfer.budget_pence)}
            </button>
          )}
        </form>
      )}
    </li>
  )
}

export default function Payday() {
  const { me } = useAuth()
  const [year, month, move] = useMonth()
  const payday = usePayday(year, month)
  const accounts = useAccounts()
  const client = useQueryClient()

  const update = useBudgetMutation(
    async ({ transfer, change }: { transfer: PaydayTransfer; change: Record<string, unknown> }) => {
      let id = transfer.id
      if (id === null) {
        // The first tick starts this payday's run, which fixes its amounts.
        const view = await api.post<PaydayView>(`/api/payday/${year}/${month}/start`)
        id =
          view.transfers.find((t) => t.line_id === transfer.line_id && t.name === transfer.name)
            ?.id ?? null
      }
      if (id === null) throw new Error('That transfer is no longer in the budget. Reload the page.')
      return api.patch<PaydayView>(`/api/payday/transfers/${id}`, change)
    },
  )

  if (payday.isPending || accounts.isPending) return <Loading what="payday" />
  if (payday.isError) return <ErrorText error={payday.error} />
  const view = payday.data
  const byKey = new Map((accounts.data ?? []).map((a) => [a.key, a]))
  const [prevY, prevM] = addMonths(year, month, -1)
  const [nextY, nextM] = addMonths(year, month, 1)

  const mine = me?.person
  const owners = [...new Set(view.transfers.map((t) => t.from_owner ?? 'joint'))].sort((a, b) =>
    a === mine ? -1 : b === mine ? 1 : a.localeCompare(b),
  )
  const groups = owners.map((owner) => {
    const rows = view.transfers.filter((t) => (t.from_owner ?? 'joint') === owner)
    return { owner, rows, done: rows.filter((t) => t.done_at).length }
  })
  const left = groups.filter((g) => g.done < g.rows.length)
  const save = (t: PaydayTransfer, change: Record<string, unknown>) =>
    update.mutate(
      { transfer: t, change },
      { onSuccess: (v) => client.setQueryData(keys.payday(year, month), v) },
    )

  return (
    <>
      <PageHeading
        title={`${formatMonth(view.month)} payday`}
        lead={`For ${formatMonth(view.funds_month)}’s bills. Transfers go on ${formatDay(view.transfer_date)}, once both salaries have landed.`}
      >
        <nav className="month-step" aria-label="Other paydays">
          <button type="button" className="button button-quiet" onClick={() => move(-1)}>
            <IconChevron dir="left" />
            {monthLabel(prevY, prevM)}
          </button>
          <button type="button" className="button button-quiet" onClick={() => move(1)}>
            {monthLabel(nextY, nextM)}
            <IconChevron />
          </button>
        </nav>
      </PageHeading>

      <dl className="pay-dates">
        {Object.entries(view.pay_dates).map(([person, d]) => (
          <div key={person}>
            <dt>{personName(person)}’s pay day</dt>
            <dd>
              {d.expected !== d.official ? (
                <>
                  usually {formatDay(d.expected)}
                  <span className="pay-official"> (officially {formatDay(d.official)})</span>
                </>
              ) : (
                formatDay(d.expected)
              )}
            </dd>
          </div>
        ))}
      </dl>

      {client.isMutating() > 0 && (
        <span className="visually-hidden" role="status">
          Saving…
        </span>
      )}
      <ErrorText error={update.error} />

      {view.transfers.length === 0 && <p className="empty">Nothing to move this payday.</p>}

      {groups.map(({ owner, rows, done }) => {
        const own = owner === mine
        const title = own
          ? 'Your transfers'
          : owner === 'joint'
            ? 'From the joint account'
            : `${personName(owner)}’s transfers`
        return (
          <section key={owner} className="register" aria-label={title}>
            <SectionHeading
              title={title}
              aside={
                <>
                  <Money pence={rows.reduce((sum, t) => sum + t.effective_pence, 0)} /> in total
                </>
              }
            />
            <ul className="transfer-list">
              {rows.map((t) => (
                <TransferRow
                  key={`${t.line_id}-${t.name}`}
                  transfer={t}
                  accounts={byKey}
                  busy={update.isPending}
                  onTick={(isDone) => save(t, { done: isDone })}
                  onAmount={(pence) => save(t, { actual_pence: pence })}
                />
              ))}
            </ul>
            <p
              className="group-progress"
              role="status"
              data-complete={done === rows.length || undefined}
            >
              {done === rows.length
                ? own
                  ? 'Yours are all sent.'
                  : `${personName(owner)}’s are all sent.`
                : `${done} of ${rows.length} sent`}
            </p>
          </section>
        )
      })}

      {view.transfers.length > 0 && (
        <div className="payday-foot" role="status" data-complete={view.complete || undefined}>
          {view.complete ? (
            <>
              <CoinMark size={36} />
              <p>
                <strong>Aw done!</strong> Everything’s moved for {formatMonth(view.funds_month)}.
              </p>
            </>
          ) : (
            <p>
              {left
                .map(
                  (g) =>
                    `${g.owner === mine ? 'You have' : `${personName(g.owner)} has`} ${g.rows.length - g.done} to go`,
                )
                .join(' · ')}
              {view.run_id === null &&
                '. The amounts follow the budget until the first transfer is ticked; then they’re fixed for this payday.'}
            </p>
          )}
        </div>
      )}
    </>
  )
}
