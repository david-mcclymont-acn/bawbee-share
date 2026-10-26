import * as Tabs from '@radix-ui/react-tabs'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { inkFor, shortName } from '../accounts.ts'
import {
  api,
  useAccounts,
  useActuals,
  useBudgetMutation,
  useCategories,
  useDiff,
  useLines,
  useSummary,
  useVersions,
  type Account,
  type Actual,
  type Diff,
  type Line,
  type LineValues,
  type Summary,
  type Version,
} from '../api.ts'
import { formatDay, formatLongDate, formatMonth, isoDate, today } from '../dates.ts'
import { Rosette } from '../marks.tsx'
import { formatPence, parsePounds, penceToInput } from '../money.ts'
import { ErrorText, Field, Loading, Money, MoneyInput, Notice, PageHeading, Sheet } from '../ui.tsx'

const BASIS: Record<string, string> = {
  gross: 'gross pay',
  take_home: 'take-home pay',
  surplus: 'what’s left over',
  fixed_pct: 'a fixed split',
}

type Column = { key: string; title: string; accounts: Account[] }

function columnsFor(accounts: Account[]): Column[] {
  const of = (owner: string, roles: string[]) =>
    accounts.filter((a) => a.owner === owner && roles.includes(a.role))
  return [
    {
      key: 'person_1',
      title: 'Person 1',
      accounts: of('person_1', ['salary_hub', 'subscriptions']),
    },
    { key: 'person_2', title: 'Person 2', accounts: of('person_2', ['salary_hub']) },
    { key: 'joint', title: 'Joint', accounts: accounts.filter((a) => a.role === 'joint_bills') },
  ].filter((c) => c.accounts.length > 0)
}

function label(v: Pick<LineValues, 'name' | 'provider'>): string {
  return v.provider ? `${v.name} (${v.provider})` : v.name
}

/** How a what-if's change to a line reads: "Changed, was £300.00", "was fixed at £2,670.00". */
function changeNote(diff: Diff | undefined, lineId: number): string {
  const change = diff?.changes.find((c) => c.line_id === lineId && c.change === 'modified')
  const amount = change?.fields.amount_pence
  if (amount && typeof amount[0] === 'number') return `Changed, was ${formatPence(amount[0])}`
  const fixed = change?.fields.actual_override_pence
  if (fixed) {
    return typeof fixed[0] === 'number'
      ? `Changed, was fixed at ${formatPence(fixed[0])}`
      : 'Changed, was calculated'
  }
  const renamed = change?.fields.name
  if (renamed && typeof renamed[0] === 'string') return `Changed, was “${renamed[0]}”`
  return 'Changed here'
}

/** This month so far, from the bank. As planned stays quiet; only a difference is marked:
 * "Paid £108.50 on Sun 1 Nov, £3.75 more than planned". */
function ActualNote({ actual }: { actual: Actual | undefined }) {
  if (!actual || actual.status === 'no_data') return null
  const gap = Math.abs(actual.actual_pence - actual.planned_pence)
  if (actual.kind === 'allocation') {
    return (
      <span className="ledger-actual" data-status={actual.status}>
        Spent <Money pence={actual.actual_pence} /> of <Money pence={actual.planned_pence} />
        {actual.status === 'over' && (
          <>
            , <Money pence={gap} /> over
          </>
        )}
      </span>
    )
  }
  if (actual.status === 'not_seen') {
    return (
      <span className="ledger-actual" data-status="not_seen">
        Not seen yet{actual.due_on && `, due ${formatDay(actual.due_on)}`}
      </span>
    )
  }
  if (actual.status === 'due') {
    return (
      <span className="ledger-actual" data-status="due">
        Due {formatDay(actual.due_on!)}
      </span>
    )
  }
  const on = actual.paid_on.map(formatDay).join(' and ')
  return (
    <span className="ledger-actual" data-status={actual.status}>
      Paid <Money pence={actual.actual_pence} /> on {on}
      {actual.status === 'over' && (
        <>
          , <Money pence={gap} /> more than planned
        </>
      )}
      {actual.status === 'partly' && (
        <>
          , <Money pence={gap} /> still to come
        </>
      )}
    </span>
  )
}

