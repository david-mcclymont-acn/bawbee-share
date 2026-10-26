import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { inkFor, shortName } from '../accounts.ts'
import {
  api,
  useAccounts,
  useBudgetMutation,
  useCategories,
  useInbox,
  useSummary,
  useVersions,
  type Account,
  type BankTransaction,
  type FollowUp,
  type InboxItem,
  type OverridingWhatIf,
  type Reminder,
} from '../api.ts'
import { formatDay, formatLongDate, formatShortMonthYear, parseDate, today } from '../dates.ts'
import { Rosette } from '../marks.tsx'
import { PLAN_KINDS } from '../plan.ts'
import { BillLineSelect, NotListed } from '../lines.tsx'
import { DismissButton, PlanStrip } from './inbox/PlanItems.tsx'
import { formatPence, parsePounds, penceToInput } from '../money.ts'
import {
  ErrorText,
  Field,
  Loading,
  Money,
  MoneyInput,
  Notice,
  PageHeading,
  SectionHeading,
} from '../ui.tsx'

const REASONS: Record<FollowUp['reason'], string> = {
  cancel: 'Cancel this',
  check_price: 'Check the price',
  check_account: 'Check which account it’s paid from',
  check: 'Follow up',
  other: 'Follow up',
}

const PER: Record<string, string> = {
  monthly: 'a month',
  annual: 'a year',
  quarterly: 'a quarter',
  weekly: 'a week',
}

/** The last answer given, so it can be taken back. */
type Answer = { id: number; label: string; text: string; undoable: boolean; hide: boolean }

function Arrow() {
  return (
    <svg className="strip-arrow" width="16" height="10" viewBox="0 0 16 10" aria-hidden="true">
      <path d="M0 5h14M10 1l4 4-4 4" />
    </svg>
  )
}

// ---- what each overriding what-if does with the new price -------------------------------

function WhatIfChoice({
  item,
  whatIf,
  onDone,
}: {
  item: InboxItem
  whatIf: OverridingWhatIf
  onDone: () => void
}) {
  // Nothing chosen to start with: a what-if never drifts from live by default.
  const [choice, setChoice] = useState<'keep' | 'take' | 'set' | null>(null)
  const [amount, setAmount] = useState('')
  const decide = useBudgetMutation(() =>
    api.post(`/api/inbox/${item.id}/what-ifs`, {
      version_id: whatIf.version_id,
      decision: choice,
      amount_pence: choice === 'set' ? parsePounds(amount) : null,
    }),
  )
  const name = `choice-${item.id}-${whatIf.version_id}`
  const own = whatIf.override_pence
  const valid = choice !== null && (choice !== 'set' || parsePounds(amount) !== null)
  return (
    <fieldset className="decision">
      <legend>
        <strong>{whatIf.name}</strong> sets its own{' '}
        {own !== null ? <Money pence={own} /> : 'amount'}. What should it use now?
      </legend>
      <label className="choice">
        <input
          type="radio"
          name={name}
          checked={choice === 'take'}
          onChange={() => setChoice('take')}
        />
        <span>
          Follow live: <Money pence={item.new_pence ?? 0} />
        </span>
      </label>
      <label className="choice">
        <input
          type="radio"
          name={name}
          checked={choice === 'keep'}
          onChange={() => setChoice('keep')}
        />
        <span>Keep its own{own !== null && <>: {formatPence(own)}</>}</span>
      </label>
      <label className="choice">
        <input
          type="radio"
          name={name}
          checked={choice === 'set'}
          onChange={() => setChoice('set')}
        />
        <span>Set another amount</span>
      </label>
      {choice === 'set' && (
        <div className="decision-amount">
          <MoneyInput
            value={amount}
            onChange={setAmount}
            label={`Amount for ${whatIf.name}`}
            autoFocus
          />
        </div>
      )}
      <ErrorText error={decide.error} />
      <div className="row-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={!valid || decide.isPending}
          onClick={() => decide.mutate(undefined, { onSuccess: onDone })}
        >
          {decide.isPending ? 'Saving…' : `Save for ${whatIf.name}`}
        </button>
      </div>
    </fieldset>
  )
}

// ---- a price change, as a register strip --------------------------------------------------

function Effect({
  item,
  account,
  jointTotal,
}: {
  item: InboxItem
  account: Account | undefined
  jointTotal: number | undefined
}) {
  const delta = (item.new_pence ?? 0) - (item.old_pence ?? 0)
  const more = delta >= 0 ? 'more' : 'less'
  const name = shortName(account?.name ?? item.account ?? '')
  if (item.frequency !== 'monthly') {
    return (
      <>
        {name} pays <Money pence={Math.abs(delta)} /> {more} {PER[item.frequency ?? ''] ?? ''}.
      </>
    )
  }
  if (account?.role === 'joint_bills' && jointTotal !== undefined) {
    return (
      <>
        {name} will need <Money pence={jointTotal + delta} /> a month,{' '}
        <Money pence={Math.abs(delta)} /> {more}, and both transfers into it are worked out again.
      </>
    )
  }
  return (
    <>
      {name} pays out <Money pence={Math.abs(delta)} /> {more} a month.
    </>
  )
}

