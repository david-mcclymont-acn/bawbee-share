import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { inkFor, shortName } from '../../accounts.ts'
import { api, useBudgetMutation } from '../../api.ts'
import { formatDay, formatLongDate, formatMonthYear, isoDate, today } from '../../dates.ts'
import { formatPence, parsePounds, penceToInput } from '../../money.ts'
import {
  useForecast,
  useGoals,
  useOneOffs,
  usePaths,
  usePlanVersions,
  type Forecast,
  type ForecastAccount,
  type ForecastPath,
  type ForecastRun,
  type Goal,
  type GoalRun,
  type OneOff,
  type Paths,
  type PlanVersion,
} from '../../plan.ts'
import {
  ErrorText,
  Field,
  Loading,
  Money,
  MoneyInput,
  Notice,
  SectionHeading,
  Sheet,
} from '../../ui.tsx'
import { AccountName } from './common.tsx'
import { fromMonthInput, PATH_DASHES, PATH_STYLE_NAMES, shortMonth, toMonthInput } from './util.ts'

const MAX_PATHS = 4
const HOUSEHOLD = 'household'

function stepsText(path: ForecastPath): string {
  return path.steps
    .map((s, i) =>
      i === 0 ? s.version_name : `${s.version_name} from ${shortMonth(s.from_month)}`,
    )
    .join(' → ')
}

// ---- the chart and its table ------------------------------------------------------------------

function value(run: ForecastRun, i: number, measure: string): number {
  const m = run.months[i]!
  return measure === HOUSEHOLD ? m.household_pence : (m.balances[measure] ?? 0)
}