// ---- the line editor ----------------------------------------------------------------------

type Editing = { line?: Line; account: string }

function LineEditor({
  version,
  editing,
  accounts,
  onClose,
}: {
  version: Version
  editing: Editing
  accounts: Account[]
  onClose: () => void
}) {
  const categories = useCategories()
  const existing = editing.line?.values
  const calculated = Boolean(existing?.transfer_rule)
  const [name, setName] = useState(existing?.name ?? '')
  const [provider, setProvider] = useState(existing?.provider ?? '')
  const [kind, setKind] = useState<LineValues['kind']>(existing?.kind ?? 'bill')
  const [amount, setAmount] = useState(penceToInput(existing?.amount_pence))
  const [fixed, setFixed] = useState(penceToInput(existing?.actual_override_pence))
  const [frequency, setFrequency] = useState<LineValues['frequency']>(
    existing?.frequency ?? 'monthly',
  )
  const [day, setDay] = useState(existing?.day_of_month?.toString() ?? '')
  const [account, setAccount] = useState(existing?.account ?? editing.account)
  const [toAccount, setToAccount] = useState(existing?.to_account ?? '')
  const [category, setCategory] = useState(existing?.category ?? '')
  const [from, setFrom] = useState(isoDate(today()))
  const live = version.type === 'live'

  const save = useBudgetMutation(async () => {
    const values: Partial<LineValues> = {}
    const set = <K extends keyof LineValues>(k: K, v: LineValues[K]) => {
      if (!existing || existing[k] !== v) values[k] = v
    }
    set('name', name.trim())
    set('provider', provider.trim() || null)
    set('kind', kind)
    set('account', account)
    set('to_account', kind === 'transfer' ? toAccount || null : null)
    set('category', category.trim() || null)
    set('frequency', frequency)
    set('day_of_month', day ? Number(day) : null)
    if (calculated) set('actual_override_pence', fixed ? parsePounds(fixed) : null)
    else set('amount_pence', amount ? parsePounds(amount) : 0)
    const effective = live ? { effective_from: from } : {}
    if (existing && editing.line) {
      if (Object.keys(values).length === 0) return
      await api.patch(`/api/versions/${version.id}/lines/${editing.line.line_id}`, {
        changes: values,
        ...effective,
      })
    } else {
      await api.post(`/api/versions/${version.id}/lines`, { values, ...effective })
    }
  })
  const remove = useBudgetMutation(() =>
    api.del(
      `/api/versions/${version.id}/lines/${editing.line!.line_id}${live ? `?effective_from=${from}` : ''}`,
    ),
  )

  const amountOk = calculated
    ? !fixed || parsePounds(fixed) !== null
    : parsePounds(amount || '0') !== null
  const dayOk = !day || (Number(day) >= 1 && Number(day) <= 31)
  const valid = name.trim() && amountOk && dayOk && (kind !== 'transfer' || toAccount)
  const done = () => onClose()
  // Stopping a live line is the one edit here with no "revert", so it asks first.
  const [confirmStop, setConfirmStop] = useState(false)

  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault()
        if (valid) save.mutate(undefined, { onSuccess: done })
      }}
    >
      <Field label="Name">
        {(id) => <input id={id} value={name} onChange={(e) => setName(e.target.value)} required />}
      </Field>
      <Field
        label="Paid to"
        hint="Who takes the money, e.g. Octopus. Helps match bank payments later."
      >
        {(id) => <input id={id} value={provider} onChange={(e) => setProvider(e.target.value)} />}
      </Field>
      {!calculated && (
        <Field
          label="Type"
          hint={
            existing
              ? 'Only bills are matched to bank payments and watched for missed ones.'
              : undefined
          }
        >
          {(id, hintId) => (
            <select
              id={id}
              value={kind}
              aria-describedby={hintId}
              onChange={(e) => setKind(e.target.value as LineValues['kind'])}
            >
              <option value="bill">Bill: a payment to someone else</option>
              <option value="allocation">Allocation: money set aside</option>
              <option value="transfer">Transfer: between our accounts</option>
            </select>
          )}
        </Field>
      )}
      {calculated ? (
        <Field
          label="Always send"
          hint="Leave blank to send the calculated amount. Set it to send a fixed amount instead."
        >
          {(id) => <MoneyInput id={id} value={fixed} onChange={setFixed} />}
        </Field>
      ) : (
        <div className="field-row">
          <Field label="Amount">
            {(id) => <MoneyInput id={id} value={amount} onChange={setAmount} />}
          </Field>
          <Field label="Every">
            {(id) => (
              <select
                id={id}
                value={frequency}
                onChange={(e) => setFrequency(e.target.value as LineValues['frequency'])}
              >
                <option value="monthly">month</option>
                <option value="annual">year</option>
                <option value="quarterly">quarter</option>
                <option value="weekly">week</option>
              </select>
            )}
          </Field>
        </div>
      )}
      <div className="field-row">
        <Field label="Day of month" hint="Blank if it varies.">
          {(id) => (
            <input
              id={id}
              inputMode="numeric"
              value={day}
              onChange={(e) => setDay(e.target.value.replace(/\D/g, ''))}
              aria-invalid={!dayOk}
            />
          )}
        </Field>
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
      <Field label="Paid from">
        {(id) => (
          <select id={id} value={account} onChange={(e) => setAccount(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.key} value={a.key}>
                {shortName(a.name)}
              </option>
            ))}
          </select>
        )}
      </Field>
      {kind === 'transfer' && !calculated && (
        <Field label="Into">
          {(id) => (
            <select
              id={id}
              value={toAccount}
              onChange={(e) => setToAccount(e.target.value)}
              required
            >
              <option value="">Choose an account</option>
              {accounts
                .filter((a) => a.key !== account)
                .map((a) => (
                  <option key={a.key} value={a.key}>
                    {shortName(a.name)}
                  </option>
                ))}
            </select>
          )}
        </Field>
      )}
      {live && (
        <Field
          label="Takes effect from"
          hint="What-ifs that don’t change this line follow it from this date."
        >
          {(id) => (
            <input
              id={id}
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              required
            />
          )}
        </Field>
      )}
      <ErrorText error={save.error ?? remove.error} />
      <div className="form-actions">
        <button className="button button-primary" type="submit" disabled={!valid || save.isPending}>
          {save.isPending ? 'Saving…' : existing ? 'Save' : 'Add line'}
        </button>
        {existing && (
          <button
            type="button"
            className="button button-danger"
            disabled={remove.isPending || confirmStop}
            onClick={() =>
              live ? setConfirmStop(true) : remove.mutate(undefined, { onSuccess: done })
            }
          >
            {live ? `Stop from ${formatLongDate(from)}…` : 'Remove from this what-if'}
          </button>
        )}
      </div>
      {confirmStop && existing && (
        <div className="confirm" role="group" aria-label="Confirm stopping this line">
          <p>
            Stop <strong>{label(existing)}</strong> from {formatLongDate(from)}? It won’t be counted
            in the live budget after that date, and what-ifs that don’t change it stop too. Its
            history stays in past periods.
          </p>
          <div className="row-actions">
            <button
              type="button"
              className="button button-danger"
              disabled={remove.isPending}
              onClick={() => remove.mutate(undefined, { onSuccess: done })}
            >
              Yes, stop it
            </button>
            <button
              type="button"
              className="button button-quiet"
              onClick={() => setConfirmStop(false)}
            >
              Keep it
            </button>
          </div>
        </div>
      )}
    </form>
  )
}