function PriceChange({
  item,
  account,
  jointTotal,
  onAnswered,
}: {
  item: InboxItem
  account: Account | undefined
  jointTotal: number | undefined
  onAnswered: (a: Answer) => void
}) {
  const [pending, setPending] = useState<OverridingWhatIf[] | null>(null)
  const approve = useBudgetMutation(() => api.post<InboxItem>(`/api/inbox/${item.id}/approve`, {}))
  const reject = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/reject`))
  const snooze = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/snooze`, {}))
  const busy = approve.isPending || reject.isPending || snooze.isPending
  const legend = useRef<HTMLHeadingElement>(null)

  // Approved with what-ifs to settle: from the server after a refresh, or just now.
  const waiting = item.status === 'approved' ? item.pending : (pending ?? [])
  const deciding = item.status === 'approved' || (pending !== null && pending.length > 0)
  useEffect(() => {
    if (pending?.length) legend.current?.focus()
  }, [pending])

  const ink = inkFor(account)
  const old = item.old_pence ?? 0
  const now = item.new_pence ?? 0
  const per = PER[item.frequency ?? 'monthly'] ?? ''
  const seen = item.seen_on ? formatDay(item.seen_on) : ''
  const name = shortName(account?.name ?? item.account ?? '')
  const whatIfs = deciding ? waiting : item.overriding
  // Answered with nothing left to choose: the page hides the strip at once.
  const done = (a: Omit<Answer, 'hide' | 'label'>) =>
    onAnswered({ ...a, label: item.label ?? '', hide: true })

  return (
    <li className="strip" data-ink={ink}>
      <div className="strip-body">
        {ink && <Rosette ink={ink} size={48} />}
        <div className="strip-what">
          <span className="strip-account">{name}</span>
          <h3 className="strip-title">{item.label}</h3>
          <p className="strip-change">
            <Money pence={old} /> <Arrow />
            <span className="visually-hidden">to</span> <Money pence={now} /> {per}
            <span className="strip-delta" data-dir={now > old ? 'up' : 'down'}>
              {now > old ? 'up' : 'down'} <Money pence={Math.abs(now - old)} />
            </span>
          </p>
          <p className="strip-meta">
            {item.component_old_pence !== undefined && item.component_new_pence !== undefined && (
              <>
                One of its payments went from <Money pence={item.component_old_pence} /> to{' '}
                <Money pence={item.component_new_pence} />.{' '}
              </>
            )}
            Paid on {seen}.
          </p>
          {!deciding && (
            <p className="strip-effect">
              <span className="strip-effect-label">If you approve:</span>{' '}
              <Effect item={item} account={account} jointTotal={jointTotal} />
              {whatIfs.length > 0 &&
                ` ${whatIfs.map((o) => o.name).join(' and ')} ${whatIfs.length === 1 ? 'sets its' : 'set their'} own amount, so you’ll choose for ${whatIfs.length === 1 ? 'it' : 'them'} next.`}
            </p>
          )}
        </div>
      </div>

      {deciding ? (
        <div className="decisions">
          <div className="decisions-head">
            <h4 ref={legend} tabIndex={-1}>
              Approved from {seen}. Live now pays <Money pence={now} />.
            </h4>
            <UndoButton
              id={item.id}
              label={item.label ?? ''}
              onUndone={() => {
                setPending(null)
                onAnswered({
                  id: item.id,
                  text: `${item.label} is back as it was.`,
                  undoable: false,
                  hide: false,
                  label: item.label ?? '',
                })
              }}
            />
          </div>
          {waiting.map((w) => (
            <WhatIfChoice
              key={w.version_id}
              item={item}
              whatIf={w}
              onDone={() => {
                const rest = waiting.filter((x) => x.version_id !== w.version_id)
                setPending(rest)
                if (rest.length === 0)
                  done({
                    id: item.id,
                    text: `${item.label} is up to date everywhere.`,
                    undoable: false,
                  })
              }}
            />
          ))}
        </div>
      ) : (
        <>
          <ErrorText error={approve.error ?? reject.error ?? snooze.error} />
          <div className="row-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() =>
                approve.mutate(undefined, {
                  onSuccess: (r) => {
                    setPending(r.pending)
                    if (r.pending.length === 0)
                      done({
                        id: item.id,
                        text: `${item.label} is now ${formatPence(now)} ${per} on live, from ${seen}.`,
                        undoable: true,
                      })
                  },
                })
              }
            >
              Approve from {seen}
              <span className="visually-hidden">: {item.label}</span>
            </button>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() =>
                reject.mutate(undefined, {
                  onSuccess: () =>
                    done({
                      id: item.id,
                      text: `${item.label} stays at ${formatPence(old)} in the budget.`,
                      undoable: true,
                    }),
                })
              }
            >
              Not a change<span className="visually-hidden">: {item.label}</span>
            </button>
            <button
              type="button"
              className="button button-quiet"
              disabled={busy}
              onClick={() =>
                snooze.mutate(undefined, {
                  onSuccess: () =>
                    done({
                      id: item.id,
                      text: `${item.label} is snoozed for a week.`,
                      undoable: true,
                    }),
                })
              }
            >
              Snooze a week<span className="visually-hidden">: {item.label}</span>
            </button>
          </div>
        </>
      )}
    </li>
  )
}