function ForecastChart({
  runs,
  measure,
  accounts,
}: {
  runs: ForecastRun[]
  measure: string
  accounts: ForecastAccount[]
}) {
  const months = runs[0]?.months ?? []
  const data = months.map((m, i) => ({
    month: m.month,
    ...Object.fromEntries(runs.map((r, ri) => [`p${ri}`, value(r, i, measure) / 100])),
  }))
  const changes = [
    ...new Set(
      runs.flatMap((r) => r.months.filter((m) => m.events.length > 0).map((m) => m.month)),
    ),
  ]
  const account = accounts.find((a) => a.key === measure)
  const ink = account ? inkFor(account) : undefined
  const stroke = ink ? `var(--ink-${ink})` : 'var(--ink)'
  return (
    <div className="chart plan-chart" aria-hidden="true">
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="month"
            tickLine={false}
            axisLine={{ stroke: 'var(--rule-strong)' }}
            tick={{ fill: 'var(--ink-muted)', fontSize: 12 }}
            tickFormatter={(iso: string) => shortMonth(iso)}
            interval="preserveStartEnd"
            minTickGap={28}
          />
          <YAxis
            width={72}
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--ink-muted)', fontSize: 12 }}
            tickFormatter={(v: number) => `£${Math.round(v).toLocaleString('en-GB')}`}
          />
          <ReferenceLine y={0} stroke="var(--danger)" strokeDasharray="3 3" />
          {changes.map((month) => (
            <ReferenceLine key={month} x={month} stroke="var(--rule-strong)" strokeWidth={0.75} />
          ))}
          <Tooltip
            cursor={{ stroke: 'var(--rule-strong)' }}
            contentStyle={{
              background: 'var(--paper)',
              border: '1px solid var(--rule-strong)',
              borderRadius: 0,
              color: 'var(--ink)',
            }}
            formatter={(v, key) => {
              const run = runs[Number(String(key).slice(1))]
              return [formatPence(Math.round(Number(v) * 100)), run?.name ?? '']
            }}
            labelFormatter={(iso) => formatMonthYear(String(iso))}
          />
          {runs.map((r, ri) => (
            <Line
              key={r.path_id}
              type="stepAfter"
              dataKey={`p${ri}`}
              stroke={stroke}
              strokeWidth={ri === 0 ? 1.75 : 1.5}
              strokeDasharray={PATH_DASHES[ri]}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function Summary({
  runs,
  measure,
  label,
}: {
  runs: ForecastRun[]
  measure: string
  label: string
}) {
  return (
    <p className="chart-summary">
      {runs.map((r, ri) => {
        const n = r.months.length
        if (n === 0) return null
        const first = value(r, 0, measure)
        const last = value(r, n - 1, measure)
        return (
          <span key={r.path_id} className="plan-summary-line">
            {runs.length > 1 && (
              <span className="plan-key" data-style={PATH_STYLE_NAMES[ri]} aria-hidden="true" />
            )}
            <strong>{r.name}</strong>: {label} <Money pence={first} /> in{' '}
            {formatMonthYear(r.months[0]!.month)}, <Money pence={last} /> by{' '}
            {formatMonthYear(r.months[n - 1]!.month)}.{' '}
          </span>
        )
      })}
    </p>
  )
}

function MonthTable({ runs, measure }: { runs: ForecastRun[]; measure: string }) {
  const months = runs[0]?.months ?? []
  return (
    <details className="chart-table">
      <summary>Show month by month</summary>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Month</th>
              {runs.map((r) => (
                <th scope="col" className="num" key={r.path_id}>
                  {r.name}
                </th>
              ))}
              <th scope="col">What changes</th>
            </tr>
          </thead>
          <tbody>
            {months.map((m, i) => (
              <tr key={m.month}>
                <th scope="row">{formatMonthYear(m.month)}</th>
                {runs.map((r) => (
                  <td className="num" key={r.path_id}>
                    <Money pence={value(r, i, measure)} />
                  </td>
                ))}
                <td>
                  {[
                    ...new Set(runs.flatMap((r) => [...(r.months[i]?.events ?? [])])),
                    ...m.one_offs.map((o) => `${o.label} ${formatPence(o.pence)}`),
                  ].join('; ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function Changes({ runs }: { runs: ForecastRun[] }) {
  const rows = (runs[0]?.months ?? [])
    .map((m, i) => {
      const events = runs.flatMap((r) =>
        (r.months[i]?.events ?? []).map((e) => ({ event: e, path: r.name })),
      )
      const seen = new Map<string, string[]>()
      for (const { event, path } of events) seen.set(event, [...(seen.get(event) ?? []), path])
      return { month: m.month, events: [...seen.entries()] }
    })
    .filter((r) => r.events.length > 0)
  if (rows.length === 0) return null
  return (
    <section className="register" aria-labelledby="changes-title">
      <SectionHeading id="changes-title" title="What changes when" />
      <ul className="plan-events">
        {rows.map((r) => (
          <li key={r.month}>
            <span className="plan-event-month">{formatMonthYear(r.month)}</span>
            <span className="plan-event-what">
              {r.events.map(([event, paths]) => (
                <span key={event}>
                  {event}
                  {runs.length > 1 && paths.length < runs.length && (
                    <span className="plan-event-path"> · {paths.join(', ')}</span>
                  )}
                </span>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// ---- paths -------------------------------------------------------------------------------------

type StepDraft = { version_id: string; from: string }

function PathSheet({
  path,
  versions,
  live,
  onClose,
}: {
  path: ForecastPath | null
  versions: PlanVersion[]
  live: Paths['live']
  onClose: () => void
}) {
  const start = toMonthInput(isoDate(today()))
  const [name, setName] = useState(path?.name ?? '')
  const [isDefault, setIsDefault] = useState(path?.is_default ?? false)
  const [steps, setSteps] = useState<StepDraft[]>(
    path?.steps.map((s) => ({
      version_id: String(s.version_id),
      from: toMonthInput(s.from_month),
    })) ?? [{ version_id: String(live.version_id), from: start }],
  )
  const save = useBudgetMutation(() => {
    const body = {
      name,
      is_default: isDefault,
      steps: steps.map((s, i) => ({
        version_id: Number(s.version_id),
        from_month: fromMonthInput(i === 0 ? start : s.from),
      })),
    }
    return path ? api.put(`/api/plan/paths/${path.id}`, body) : api.post('/api/plan/paths', body)
  })
  const remove = useBudgetMutation(() => api.del(`/api/plan/paths/${path!.id}`))
  const set = (i: number, change: Partial<StepDraft>) =>
    setSteps(steps.map((s, j) => (j === i ? { ...s, ...change } : s)))
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={path ? `Change ${path.name}` : 'A new path'}
      description="A path is the budget as it is now, then each what-if from the month it starts."
    >
      <form
        className="form"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <Field label="Name">
          {(id) => (
            <input
              id={id}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Back 3 days from April"
              required
              autoFocus
            />
          )}
        </Field>
        <ol className="plan-steps">
          {steps.map((s, i) => (
            <li key={i}>
              <Field label={i === 0 ? 'From now' : 'Then'}>
                {(id) => (
                  <select
                    id={id}
                    value={s.version_id}
                    onChange={(e) => set(i, { version_id: e.target.value })}
                  >
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                        {v.type === 'live' ? ' (live)' : ''}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              {i > 0 && (
                <Field label="From">
                  {(id) => (
                    <input
                      id={id}
                      type="month"
                      min={start}
                      value={s.from}
                      onChange={(e) => set(i, { from: e.target.value })}
                      required
                    />
                  )}
                </Field>
              )}
              {i > 0 && (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => setSteps(steps.filter((_, j) => j !== i))}
                >
                  Remove<span className="visually-hidden"> step {i + 1}</span>
                </button>
              )}
            </li>
          ))}
        </ol>
        {steps.length < 6 && (
          <div>
            <button
              type="button"
              className="button"
              onClick={() => {
                const next = versions.find((v) => v.type === 'what_if')
                const planned = next?.planned_start ? toMonthInput(next.planned_start) : start
                setSteps([
                  ...steps,
                  { version_id: String(next?.id ?? live.version_id), from: planned },
                ])
              }}
            >
              Add a what-if
            </button>
          </div>
        )}
        <label className="choice">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
          />
          <span>Show this one first, here and on the Dashboard</span>
        </label>
        <ErrorText error={save.error ?? remove.error} />
        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={save.isPending}>
            {save.isPending ? 'Saving…' : 'Save the path'}
          </button>
          {path && (
            <button
              type="button"
              className="button button-quiet"
              onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
            >
              Delete it
            </button>
          )}
        </div>
      </form>
    </Sheet>
  )
}

function PathsSection({
  paths,
  shown,
  onShown,
  onEdit,
}: {
  paths: Paths
  shown: number[]
  onShown: (ids: number[]) => void
  onEdit: (path: ForecastPath | null) => void
}) {
  const all: {
    id: number
    name: string
    note: string
    path: ForecastPath | null
    isDefault: boolean
  }[] = [
    {
      id: paths.live.id,
      name: paths.live.name,
      note: 'The live budget, unchanged',
      path: null,
      isDefault: paths.default_id === paths.live.id,
    },
    ...paths.paths.map((p) => ({
      id: p.id,
      name: p.name,
      note: stepsText(p),
      path: p,
      isDefault: p.is_default,
    })),
  ]
  const toggle = (id: number, on: boolean) =>
    onShown(on ? [...shown, id] : shown.filter((x) => x !== id))
  return (
    <section className="register" aria-labelledby="paths-title">
      <SectionHeading
        id="paths-title"
        title="Paths"
        aside={
          <button type="button" className="button" onClick={() => onEdit(null)}>
            New path
          </button>
        }
      />
      <p className="section-lead">Show up to four at once. Each is drawn with its own line.</p>
      <ul className="plan-paths">
        {all.map((p) => {
          const index = shown.indexOf(p.id)
          const on = index >= 0
          return (
            <li key={p.id}>
              <label className="plan-path-show">
                <input
                  type="checkbox"
                  checked={on}
                  disabled={!on && shown.length >= MAX_PATHS}
                  onChange={(e) => toggle(p.id, e.target.checked)}
                />
                <span className="plan-path-what">
                  <span className="plan-path-name">
                    {on && (
                      <span
                        className="plan-key"
                        data-style={PATH_STYLE_NAMES[index]}
                        aria-hidden="true"
                      />
                    )}
                    {p.name}
                    {p.isDefault && <span className="plan-tag">Shown first</span>}
                  </span>
                  <span className="plan-path-note">{p.note}</span>
                </span>
              </label>
              {p.path && (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => onEdit(p.path)}
                >
                  Change<span className="visually-hidden">: {p.name}</span>
                </button>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

// ---- starting balances, one-offs, goals --------------------------------------------------------

function OpeningSheet({ account, onClose }: { account: ForecastAccount; onClose: () => void }) {
  const [text, setText] = useState(
    penceToInput(account.opening_source === 'none' ? null : account.opening_pence),
  )
  const pence = parsePounds(text)
  const save = useBudgetMutation(() =>
    api.put(`/api/plan/opening/${account.key}`, { pence: text.trim() === '' ? null : pence }),
  )
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title={`${shortName(account.name)} balance`}>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <Field label="What it holds today" hint="Leave it empty if you don’t know it.">
          {(id, hintId) => (
            <span aria-describedby={hintId}>
              <MoneyInput id={id} value={text} onChange={setText} autoFocus />
            </span>
          )}
        </Field>
        <ErrorText error={save.error} />
        <div className="form-actions">
          <button
            type="submit"
            className="button button-primary"
            disabled={save.isPending || (text.trim() !== '' && pence === null)}
          >
            Save the balance
          </button>
        </div>
      </form>
    </Sheet>
  )
}

function Opening({ accounts }: { accounts: ForecastAccount[] }) {
  const [editing, setEditing] = useState<ForecastAccount | null>(null)
  return (
    <section className="register" aria-labelledby="opening-title">
      <SectionHeading
        id="opening-title"
        title="Starting balances"
        aside={<span>Where each account begins</span>}
      />
      <ul className="figure-list">
        {accounts.map((a) => (
          <li key={a.key} data-ink={inkFor(a)}>
            <span className="figure-name">
              <AccountName account={a} />
              <span className="plan-opening-source">
                {a.opening_source === 'bank'
                  ? `From the bank${a.opening_as_of ? `, ${formatDay(a.opening_as_of)}` : ''}`
                  : a.opening_source === 'entered'
                    ? 'Typed in'
                    : 'No balance yet: counted as £0'}
              </span>
            </span>
            <span className="row-actions">
              {a.opening_source !== 'none' && <Money pence={a.opening_pence} />}
              {a.opening_source !== 'bank' && (
                <button type="button" className="button button-quiet" onClick={() => setEditing(a)}>
                  {a.opening_source === 'none' ? 'Enter it' : 'Change'}
                  <span className="visually-hidden">: {a.name}</span>
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
      {editing && <OpeningSheet account={editing} onClose={() => setEditing(null)} />}
    </section>
  )
}

function OneOffSheet({
  item,
  accounts,
  paths,
  onClose,
}: {
  item: OneOff | null
  accounts: ForecastAccount[]
  paths: Paths
  onClose: () => void
}) {
  const [label, setLabel] = useState(item?.label ?? '')
  const [account, setAccount] = useState(
    item?.account ?? accounts.find((a) => a.role === 'joint_bills')?.key ?? '',
  )
  const [on, setOn] = useState(item?.on ?? '')
  const [direction, setDirection] = useState<'out' | 'in'>(
    item && item.amount_pence > 0 ? 'in' : 'out',
  )
  const [amount, setAmount] = useState(penceToInput(item ? Math.abs(item.amount_pence) : null))
  const [path, setPath] = useState(String(item?.path_id ?? ''))
  const pence = parsePounds(amount)
  const save = useBudgetMutation(() => {
    const body = {
      label,
      account,
      on,
      amount_pence: direction === 'out' ? -(pence ?? 0) : (pence ?? 0),
      path_id: path ? Number(path) : null,
    }
    return item
      ? api.put(`/api/plan/one-offs/${item.id}`, body)
      : api.post('/api/plan/one-offs', body)
  })
  const remove = useBudgetMutation(() => api.del(`/api/plan/one-offs/${item!.id}`))
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={item ? `Change ${item.label}` : 'A one-off'}
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <Field label="What it is">
          {(id) => (
            <input
              id={id}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              required
              autoFocus
            />
          )}
        </Field>
        <div className="field-row">
          <Field label="Account">
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
          <Field label="When">
            {(id) => (
              <input
                id={id}
                type="date"
                value={on}
                onChange={(e) => setOn(e.target.value)}
                required
              />
            )}
          </Field>
        </div>
        <fieldset className="plan-direction">
          <legend>Money</legend>
          <label className="choice">
            <input
              type="radio"
              checked={direction === 'out'}
              onChange={() => setDirection('out')}
            />
            <span>Going out</span>
          </label>
          <label className="choice">
            <input type="radio" checked={direction === 'in'} onChange={() => setDirection('in')} />
            <span>Coming in</span>
          </label>
        </fieldset>
        <Field label="Amount">
          {(id) => <MoneyInput id={id} value={amount} onChange={setAmount} />}
        </Field>
        <Field label="On which paths">
          {(id) => (
            <select id={id} value={path} onChange={(e) => setPath(e.target.value)}>
              <option value="">Every path</option>
              {paths.paths.map((p) => (
                <option key={p.id} value={p.id}>
                  Only {p.name}
                </option>
              ))}
            </select>
          )}
        </Field>
        <ErrorText error={save.error ?? remove.error} />
        <div className="form-actions">
          <button
            type="submit"
            className="button button-primary"
            disabled={save.isPending || !pence || !label || !on}
          >
            Save the one-off
          </button>
          {item && (
            <button
              type="button"
              className="button button-quiet"
              onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
            >
              Delete it
            </button>
          )}
        </div>
      </form>
    </Sheet>
  )
}

function OneOffs({ accounts, paths }: { accounts: ForecastAccount[]; paths: Paths }) {
  const items = useOneOffs()
  const [open, setOpen] = useState<OneOff | 'new' | null>(null)
  const byKey = new Map(accounts.map((a) => [a.key, a]))
  return (
    <section className="register" aria-labelledby="one-offs-title" id="one-offs">
      <SectionHeading
        id="one-offs-title"
        title="One-offs"
        aside={
          <button type="button" className="button" onClick={() => setOpen('new')}>
            Add a one-off
          </button>
        }
      />
      {items.isPending ? (
        <Loading what="one-offs" />
      ) : (items.data ?? []).length === 0 ? (
        <p className="empty">Nothing dated yet: a holiday, a car’s final payment, a bonus.</p>
      ) : (
        <ul className="figure-list">
          {items.data!.map((o) => (
            <li key={o.id} data-ink={inkFor(byKey.get(o.account))}>
              <span className="figure-name">
                {o.label}
                <span className="plan-opening-source">
                  {formatLongDate(o.on)} · <AccountName account={byKey.get(o.account)} />
                  {o.path_id
                    ? ` · only ${paths.paths.find((p) => p.id === o.path_id)?.name ?? 'one path'}`
                    : ''}
                </span>
              </span>
              <span className="row-actions">
                <Money pence={o.amount_pence} />
                <button type="button" className="button button-quiet" onClick={() => setOpen(o)}>
                  Change<span className="visually-hidden">: {o.label}</span>
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <OneOffSheet
          item={open === 'new' ? null : open}
          accounts={accounts}
          paths={paths}
          onClose={() => setOpen(null)}
        />
      )}
    </section>
  )
}

type GoalDraft = Partial<Goal> & { line_id?: number | null }

function GoalSheet({
  goal,
  accounts,
  onClose,
}: {
  goal: GoalDraft
  accounts: ForecastAccount[]
  onClose: () => void
}) {
  const [name, setName] = useState(goal.name ?? '')
  const [account, setAccount] = useState(goal.account ?? accounts[0]?.key ?? '')
  const [target, setTarget] = useState(penceToInput(goal.target_pence ?? null))
  const [date, setDate] = useState(goal.target_date ?? '')
  const [saved, setSaved] = useState(penceToInput(goal.saved_pence ?? null))
  const [yearly, setYearly] = useState(goal.repeats_yearly ?? false)
  const targetPence = parsePounds(target)
  const savedPence = parsePounds(saved || '0')
  const save = useBudgetMutation(() => {
    const body = {
      name,
      account,
      line_id: goal.line_id ?? null,
      target_pence: targetPence,
      target_date: date,
      saved_pence: savedPence,
      saved_on: isoDate(today()),
      repeats_yearly: yearly,
    }
    return goal.id ? api.put(`/api/plan/goals/${goal.id}`, body) : api.post('/api/plan/goals', body)
  })
  const remove = useBudgetMutation(() => api.del(`/api/plan/goals/${goal.id}`))
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={goal.id ? `Change ${goal.name}` : 'A savings goal'}
      description={
        goal.line_label
          ? `Paid by the budget’s ${goal.line_label} line. If it needs a different amount a month, the inbox asks first.`
          : undefined
      }
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <Field label="Name">
          {(id) => (
            <input
              id={id}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
            />
          )}
        </Field>
        <Field label="Held in">
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
        <div className="field-row">
          <Field label="Target">
            {(id) => <MoneyInput id={id} value={target} onChange={setTarget} />}
          </Field>
          <Field label="Needed by">
            {(id) => (
              <input
                id={id}
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                required
              />
            )}
          </Field>
        </div>
        <Field label="Saved so far" hint="As it stands today.">
          {(id) => <MoneyInput id={id} value={saved} onChange={setSaved} />}
        </Field>
        <label className="choice">
          <input type="checkbox" checked={yearly} onChange={(e) => setYearly(e.target.checked)} />
          <span>Every year: spent on the day, then saved for again (like Christmas)</span>
        </label>
        <ErrorText error={save.error ?? remove.error} />
        <div className="form-actions">
          <button
            type="submit"
            className="button button-primary"
            disabled={save.isPending || !targetPence || savedPence === null || !date}
          >
            Save the goal
          </button>
          {goal.id && (
            <button
              type="button"
              className="button button-quiet"
              onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
            >
              Delete it
            </button>
          )}
        </div>
      </form>
    </Sheet>
  )
}

function GoalsSection({
  run,
  accounts,
}: {
  run: ForecastRun | undefined
  accounts: ForecastAccount[]
}) {
  const goals = useGoals()
  const [open, setOpen] = useState<GoalDraft | null>(null)
  const byKey = new Map(accounts.map((a) => [a.key, a]))
  const projected = new Map<number, GoalRun>(
    (run?.goals ?? []).filter((g) => g.id !== undefined).map((g) => [g.id!, g]),
  )
  return (
    <section className="register" aria-labelledby="goals-title">
      <SectionHeading
        id="goals-title"
        title="Savings goals"
        aside={
          <button type="button" className="button" onClick={() => setOpen({})}>
            Add a goal
          </button>
        }
      />
      {goals.isPending && <Loading what="goals" />}
      {(goals.data?.goals ?? []).length === 0 && !goals.isPending && (
        <p className="empty">
          No goals yet. The budget’s sinking funds are below, ready to set up.
        </p>
      )}
      <ul className="figure-list plan-goals">
        {(goals.data?.goals ?? []).map((g) => {
          const p = projected.get(g.id)
          return (
            <li key={g.id} data-ink={inkFor(byKey.get(g.account))}>
              <span className="figure-name">
                {g.name}
                <span className="plan-opening-source">
                  <Money pence={p?.saved_pence ?? g.saved_pence} /> of{' '}
                  <Money pence={g.target_pence} /> by{' '}
                  {formatLongDate(p?.target_month ?? g.target_date)}
                  {g.repeats_yearly ? ', every year' : ''}
                </span>
                {p && (
                  <span className={p.on_track ? 'ok-text' : 'plan-behind'}>
                    {p.on_track ? (
                      <>
                        On track: <Money pence={p.allocation_pence} /> a month
                      </>
                    ) : (
                      <>
                        Needs <Money pence={p.needed_pence} /> a month; the budget puts aside{' '}
                        <Money pence={p.allocation_pence} />
                      </>
                    )}
                  </span>
                )}
              </span>
              <button type="button" className="button button-quiet" onClick={() => setOpen(g)}>
                Change<span className="visually-hidden">: {g.name}</span>
              </button>
            </li>
          )
        })}
      </ul>
      {(goals.data?.candidates ?? []).length > 0 && (
        <>
          <h3 className="plan-sub">Sinking funds in the budget</h3>
          <ul className="figure-list">
            {goals.data!.candidates.map((c) => (
              <li key={c.line_id} data-ink={inkFor(byKey.get(c.account))}>
                <span className="figure-name">
                  {c.label}
                  <span className="plan-opening-source">
                    <Money pence={c.monthly_pence} /> a month ·{' '}
                    <AccountName account={byKey.get(c.account)} />
                  </span>
                </span>
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() =>
                    setOpen({
                      name: c.label,
                      account: c.account,
                      line_id: c.line_id,
                      line_label: c.label,
                      repeats_yearly: /xmas|christmas|birthday/i.test(c.label),
                    })
                  }
                >
                  Set up as a goal<span className="visually-hidden">: {c.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {open && <GoalSheet goal={open} accounts={accounts} onClose={() => setOpen(null)} />}
    </section>
  )
}

// ---- the tab -------------------------------------------------------------------------------------

function Warnings({ forecast }: { forecast: Forecast }) {
  const all = forecast.runs.flatMap((r) => r.warnings.map((w) => ({ ...w, path: r.name })))
  if (all.length === 0) return null
  return (
    <>
      {all.slice(0, 4).map((w) => (
        <Notice
          key={`${w.path}-${w.code}-${w.subject}`}
          tone={w.code === 'goal_behind' ? 'warn' : 'danger'}
        >
          {forecast.runs.length > 1 && <strong>{w.path}: </strong>}
          {w.message}
        </Notice>
      ))}
    </>
  )
}

export default function ForecastTab() {
  const paths = usePaths()
  const versions = usePlanVersions()
  const [chosen, setChosen] = useState<number[] | null>(null)
  const shown = chosen ?? (paths.data ? [paths.data.default_id] : undefined)
  const forecast = useForecast(shown)
  const [measure, setMeasure] = useState(HOUSEHOLD)
  const [editing, setEditing] = useState<ForecastPath | null | 'new'>(null)
  const runs = useMemo(() => forecast.data?.runs ?? [], [forecast.data])

  if (paths.isPending || versions.isPending || (forecast.isPending && !forecast.data)) {
    return <Loading what="the forecast" />
  }
  if (paths.isError) return <ErrorText error={paths.error} />
  if (forecast.isError) return <ErrorText error={forecast.error} />
  const data = forecast.data!
  const accounts = data.accounts
  const measureName =
    measure === HOUSEHOLD
      ? 'left over'
      : `${shortName(accounts.find((a) => a.key === measure)?.name ?? '')} holds`
  return (
    <>
      <Warnings forecast={data} />
      <section className="register" aria-labelledby="timeline-title">
        <SectionHeading
          id="timeline-title"
          title={`${formatMonthYear(data.start)} onwards`}
          aside={
            <label className="plan-measure">
              <span>Show</span>
              <select value={measure} onChange={(e) => setMeasure(e.target.value)}>
                <option value={HOUSEHOLD}>Household surplus</option>
                {accounts.map((a) => (
                  <option key={a.key} value={a.key}>
                    {shortName(a.name)} balance
                  </option>
                ))}
              </select>
            </label>
          }
        />
        <Summary runs={runs} measure={measure} label={measureName} />
        <ForecastChart runs={runs} measure={measure} accounts={accounts} />
        <p className="plan-chart-note">
          Upright lines mark a month where something changes. This month is on the{' '}
          <Link to="/">Dashboard</Link>, day by day.
        </p>
        <MonthTable runs={runs} measure={measure} />
      </section>
      <PathsSection
        paths={paths.data!}
        shown={shown ?? []}
        onShown={(ids) => setChosen(ids.length > 0 ? ids : [paths.data!.live.id])}
        onEdit={(p) => setEditing(p ?? 'new')}
      />
      <Changes runs={runs} />
      <GoalsSection run={runs[0]} accounts={accounts} />
      <OneOffs accounts={accounts} paths={paths.data!} />
      <Opening accounts={accounts} />
      {editing && (
        <PathSheet
          path={editing === 'new' ? null : editing}
          versions={versions.data ?? []}
          live={paths.data!.live}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  )
}