// ---- a column of the budget --------------------------------------------------------------

function AccountColumn({
  column,
  lines,
  summary,
  diff,
  version,
  allAccounts,
  onEdit,
  onRevert,
  actuals,
  covered,
}: {
  column: Column
  lines: Line[]
  summary: Summary
  diff: Diff | undefined
  version: Version
  allAccounts: Account[]
  onEdit: (e: Editing) => void
  onRevert: (lineId: number) => void
  actuals?: Map<number, Actual>
  covered?: Record<string, { from: string; to: string }>
}) {
  const transfers = new Map(summary.transfers.map((t) => [t.line_id, t]))
  const flows = new Map(summary.accounts.map((a) => [a.key, a]))
  const readOnly = version.type === 'period'
  const name = (key: string) => shortName(allAccounts.find((a) => a.key === key)?.name ?? key)

  return (
    <div className="column">
      {column.accounts.map((account) => {
        const flow = flows.get(account.key)
        const own = lines
          .filter((l) => l.values.account === account.key)
          .sort(
            (a, b) =>
              (a.values.day_of_month ?? 99) - (b.values.day_of_month ?? 99) ||
              a.values.name.localeCompare(b.values.name),
          )
        const incoming = summary.transfers.filter((t) => t.to_account === account.key)
        const removed = (diff?.changes ?? []).filter(
          (c) => c.change === 'removed' && c.account === account.key,
        )
        const personal =
          account.role === 'salary_hub' && account.owner
            ? summary.surpluses.personal[account.owner]
            : undefined
        const takeHome =
          personal !== undefined && flow
            ? personal + flow.outflow_pence - flow.inflow_pence
            : undefined
        const left =
          personal ??
          (account.role === 'joint_bills'
            ? summary.surpluses.joint
            : flow
              ? flow.inflow_pence - flow.outflow_pence
              : 0)
        const ink = inkFor(account)
        const span = covered?.[account.key]
        return (
          <section
            key={account.key}
            className="account-block"
            data-ink={ink}
            aria-label={shortName(account.name)}
          >
            <header className="account-head">
              {ink && <Rosette ink={ink} size={40} />}
              <div>
                <h3>{shortName(account.name)}</h3>
                {account.provider && <span className="account-bank">{account.provider}</span>}
              </div>
              {actuals && span && (
                <span className="account-actuals">
                  Bank export {formatDay(span.from)} to {formatDay(span.to)}
                </span>
              )}
              {actuals && !span && account.role !== 'savings' && account.role !== 'investment' && (
                <span className="account-actuals">No bank export this month</span>
              )}
            </header>

            <ul className="ledger">
              <li className="ledger-head" aria-hidden="true">
                <span className="ledger-day">Day</span>
                <span className="ledger-name">Line</span>
                <span>A month</span>
              </li>
              {takeHome !== undefined && (
                <li className="ledger-in">
                  <span className="ledger-day" />
                  <span className="ledger-name">Take-home pay</span>
                  <Money pence={takeHome} />
                </li>
              )}
              {incoming.map((t) => (
                <li key={`in-${t.line_id}`} className="ledger-in">
                  <span className="ledger-day" />
                  <span className="ledger-name">In from {name(t.from_account)}</span>
                  <Money pence={t.effective_pence} />
                </li>
              ))}
              {own.map((line) => {
                const v = line.values
                const transfer = transfers.get(line.line_id)
                const pence = transfer ? transfer.effective_pence : line.monthly_pence
                return (
                  <li key={line.line_id} data-origin={line.origin}>
                    <span className="ledger-day">{v.day_of_month ?? ''}</span>
                    <span className="ledger-name">
                      {readOnly || line.origin === 'computed' ? (
                        label(v)
                      ) : (
                        <button
                          type="button"
                          className="ledger-link"
                          onClick={() => onEdit({ line, account: v.account })}
                        >
                          {label(v)}
                        </button>
                      )}
                      {v.frequency !== 'monthly' && v.amount_pence !== null && (
                        <span className="ledger-note">
                          {formatPence(v.amount_pence)} a{' '}
                          {v.frequency === 'annual'
                            ? 'year'
                            : v.frequency === 'quarterly'
                              ? 'quarter'
                              : 'week'}
                        </span>
                      )}
                      {transfer && transfer.difference_pence !== 0 && (
                        <span className="ledger-note">
                          suggested {formatPence(transfer.suggested_pence)}
                        </span>
                      )}
                      <ActualNote actual={actuals?.get(line.line_id)} />
                      {line.origin === 'computed' && (
                        <span className="ledger-mark">
                          Calculated from <Link to="/plan/childcare">Childcare</Link>
                        </span>
                      )}
                      {line.origin !== 'base' &&
                        line.origin !== 'computed' &&
                        version.type === 'what_if' && (
                          <span className="ledger-mark">
                            {line.origin === 'added'
                              ? 'Added here'
                              : changeNote(diff, line.line_id)}
                            <button
                              type="button"
                              className="ledger-revert"
                              onClick={() => onRevert(line.line_id)}
                            >
                              {line.origin === 'added' ? 'Remove' : 'Revert'}
                            </button>
                          </span>
                        )}
                    </span>
                    <Money pence={pence} />
                  </li>
                )
              })}
              {removed.map((c) => (
                <li key={`removed-${c.line_id}`} data-origin="removed">
                  <span className="ledger-day" />
                  <span className="ledger-name">
                    <s>{c.label}</s>
                    <span className="ledger-mark">
                      Removed here
                      <button
                        type="button"
                        className="ledger-revert"
                        onClick={() => onRevert(c.line_id)}
                      >
                        Restore
                      </button>
                    </span>
                  </span>
                  <span />
                </li>
              ))}
            </ul>

            <dl className="ledger-totals">
              <div>
                <dt>Going out</dt>
                <dd>
                  <Money pence={flow?.outflow_pence ?? 0} />
                </dd>
              </div>
              <div className="ledger-left">
                <dt>Left over</dt>
                <dd>
                  <Money pence={left} />
                </dd>
              </div>
            </dl>
            {!readOnly && (
              <button
                type="button"
                className="button button-quiet add-line"
                onClick={() => onEdit({ account: account.key })}
              >
                Add a line to {shortName(account.name)}
              </button>
            )}
          </section>
        )
      })}
    </div>
  )
}