function UndoButton({ id, label, onUndone }: { id: number; label: string; onUndone: () => void }) {
  const undo = useBudgetMutation(() => api.post(`/api/inbox/${id}/undo`))
  return (
    <span className="undo">
      <button
        type="button"
        className="button"
        disabled={undo.isPending}
        onClick={() => undo.mutate(undefined, { onSuccess: onUndone })}
      >
        Undo<span className="visually-hidden">: {label}</span>
      </button>
      <ErrorText error={undo.error} />
    </span>
  )
}

// ---- regular payments the budget doesn't know ------------------------------------------

const CADENCE: Record<string, string> = {
  weekly: 'every week',
  monthly: 'every month',
  quarterly: 'every quarter',
  annual: 'every year',
}
const FREQUENCY: Record<string, string> = {
  weekly: 'weekly',
  monthly: 'monthly',
  quarterly: 'quarterly',
  annual: 'annual',
}

function ordinal(n: number): string {
  const tens = n % 100
  const suffix =
    tens >= 11 && tens <= 13
      ? 'th'
      : (({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th')
  return `${n}${suffix}`
}

/** What an answer does to the money, said before it's given. */
function Consequence({
  prefix,
  account,
  pence,
  monthly,
  jointTotal,
}: {
  prefix: string
  account: Account | undefined
  pence: number
  monthly: boolean
  jointTotal: number | undefined
}) {
  const name = shortName(account?.name ?? '')
  const more = pence >= 0 ? 'more' : 'less'
  return (
    <p className="strip-effect">
      <span className="strip-effect-label">{prefix}</span>{' '}
      {!monthly ? (
        <>{name} counts it at its monthly share, and the transfers are worked out again.</>
      ) : account?.role === 'joint_bills' && jointTotal !== undefined ? (
        <>
          Joint Billing will need <Money pence={jointTotal + pence} /> a month,{' '}
          <Money pence={Math.abs(pence)} /> {more}, and both transfers into it are worked out again.
        </>
      ) : (
        <>
          {name} pays out <Money pence={Math.abs(pence)} /> {more} a month.
        </>
      )}
    </p>
  )
}

function NewRegular({
  item,
  account,
  jointTotal,
  onAnswered,
}: {
  item: InboxItem
  account: Account | undefined
  jointTotal: number | undefined
  onAnswered: (a: Answer) => void
}) {
  const categories = useCategories()
  const [adding, setAdding] = useState(false)
  const opener = useRef<HTMLButtonElement>(null)
  const [name, setName] = useState(item.merchant ?? '')
  const [provider, setProvider] = useState('')
  const [category, setCategory] = useState('')
  const [amount, setAmount] = useState(penceToInput(item.amount_pence))
  const [day, setDay] = useState(item.day_of_month ? String(item.day_of_month) : '')
  const done = (text: string) =>
    onAnswered({ id: item.id, label: item.merchant ?? '', text, undoable: true, hide: true })
  const accept = useBudgetMutation(() =>
    api.post(`/api/inbox/${item.id}/accept`, {
      name: name.trim(),
      provider: provider.trim() || null,
      category: category.trim() || null,
      amount_pence: parsePounds(amount),
      frequency: FREQUENCY[item.cadence ?? 'monthly'],
      day_of_month: day ? Number(day) : null,
    }),
  )
  const [linking, setLinking] = useState(false)
  const [lineId, setLineId] = useState('')
  const link = useBudgetMutation((to: number) =>
    api.post(`/api/inbox/${item.id}/link`, { line_id: to }),
  )
  const ignore = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/ignore`))
  const busy = accept.isPending || link.isPending || ignore.isPending
  const ink = inkFor(account)
  const monthly = item.cadence === 'monthly'
  const dayOk = !day || (Number(day) >= 1 && Number(day) <= 31)
  const valid = name.trim() && parsePounds(amount) !== null && dayOk
  return (
    <li className="strip" data-ink={ink}>
      <div className="strip-body">
        {ink && <Rosette ink={ink} size={48} />}
        <div className="strip-what">
          <span className="strip-account">{shortName(account?.name ?? item.account ?? '')}</span>
          <h3 className="strip-title">{item.merchant}</h3>
          <p className="strip-change">
            <Money pence={item.amount_pence ?? 0} /> {CADENCE[item.cadence ?? 'monthly']}
            {item.day_of_month && item.cadence !== 'weekly' && (
              <span className="strip-when">around the {ordinal(item.day_of_month)}</span>
            )}
          </p>
          <p className="strip-meta">
            Paid {item.occurrences} times since {formatLongDate(item.first_seen ?? '')}, last on{' '}
            {formatDay(item.last_seen ?? '')}. It isn’t in the budget.
            {item.previous_amount_pence && item.price_changed_on && (
              <>
                {' '}
                It was <Money pence={item.previous_amount_pence} /> until{' '}
                {formatDay(item.price_changed_on)}.
              </>
            )}
            {item.likely_label && (
              <>
                {' '}
                Same price as {item.likely_label}, which nothing pays yet: it may be that bill under
                another name.
              </>
            )}
          </p>
          {!adding && !linking && (
            <Consequence
              prefix="If you add it:"
              account={account}
              pence={item.amount_pence ?? 0}
              monthly={monthly}
              jointTotal={jointTotal}
            />
          )}
        </div>
      </div>
      <ErrorText error={accept.error ?? link.error ?? ignore.error} />
      {adding ? (
        <form
          className="form strip-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid)
              accept.mutate(undefined, {
                onSuccess: () =>
                  done(`${name.trim()} is in the live budget now, with its past payments.`),
              })
          }}
        >
          <div className="field-row">
            <Field label="Name">
              {(id) => (
                <input
                  id={id}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                  required
                />
              )}
            </Field>
            <Field label="Paid to" hint="Optional, e.g. the company.">
              {(id, hintId) => (
                <input
                  id={id}
                  value={provider}
                  aria-describedby={hintId}
                  onChange={(e) => setProvider(e.target.value)}
                />
              )}
            </Field>
          </div>
          <div className="field-row">
            <Field label={`Amount, ${CADENCE[item.cadence ?? 'monthly']}`}>
              {(id) => <MoneyInput id={id} value={amount} onChange={setAmount} />}
            </Field>
            {item.cadence !== 'weekly' && (
              <Field label="Day of month" hint="1 to 31, or blank if it varies.">
                {(id, hintId) => (
                  <input
                    id={id}
                    inputMode="numeric"
                    value={day}
                    aria-invalid={!dayOk}
                    aria-describedby={hintId}
                    onChange={(e) => setDay(e.target.value.replace(/\D/g, '').slice(0, 2))}
                  />
                )}
              </Field>
            )}
            <Field label="Category">
              {(id) => (
                <>
                  <input
                    id={id}
                    list={`${id}-list`}
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                  />
                  <datalist id={`${id}-list`}>
                    {(categories.data ?? []).map((c) => (
                      <option key={c} value={c} />
                    ))}
                  </datalist>
                </>
              )}
            </Field>
          </div>
          <Consequence
            prefix="Adding it:"
            account={account}
            pence={parsePounds(amount) ?? 0}
            monthly={monthly}
            jointTotal={jointTotal}
          />
          <div className="row-actions">
            <button className="button button-primary" type="submit" disabled={!valid || busy}>
              {accept.isPending ? 'Adding…' : 'Add to the live budget'}
            </button>
            <button
              type="button"
              className="button button-quiet"
              onClick={() => {
                setAdding(false)
                window.setTimeout(() => opener.current?.focus(), 0)
              }}
            >
              Back
            </button>
          </div>
        </form>
      ) : linking ? (
        <form
          className="form strip-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (lineId)
              link.mutate(Number(lineId), {
                onSuccess: () =>
                  done(`${item.merchant} is matched to that bill now, past payments and future.`),
              })
          }}
        >
          <Field label={`Which bill is ${item.merchant}?`} hint={<NotListed />}>
            {(id, hintId) => (
              <BillLineSelect
                id={id}
                account={item.account}
                value={lineId}
                onChange={setLineId}
                none="Choose a bill"
                describedBy={hintId}
              />
            )}
          </Field>
          <div className="row-actions">
            <button className="button button-primary" type="submit" disabled={!lineId || busy}>
              {link.isPending ? 'Linking…' : 'Match it to this bill'}
            </button>
            <button
              type="button"
              className="button button-quiet"
              onClick={() => {
                setLinking(false)
                window.setTimeout(() => opener.current?.focus(), 0)
              }}
            >
              Back
            </button>
          </div>
        </form>
      ) : (
        <div className="row-actions">
          {item.likely_line_id && item.likely_label && (
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() =>
                link.mutate(item.likely_line_id!, {
                  onSuccess: () =>
                    done(`${item.merchant} is ${item.likely_label} now, past payments and future.`),
                })
              }
            >
              It’s {item.likely_label}
            </button>
          )}
          <button
            ref={opener}
            type="button"
            className={item.likely_line_id ? 'button' : 'button button-primary'}
            disabled={busy}
            onClick={() => setAdding(true)}
          >
            Add to the budget…<span className="visually-hidden">: {item.merchant}</span>
          </button>
          <button type="button" className="button" disabled={busy} onClick={() => setLinking(true)}>
            {item.likely_line_id ? 'Another bill…' : 'It’s in the budget already…'}
            <span className="visually-hidden">: {item.merchant}</span>
          </button>
          <button
            type="button"
            className="button button-quiet"
            disabled={busy}
            onClick={() =>
              ignore.mutate(undefined, {
                onSuccess: () => done(`${item.merchant} set aside: it won’t be suggested again.`),
              })
            }
          >
            Not a bill<span className="visually-hidden">: {item.merchant}</span>
          </button>
          <DismissButton
            item={item}
            label={item.merchant ?? ''}
            disabled={busy}
            onAnswered={onAnswered}
          />
        </div>
      )}
    </li>
  )
}

// ---- payments to check: missed, or still paid after a "cancel" -----------------------------

function formatMonthName(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', { month: 'long' }).format(parseDate(iso))
}

/** It was paid, under a name or amount the matcher didn't take for the bill: pick the
 *  payment from what went out around then. */
function PaymentPicker({
  item,
  onPicked,
  onBack,
}: {
  item: InboxItem
  onPicked: (text: string) => void
  onBack: () => void
}) {
  const options = useQuery({
    queryKey: ['inbox', item.id, 'payments'],
    queryFn: () => api.get<BankTransaction[]>(`/api/inbox/${item.id}/payments`),
  })
  const [chosen, setChosen] = useState<number | null>(null)
  const [always, setAlways] = useState(true)
  const paid = useBudgetMutation(() =>
    api.post(`/api/inbox/${item.id}/paid`, { transaction_id: chosen, always }),
  )
  const legend = useRef<HTMLLegendElement>(null)
  useEffect(() => legend.current?.focus(), [])
  const pick = options.data?.find((t) => t.id === chosen)
  const month = item.month ? formatMonthName(item.month) : 'that month'
  return (
    <form
      className="form strip-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (pick)
          paid.mutate(undefined, {
            onSuccess: () =>
              onPicked(
                `${item.label}: matched to ${pick.merchant} on ${formatDay(pick.booked_date)}.` +
                  (always ? ` ${pick.merchant}’s next payments will match it too.` : ''),
              ),
          })
      }}
    >
      <fieldset className="decision">
        <legend ref={legend} tabIndex={-1}>
          Which payment was {item.label}? Money out around {month}, nearest the usual amount first.
        </legend>
        {options.isPending ? (
          <Loading what="payments" />
        ) : options.isError ? (
          <ErrorText error={options.error} />
        ) : options.data.length === 0 ? (
          <p className="field-hint">
            Nothing went out of this account around {month}. If the bank hasn’t sent it yet, snooze
            this for a week.
          </p>
        ) : (
          options.data.map((t) => (
            <label key={t.id} className="choice">
              <input
                type="radio"
                name={`paid-${item.id}`}
                checked={chosen === t.id}
                onChange={() => setChosen(t.id)}
              />
              <span>
                <Money pence={-t.amount_pence} /> on {formatDay(t.booked_date)} · {t.merchant}
                {t.description && t.description !== t.merchant && (
                  <span className="follow-note"> {t.description}</span>
                )}
                {t.line_name && <span className="follow-note"> Matched to {t.line_name} now.</span>}
              </span>
            </label>
          ))
        )}
      </fieldset>
      {pick && (
        <label className="choice">
          <input type="checkbox" checked={always} onChange={(e) => setAlways(e.target.checked)} />
          <span>
            Match <strong>{pick.merchant}</strong> to {item.label} from now on
          </span>
        </label>
      )}
      <ErrorText error={paid.error} />
      <div className="row-actions">
        <button className="button button-primary" type="submit" disabled={!pick || paid.isPending}>
          {paid.isPending ? 'Matching…' : 'That’s the payment'}
        </button>
        <button type="button" className="button button-quiet" onClick={onBack}>
          Back
        </button>
      </div>
    </form>
  )
}

function Missed({
  item,
  account,
  jointTotal,
  onAnswered,
}: {
  item: InboxItem
  account: Account | undefined
  jointTotal: number | undefined
  onAnswered: (a: Answer) => void
}) {
  const [confirm, setConfirm] = useState(false)
  const [finding, setFinding] = useState(false)
  const question = useRef<HTMLParagraphElement>(null)
  const opener = useRef<HTMLButtonElement>(null)
  const finder = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (confirm) question.current?.focus()
  }, [confirm])
  const stopped = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/stopped`))
  const snooze = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/snooze`, {}))
  const busy = stopped.isPending || snooze.isPending
  const ink = inkFor(account)
  const month = item.month ? formatMonthName(item.month) : 'this month'
  const usual = item.last_paid_pence ?? item.amount_pence ?? 0
  const answer = (text: string) =>
    onAnswered({ id: item.id, label: item.label ?? '', text, undoable: true, hide: true })
  return (
    <li className="strip" data-ink={ink}>
      <div className="strip-body">
        {ink && <Rosette ink={ink} size={48} />}
        <div className="strip-what">
          <span className="strip-account">{shortName(account?.name ?? item.account ?? '')}</span>
          <h3 className="strip-title">
            {item.label}: <span className="strip-state">not paid in {month}</span>
          </h3>
          <p className="strip-meta">
            Usually <Money pence={usual} /> around {formatDay(item.due_on ?? '')}; last paid{' '}
            {formatDay(item.last_paid_on ?? '')}. Five days on, the bank shows nothing.
          </p>
        </div>
      </div>
      <ErrorText error={stopped.error ?? snooze.error} />
      {finding ? (
        <PaymentPicker
          item={item}
          onPicked={answer}
          onBack={() => {
            setFinding(false)
            window.setTimeout(() => finder.current?.focus(), 0)
          }}
        />
      ) : confirm ? (
        <div className="confirm" role="group" aria-label={`Confirm ${item.label} has stopped`}>
          <p ref={question} tabIndex={-1}>
            Take <strong>{item.label}</strong> (<Money pence={usual} /> a month) out of the live
            budget from {month}? Any what-if that hasn’t changed it stops too. Its history stays,
            and you can undo this.
          </p>
          <Consequence
            prefix="Then:"
            account={account}
            pence={-(item.amount_pence ?? usual)}
            monthly
            jointTotal={jointTotal}
          />
          <div className="row-actions">
            <button
              type="button"
              className="button button-danger"
              disabled={busy}
              onClick={() =>
                stopped.mutate(undefined, {
                  onSuccess: () => answer(`${item.label} is out of the live budget from ${month}.`),
                })
              }
            >
              Yes, it’s stopped
            </button>
            <button
              type="button"
              className="button button-quiet"
              onClick={() => {
                setConfirm(false)
                window.setTimeout(() => opener.current?.focus(), 0)
              }}
            >
              Back
            </button>
          </div>
        </div>
      ) : (
        <div className="row-actions">
          <button
            ref={finder}
            type="button"
            className="button button-primary"
            disabled={busy}
            onClick={() => setFinding(true)}
          >
            It was paid: find the payment…
            <span className="visually-hidden">: {item.label}</span>
          </button>
          <button
            ref={opener}
            type="button"
            className="button"
            disabled={busy}
            onClick={() => setConfirm(true)}
          >
            It’s stopped<span className="visually-hidden">: {item.label}</span>
          </button>
          <button
            type="button"
            className="button button-quiet"
            disabled={busy}
            onClick={() =>
              snooze.mutate(undefined, {
                onSuccess: () => answer(`${item.label} put off for a week.`),
              })
            }
          >
            Snooze a week<span className="visually-hidden">: {item.label}</span>
          </button>
          <DismissButton
            item={item}
            label={item.label ?? ''}
            disabled={busy}
            onAnswered={onAnswered}
          />
        </div>
      )}
    </li>
  )
}

function StillPaying({
  item,
  account,
  onAnswered,
}: {
  item: InboxItem
  account: Account | undefined
  onAnswered: (a: Answer) => void
}) {
  const cancelled = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/cancelled`))
  const keep = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/keep`))
  const busy = cancelled.isPending || keep.isPending
  const ink = inkFor(account)
  const answer = (text: string) =>
    onAnswered({ id: item.id, label: item.label ?? '', text, undoable: true, hide: true })
  return (
    <li className="strip" data-ink={ink}>
      <div className="strip-body">
        {ink && <Rosette ink={ink} size={48} />}
        <div className="strip-what">
          <span className="strip-account">{shortName(account?.name ?? item.account ?? '')}</span>
          <h3 className="strip-title">
            {item.label}: <span className="strip-state">still being paid</span>
          </h3>
          <p className="strip-meta">
            It’s marked to cancel, but <Money pence={item.taken_pence ?? item.amount_pence ?? 0} />{' '}
            has been taken in the last six weeks, most recently{' '}
            <Money pence={item.amount_pence ?? 0} /> on {formatDay(item.paid_on ?? '')}.
          </p>
        </div>
      </div>
      <ErrorText error={cancelled.error ?? keep.error} />
      <div className="row-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={busy}
          onClick={() =>
            cancelled.mutate(undefined, {
              onSuccess: () =>
                answer(`Noted. If ${item.label} is taken again, it’ll be back here.`),
            })
          }
        >
          I’ve cancelled it<span className="visually-hidden">: {item.label}</span>
        </button>
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() =>
            keep.mutate(undefined, {
              onSuccess: () => answer(`${item.label} stays; it’s no longer marked to cancel.`),
            })
          }
        >
          Keep paying it<span className="visually-hidden">: {item.label}</span>
        </button>
        <DismissButton
          item={item}
          label={item.label ?? ''}
          disabled={busy}
          onAnswered={onAnswered}
        />
      </div>
    </li>
  )
}

// ---- bank consents ending ---------------------------------------------------------------------

function ConsentStrip({ item, onAnswered }: { item: InboxItem; onAnswered: (a: Answer) => void }) {
  const snooze = useBudgetMutation(() => api.post(`/api/inbox/${item.id}/snooze`, {}))
  const ends = item.expires_on
  const days = ends
    ? Math.round((parseDate(ends).getTime() - today().getTime()) / 86_400_000)
    : null
  const ended = Boolean(item.expired) || (days !== null && days < 0)
  const what = item.accounts?.length ? item.accounts.join(' and ') : item.bank
  return (
    <li className="strip consent-strip">
      <div className="strip-what">
        <span className="strip-account">{item.bank}</span>
        <h3 className="strip-title">
          {ended
            ? `${what}: your bank link has ended`
            : `${what}: your bank link ends ${ends ? formatDay(ends) : 'soon'}`}
        </h3>
        <p className="strip-meta">
          {ended
            ? 'Syncing has stopped until you re-link it. CSV import still works meanwhile.'
            : `In ${days} ${days === 1 ? 'day' : 'days'} the bank stops sharing until you re-link it. It takes a minute at the bank.`}
        </p>
      </div>
      <ErrorText error={snooze.error} />
      <div className="row-actions">
        <Link className="button button-primary" to={`/banks?relink=${item.connection_id}`}>
          Re-link {item.bank}
        </Link>
        {!ended && days !== null && days > 7 && (
          <button
            type="button"
            className="button button-quiet"
            disabled={snooze.isPending}
            onClick={() =>
              snooze.mutate(undefined, {
                onSuccess: () =>
                  onAnswered({
                    id: item.id,
                    label: item.bank ?? '',
                    text: `${item.bank} reminder put off for a while. It comes back before the link ends.`,
                    undoable: true,
                    hide: true,
                  }),
              })
            }
          >
            Remind me later<span className="visually-hidden">: {item.bank}</span>
          </button>
        )}
        <DismissButton
          item={item}
          label={item.bank ?? ''}
          disabled={snooze.isPending}
          onAnswered={onAnswered}
        />
      </div>
    </li>
  )
}

// ---- chores: notes and reminders, not counted ----------------------------------------------

function ReminderRow({ reminder }: { reminder: Reminder }) {
  if (reminder.kind === 'promotion_due') {
    return (
      <li>
        <div>
          <span className="follow-what">
            <strong>{reminder.name}</strong> was planned to start on{' '}
            {formatLongDate(reminder.planned_start)}
          </span>
          <span className="follow-note">Promote it to live when that stage arrives.</span>
        </div>
        <Link className="button" to="/versions">
          Review<span className="visually-hidden"> {reminder.name}</span>
        </Link>
      </li>
    )
  }
  if (reminder.kind === 'redundant_override') {
    return (
      <li>
        <div>
          <span className="follow-what">
            <strong>{reminder.name}</strong>’s change to {reminder.line_name} now matches live
          </span>
          <span className="follow-note">It does nothing any more; revert it to tidy up.</span>
        </div>
        <Link className="button" to={`/budget?v=${reminder.version_id}`}>
          Open {reminder.name}
        </Link>
      </li>
    )
  }
  return (
    <li>
      <div>
        <span className="follow-what">
          {reminder.label} last checked {formatShortMonthYear(reminder.as_of)}
        </span>
        <span className="follow-note">Take-home pay may have moved since.</span>
      </div>
      <Link className="button" to="/settings#take-home">
        Review<span className="visually-hidden"> {reminder.label}</span>
      </Link>
    </li>
  )
}

function Chores({
  followUps,
  reminders,
}: {
  followUps: (Omit<FollowUp, 'status'> & { watching_since?: string | null })[]
  reminders: Reminder[]
}) {
  const resolve = useBudgetMutation(({ id, status }: { id: number; status: string }) =>
    api.patch(`/api/follow-ups/${id}`, { status }),
  )
  // The last follow-up put away, so it can be brought back.
  const [putAway, setPutAway] = useState<{ id: number; name: string; status: string } | null>(null)
  if (followUps.length === 0 && reminders.length === 0 && !putAway) return null

  // A note most of them share is said once, not on every row.
  const counts = new Map<string, number>()
  for (const f of followUps) if (f.note) counts.set(f.note, (counts.get(f.note) ?? 0) + 1)
  const shared = [...counts.entries()].find(([, n]) => n > 1)?.[0]

  return (
    <section className="register" aria-labelledby="chores-title">
      <SectionHeading
        id="chores-title"
        title="Chores"
        aside={<span>Worth doing, not counted in the inbox</span>}
      />
      {shared && <p className="section-lead">{shared}</p>}
      <ErrorText error={resolve.error} />
      {putAway && (
        <Notice
          tone="ok"
          action={
            <button
              type="button"
              className="button"
              onClick={() =>
                resolve.mutate(
                  { id: putAway.id, status: 'open' },
                  { onSuccess: () => setPutAway(null) },
                )
              }
            >
              Undo
            </button>
          }
        >
          {putAway.name} marked {putAway.status === 'done' ? 'sorted' : 'not needed'}.
        </Notice>
      )}
      <ul className="follow-list">
        {followUps.map((f) => (
          <li key={f.id}>
            <div>
              <span className="follow-what">
                {REASONS[f.reason]}: <strong>{f.line_name ?? 'a line'}</strong>
              </span>
              {f.note && f.note !== shared && <span className="follow-note">{f.note}</span>}
              {f.watching_since && (
                <span className="follow-note">
                  You cancelled it on {formatDay(f.watching_since)}; watching for another payment.
                </span>
              )}
            </div>
            <div className="row-actions">
              {(['done', 'dismissed'] as const).map((status) => (
                <button
                  key={status}
                  type="button"
                  className={status === 'done' ? 'button' : 'button button-quiet'}
                  disabled={resolve.isPending}
                  onClick={() =>
                    resolve.mutate(
                      { id: f.id, status },
                      {
                        onSuccess: () =>
                          setPutAway({ id: f.id, name: f.line_name ?? 'It', status }),
                      },
                    )
                  }
                >
                  {status === 'done' ? 'Sorted' : 'Not needed'}
                  <span className="visually-hidden">: {f.line_name ?? 'this'}</span>
                </button>
              ))}
            </div>
          </li>
        ))}
        {reminders.map((r) => (
          <ReminderRow
            key={`${r.kind}-${'version_id' in r ? r.version_id : ''}-${'line_id' in r ? r.line_id : r.kind === 'stale_input' ? r.label : ''}`}
            reminder={r}
          />
        ))}
      </ul>
    </section>
  )
}

// ---- the page ----------------------------------------------------------------------------

export default function Inbox() {
  const inbox = useInbox()
  const accounts = useAccounts()
  const versions = useVersions()
  const live = versions.data?.find((v) => v.type === 'live')
  const summary = useSummary(live?.id)
  const [answer, setAnswer] = useState<Answer | null>(null)
  // Strips just answered, hidden before the refetch lands (and shown again on undo).
  const [hidden, setHidden] = useState<number[]>([])
  const answered = (a: Answer) => {
    setAnswer(a)
    if (a.hide) setHidden((h) => [...h, a.id])
  }
  const notice = useRef<HTMLDivElement>(null)
  // A strip that disappears takes focus with it; the answer's notice takes it instead.
  useEffect(() => {
    if (answer) notice.current?.focus()
  }, [answer])

  if (inbox.isPending) return <Loading what="the inbox" />
  if (inbox.isError) return <ErrorText error={inbox.error} />
  const { items, follow_ups: followUps, reminders } = inbox.data
  const prices = items.filter((i) => i.kind === 'price_change' && !hidden.includes(i.id))
  const consents = items.filter((i) => i.kind === 'consent_expiring' && !hidden.includes(i.id))
  const regulars = items.filter((i) => i.kind === 'new_recurring' && !hidden.includes(i.id))
  const checks = items.filter(
    (i) => (i.kind === 'missed_payment' || i.kind === 'still_paying') && !hidden.includes(i.id),
  )
  const ahead = items.filter(
    (i) => (PLAN_KINDS as readonly string[]).includes(i.kind) && !hidden.includes(i.id),
  )
  const account = (key: string | undefined) => (accounts.data ?? []).find((a) => a.key === key)
  const hub = (person: string | undefined) =>
    (accounts.data ?? []).find((a) => a.role === 'salary_hub' && a.owner === person)

  return (
    <>
      <PageHeading
        title="Inbox"
        lead="What the bank has noticed. Nothing in the budget changes until one of you says yes."
      />

      <div ref={notice} tabIndex={-1} className="answer">
        {answer && (
          <Notice
            tone="ok"
            urgent
            action={
              answer.undoable ? (
                <UndoButton
                  id={answer.id}
                  label={answer.label}
                  onUndone={() => {
                    setHidden((h) => h.filter((id) => id !== answer.id))
                    setAnswer(null)
                  }}
                />
              ) : undefined
            }
          >
            {answer.text}
          </Notice>
        )}
      </div>

      {consents.length > 0 && (
        <section className="register" aria-labelledby="consents-title">
          <SectionHeading
            id="consents-title"
            title="Bank links"
            aside={<span>Your links ending within 14 days</span>}
          />
          <ul className="strip-list">
            {consents.map((item) => (
              <ConsentStrip key={item.id} item={item} onAnswered={answered} />
            ))}
          </ul>
        </section>
      )}

      {prices.length + consents.length + regulars.length + checks.length + ahead.length === 0 ? (
        <div className="inbox-clear">
          <p className="inbox-clear-title">Inbox clear. Braw.</p>
          <p>
            Price changes, new regular payments, missed bills and anything still paid after a cancel
            land here when the bank shows them. <Link to="/banks">Bank links</Link>
          </p>
        </div>
      ) : prices.length > 0 ? (
        <section className="register" aria-labelledby="prices-title">
          <SectionHeading
            id="prices-title"
            title="Price changes"
            aside={<span>Seen in the bank, not yet in the budget</span>}
          />
          <ul className="strip-list">
            {prices.map((item) => (
              <PriceChange
                key={item.id}
                item={item}
                account={account(item.account)}
                jointTotal={summary.data?.joint_total_pence}
                onAnswered={answered}
              />
            ))}
          </ul>
        </section>
      ) : null}

      {checks.length > 0 && (
        <section className="register" aria-labelledby="checks-title">
          <SectionHeading
            id="checks-title"
            title="Payments to check"
            aside={<span>Missed, or still taken after a cancel</span>}
          />
          <ul className="strip-list">
            {checks.map((item) =>
              item.kind === 'missed_payment' ? (
                <Missed
                  key={item.id}
                  item={item}
                  account={account(item.account)}
                  jointTotal={summary.data?.joint_total_pence}
                  onAnswered={answered}
                />
              ) : (
                <StillPaying
                  key={item.id}
                  item={item}
                  account={account(item.account)}
                  onAnswered={answered}
                />
              ),
            )}
          </ul>
        </section>
      )}

      {regulars.length > 0 && (
        <section className="register" aria-labelledby="regulars-title">
          <SectionHeading
            id="regulars-title"
            title="New regular payments"
            aside={<Link to="/regular">Every regular payment</Link>}
          />
          <ul className="strip-list">
            {regulars.map((item) => (
              <NewRegular
                key={item.id}
                item={item}
                account={account(item.account)}
                jointTotal={summary.data?.joint_total_pence}
                onAnswered={answered}
              />
            ))}
          </ul>
        </section>
      )}

      {ahead.length > 0 && (
        <section className="register" aria-labelledby="ahead-title">
          <SectionHeading
            id="ahead-title"
            title="Looking ahead"
            aside={<Link to="/plan/forecast">The forecast</Link>}
          />
          <ul className="strip-list">
            {ahead.map((item) => (
              <PlanStrip
                key={item.id}
                item={item}
                account={item.kind === 'salary_mismatch' ? hub(item.person) : account(item.account)}
                onAnswered={answered}
              />
            ))}
          </ul>
        </section>
      )}

      <Chores followUps={followUps} reminders={reminders} />
    </>
  )
}
