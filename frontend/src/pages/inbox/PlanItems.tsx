import { Link } from 'react-router'
import { inkFor, shortName } from '../../accounts.ts'
import { api, useBudgetMutation, type Account, type InboxItem } from '../../api.ts'
import { formatDay, formatLongDate, formatMonthYear } from '../../dates.ts'
import { formatPence } from '../../money.ts'
import { Rosette } from '../../marks.tsx'
import { ErrorText, Money } from '../../ui.tsx'

/** The last answer given, so it can be taken back (as Inbox.tsx). */
export type Answer = { id: number; label: string; text: string; undoable: boolean; hide: boolean }

/** Put any item away without doing anything about it. It can be undone, and the same alert
 *  isn't raised again. */
export function DismissButton({
  item,
  label,
  disabled,
  onAnswered,
}: {
  item: InboxItem
  label: string
  disabled?: boolean
  onAnswered: (a: Answer) => void
}) {
  const dismiss = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/reject`))
  return (
    <>
      <button
        type="button"
        className="button button-quiet"
        disabled={disabled || dismiss.isPending}
        onClick={() =>
          dismiss.mutate(undefined, {
            onSuccess: () =>
              onAnswered({
                id: item.id,
                label,
                text: `${label} dismissed. Nothing in the budget changed.`,
                undoable: true,
                hide: true,
              }),
          })
        }
      >
        Dismiss<span className="visually-hidden">: {label}</span>
      </button>
      <ErrorText error={dismiss.error} />
    </>
  )
}

function title(item: InboxItem) {
  switch (item.kind) {
    case 'goal_adjust':
      return (
        <>
          {item.name}: needs <Money pence={item.needed_pence ?? 0} /> a month
        </>
      )
    case 'salary_mismatch':
      return (
        <>
          {item.name}’s pay: <Money pence={item.landed_pence ?? 0} />, not{' '}
          <Money pence={item.expected_pence ?? 0} />
        </>
      )
    case 'renewal_due':
      return (
        <>
          {item.name} renews {formatDay(item.renews_on ?? '')}
        </>
      )
    case 'remortgage_window':
      return (
        <>
          The {item.lender} fix ends {formatMonthYear(item.fix_end ?? '')}
          {item.fix_end_exact ? '' : ' (approx.)'}
        </>
      )
    default:
      return (
        <>
          {item.label} ends {formatMonthYear(item.end_month ?? '')}
        </>
      )
  }
}

function meta(item: InboxItem) {
  switch (item.kind) {
    case 'goal_adjust':
      return (
        <>
          The budget puts aside <Money pence={item.current_pence ?? 0} /> on {item.label}.{' '}
          <Money pence={item.target_pence ?? 0} /> is needed by{' '}
          {formatLongDate(item.target_date ?? '')}.
        </>
      )
    case 'salary_mismatch':
      return (
        <>
          Landed on {formatDay(item.on ?? '')} ({item.description}). The budget expects{' '}
          <Money pence={item.expected_pence ?? 0} />
          {item.budget_is_override ? ', its own figure for now.' : ', the calculated take-home.'} A
          one-off (overtime, a back-dated rise) can explain it.
        </>
      )
    case 'renewal_due':
      return (
        <>
          {item.provider ? `${item.provider}. ` : ''}
          {item.cost_pence ? (
            <>
              Last year <Money pence={item.cost_pence} />.{' '}
            </>
          ) : null}
          Time to compare prices before it renews.
        </>
      )
    case 'remortgage_window':
      return (
        <>
          About <Money pence={item.balance_at_fix_end_pence ?? 0} /> will be owed then
          {item.balance_anchored ? '' : ' (estimated, with no statement yet)'}. Offers can usually
          be held for six months, so now’s the time to look.
        </>
      )
    default:
      return (
        <>
          The {item.type ?? 'agreement'}’s last payment of <Money pence={item.amount_pence ?? 0} />{' '}
          is in {formatMonthYear(item.end_month ?? '')}
          {item.end_confirmed ? '' : ' (approx.)'}
          {item.final_payment_pence ? (
            <>
              , with an optional final payment of <Money pence={item.final_payment_pence} />
            </>
          ) : null}
          . From the month after, the forecast leaves it out.
        </>
      )
  }
}

export function PlanStrip({
  item,
  account,
  onAnswered,
}: {
  item: InboxItem
  account: Account | undefined
  onAnswered: (a: Answer) => void
}) {
  const take = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/take`))
  const done = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/done`))
  const reject = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/reject`))
  const snooze = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/snooze`, {}))
  const busy = take.isPending || done.isPending || reject.isPending || snooze.isPending
  const ink = inkFor(account)
  const label = item.name ?? item.label ?? item.lender ?? ''
  const answer = (text: string) =>
    onAnswered({ id: item.id, label, text, undoable: true, hide: true })
  const later = (
    <button
      type="button"
      className="button button-quiet"
      disabled={busy}
      onClick={() =>
        snooze.mutate(undefined, { onSuccess: () => answer(`${label}: back in a week.`) })
      }
    >
      Remind me in a week<span className="visually-hidden">: {label}</span>
    </button>
  )
  const finished = (text: string, what: string) => (
    <button
      type="button"
      className="button"
      disabled={busy}
      onClick={() => done.mutate(undefined, { onSuccess: () => answer(text) })}
    >
      {what}
      <span className="visually-hidden">: {label}</span>
    </button>
  )
  return (
    <li className="strip" data-ink={ink}>
      <div className="strip-body">
        {ink && <Rosette ink={ink} size={48} />}
        <div className="strip-what">
          {account && <span className="strip-account">{shortName(account.name)}</span>}
          <h3 className="strip-title">{title(item)}</h3>
          <p className="strip-meta">{meta(item)}</p>
        </div>
      </div>
      <ErrorText error={take.error ?? done.error ?? reject.error ?? snooze.error} />
      <div className="row-actions">
        {item.kind === 'goal_adjust' && (
          <>
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() =>
                take.mutate(undefined, {
                  onSuccess: () =>
                    answer(
                      `${item.label} is now ${formatPence(item.needed_pence ?? 0)} a month in the live budget.`,
                    ),
                })
              }
            >
              <span>
                Put aside <Money pence={item.needed_pence ?? 0} /> a month
              </span>
            </button>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() =>
                reject.mutate(undefined, {
                  onSuccess: () => answer(`${label} stays as it is. The goal shows as behind.`),
                })
              }
            >
              Leave it as it is<span className="visually-hidden">: {label}</span>
            </button>
          </>
        )}
        {item.kind === 'salary_mismatch' && (
          <>
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() =>
                take.mutate(undefined, {
                  onSuccess: () =>
                    answer(
                      `${item.name}’s take-home is now ${formatPence(item.landed_pence ?? 0)} in the live budget.`,
                    ),
                })
              }
            >
              <span>
                Use <Money pence={item.landed_pence ?? 0} /> as take-home
              </span>
            </button>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() =>
                reject.mutate(undefined, {
                  onSuccess: () => answer(`Noted as a one-off. ${item.name}’s take-home stays.`),
                })
              }
            >
              It was a one-off<span className="visually-hidden">: {label}</span>
            </button>
            <Link className="button button-quiet" to="/plan/take-home">
              See the calculation
            </Link>
          </>
        )}
        {item.kind === 'renewal_due' && (
          <>
            {finished(
              `${label}: done. It comes round again next year.`,
              'Done: renewed or switched',
            )}
            <Link className="button button-quiet" to="/plan/renewals">
              Change the renewal
            </Link>
            {later}
          </>
        )}
        {item.kind === 'remortgage_window' && (
          <>
            <Link className="button button-primary" to="/plan/mortgage#remortgage">
              Make a remortgage what-if
            </Link>
            {finished('Noted. The mortgage screen keeps the countdown.', 'Done for now')}
            {later}
          </>
        )}
        {item.kind === 'agreement_ending' && (
          <>
            <Link className="button button-primary" to="/versions">
              Make a what-if for what’s next
            </Link>
            <Link className="button" to="/plan/forecast#one-offs">
              Add the final payment
            </Link>
            {finished('Noted. The forecast already leaves it out after it ends.', 'Done for now')}
          </>
        )}
        {['renewal_due', 'remortgage_window', 'agreement_ending'].includes(item.kind) && (
          <DismissButton item={item} label={label} disabled={busy} onAnswered={onAnswered} />
        )}
      </div>
    </li>
  )
}