// ---- the page ----------------------------------------------------------------------------

export default function Budget() {
  const [params, setParams] = useSearchParams()
  const versions = useVersions()
  const accounts = useAccounts()
  const choices = (versions.data ?? []).filter(
    (v) => v.status === 'active' || v.status === 'draft' || v.type === 'period',
  )
  const live = choices.find((v) => v.type === 'live')
  const selected = choices.find((v) => String(v.id) === params.get('v')) ?? live
  const lines = useLines(selected?.id)
  const summary = useSummary(selected?.id)
  const diff = useDiff(selected?.id, selected?.type === 'what_if')
  const [editing, setEditing] = useState<Editing | null>(null)
  const month = params.get('month') ?? isoDate(today()).slice(0, 7)
  // Actual vs planned is for the live budget, this month (PLAN.md §8.4 step 5).
  const actuals = useActuals(month, selected?.type === 'live')
  const covered = selected?.type === 'live' ? actuals.data?.covered : undefined
  const actualMap =
    covered && Object.keys(covered).length > 0
      ? new Map(actuals.data!.lines.map((a) => [a.line_id, a]))
      : undefined
  const revert = useBudgetMutation((lineId: number) =>
    api.post(`/api/versions/${selected!.id}/lines/${lineId}/revert`),
  )

  if (versions.isPending || accounts.isPending) return <Loading what="the budget" />
  if (!selected)
    return <ErrorText error={new Error('There’s no live budget yet. Import the seed first.')} />
  const columns = columnsFor(accounts.data ?? [])
  const s = summary.data
  const base =
    choices.find((v) => v.id === selected.base_version_id) ??
    (versions.data ?? []).find((v) => v.id === selected.base_version_id)
  const editable = (accounts.data ?? []).filter((a) => a.role !== 'dormant')

  const shares = s
    ? Object.entries(s.shares)
        .map(
          ([p, sh]) =>
            `${p === 'person_1' ? 'Person 1' : p === 'person_2' ? 'Person 2' : p} ${Number(sh.percent).toFixed(1)}%`,
        )
        .join(' · ')
    : ''

  return (
    <>
      <PageHeading
        title={selected.name}
        lead={
          selected.type === 'live'
            ? 'The live budget: what payday and the dashboard use.'
            : selected.type === 'period'
              ? `A frozen period, ${selected.period_start ? formatLongDate(selected.period_start) : ''} to ${selected.period_end ? formatLongDate(selected.period_end) : ''}.`
              : `A what-if built on ${base?.name ?? 'the live budget'}. Only what you change here is stored; everything else follows its base.`
        }
      >
        <label className="version-switch">
          <span>Budget</span>
          <select
            value={selected.id}
            onChange={(e) =>
              setParams(e.target.value === String(live?.id) ? {} : { v: e.target.value })
            }
          >
            {choices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.type === 'live'
                  ? `${v.name} (live)`
                  : v.type === 'period'
                    ? `${v.name} (period)`
                    : v.name}
              </option>
            ))}
          </select>
        </label>
      </PageHeading>

      {selected.type === 'period' && (
        <Notice>Periods are read-only: this is how the budget stood.</Notice>
      )}
      {(diff.data?.conflicts.length ?? 0) > 0 && (
        <Notice tone="warn">
          {diff.data!.conflicts.length} of this what-if’s changes are to lines that have since
          changed on live. You’ll choose which to keep when you promote it.
        </Notice>
      )}
      <ErrorText error={revert.error} />

      {!s || lines.isPending ? (
        <Loading what="lines" />
      ) : (
        <>
          <p className="split-line">
            Joint Billing needs <Money pence={s.joint_total_pence} /> a month, split by{' '}
            {BASIS[s.split_basis] ?? s.split_basis}: {shares}.
            {actualMap &&
              ` Under each line: what the bank shows for ${formatMonth(`${month}-01`)} so far.`}
          </p>
          <div className="budget-desktop">
            {columns.map((c) => (
              <div key={c.key} className="budget-col">
                <h2 className="budget-col-title">{c.title}</h2>
                <AccountColumn
                  column={c}
                  lines={lines.data ?? []}
                  summary={s}
                  diff={diff.data}
                  version={selected}
                  allAccounts={accounts.data ?? []}
                  onEdit={setEditing}
                  onRevert={(id) => revert.mutate(id)}
                  actuals={actualMap}
                  covered={covered}
                />
              </div>
            ))}
          </div>
          <Tabs.Root className="budget-mobile" defaultValue={columns[0]?.key}>
            <Tabs.List className="tabs" aria-label="Accounts">
              {columns.map((c) => (
                <Tabs.Trigger key={c.key} value={c.key} className="tab">
                  {c.title}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            {columns.map((c) => (
              <Tabs.Content key={c.key} value={c.key}>
                <AccountColumn
                  column={c}
                  lines={lines.data ?? []}
                  summary={s}
                  diff={diff.data}
                  version={selected}
                  allAccounts={accounts.data ?? []}
                  onEdit={setEditing}
                  onRevert={(id) => revert.mutate(id)}
                  actuals={actualMap}
                  covered={covered}
                />
              </Tabs.Content>
            ))}
          </Tabs.Root>
        </>
      )}

      <Sheet
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        title={editing?.line ? label(editing.line.values) : 'Add a line'}
        description={
          selected.type === 'live'
            ? 'Changes to the live budget are dated, so what-ifs that don’t touch this line follow it.'
            : `This change is stored in “${selected.name}” only. The live budget doesn’t move.`
        }
      >
        {editing && (
          <LineEditor
            key={editing.line?.line_id ?? `new-${editing.account}`}
            version={selected}
            editing={editing}
            accounts={editable}
            onClose={() => setEditing(null)}
          />
        )}
      </Sheet>
    </>
  )
}
