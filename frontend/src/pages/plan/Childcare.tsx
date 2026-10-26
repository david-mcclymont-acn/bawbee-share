import { useState, type ChangeEvent, type FormEvent } from 'react'
import { inkFor, shortName } from '../../accounts.ts'
import { api, useAccounts, useBudgetMutation } from '../../api.ts'
import { formatLongDate, formatMonthYear } from '../../dates.ts'
import { formatPence } from '../../money.ts'
import {
  useChildcare,
  usePlanVersions,
  type CareCell,
  type Child,
  type Childcare,
  type Nursery,
  type Pattern,
} from '../../plan.ts'
import { ErrorText, Field, Loading, Money, Notice, SectionHeading, Sheet } from '../../ui.tsx'
import { plural } from './util.ts'

const METRICS = [
  { id: 'left', label: 'Left over' },
  { id: 'net', label: 'Childcare cost' },
  { id: 'gross', label: 'Before Tax-Free Childcare' },
  { id: 'share', label: 'Share of take-home' },
] as const
type Metric = (typeof METRICS)[number]['id']

function patternLabel(p: Pattern) {
  return `${p.days} ${p.days === '1' ? 'day' : 'days'} × ${p.hours}h`
}

function WeekDots({ days }: { days: string }) {
  const n = Number(days)
  return (
    <span className="care-week" aria-hidden="true">
      {[1, 2, 3, 4, 5].map((d) => (
        <span key={d} data-on={d <= n || undefined} data-half={d - 0.5 === n || undefined} />
      ))}
    </span>
  )
}

function CellFigures({ cell, metric }: { cell: CareCell; metric: Metric }) {
  const c = cell.childcare
  if (metric === 'net')
    return (
      <>
        <Money pence={cell.month.childcare.net_pence} className="care-big" />
        <span className="care-sub">
          <Money pence={c.net_pence} /> a year
        </span>
      </>
    )
  if (metric === 'gross')
    return (
      <>
        <Money pence={cell.month.childcare.before_tfc_pence} className="care-big" />
        <span className="care-sub">
          {c.tfc_pence > 0 ? (
            <>
              <Money pence={cell.month.childcare.tfc_pence} /> a month back
            </>
          ) : (
            'No Tax-Free Childcare here'
          )}
        </span>
      </>
    )
  if (metric === 'share')
    return (
      <>
        <span className="care-big money">{cell.share_of_take_home}%</span>
        <span className="care-sub">
          <Money pence={cell.month.childcare.net_pence} /> of{' '}
          <Money pence={cell.month.pay.net_pence} />
        </span>
      </>
    )
  return (
    <>
      <Money pence={cell.monthly_left_pence} className="care-big" />
      <span className="care-sub">
        <Money pence={cell.annual_left_pence} /> a year
      </span>
    </>
  )
}

function Matrix({
  data,
  metric,
  selected,
  onSelect,
}: {
  data: Childcare
  metric: Metric
  selected: CareCell | undefined
  onSelect: (c: CareCell) => void
}) {
  const cell = (n: Nursery, p: Pattern) =>
    data.cells.find((c) => c.nursery_id === n.id && c.pattern_id === p.id)
  return (
    <div className="table-wrap">
      <table className="care-matrix">
        <caption className="visually-hidden">
          {METRICS.find((m) => m.id === metric)?.label} a month, for each nursery and work pattern
        </caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Nursery</span>
            </th>
            {data.patterns.map((p) => (
              <th scope="col" key={p.id}>
                <WeekDots days={p.days} />
                {p.days} days
                <span className="care-hours">{p.hours}h</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.nurseries.map((n) => (
            <tr key={n.id}>
              <th scope="row">
                <span className="care-nursery">{n.name}</span>
                <span className="care-rates">
                  {n.rates.map((r) => `£${r.rate}`).join(' + ')} a {n.basis}
                </span>
                {!n.takes_tfc && <span className="care-flag">No Tax-Free Childcare</span>}
                {n.bills_leave && <span className="care-flag">Bills leave days</span>}
              </th>
              {data.patterns.map((p) => {
                const c = cell(n, p)
                if (!c) return <td key={p.id} />
                const best = data.best?.nursery_id === n.id && data.best?.pattern_id === p.id
                const on = selected === c
                const discount = c.childcare.children.some((k) => k.discount_applied)
                return (
                  <td key={p.id}>
                    <button
                      type="button"
                      className="care-cell"
                      aria-pressed={on}
                      data-best={best || undefined}
                      onClick={() => onSelect(c)}
                    >
                      <span className="care-cell-pattern" aria-hidden="true">
                        {patternLabel(p)}
                      </span>
                      <CellFigures cell={c} metric={metric} />
                      {(best || discount) && (
                        <span className="care-note">
                          {best ? 'Most left over' : 'Discount applied'}
                        </span>
                      )}
                      <span className="visually-hidden">
                        : {n.name}, {patternLabel(p)}
                      </span>
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Detail({ data, cell }: { data: Childcare; cell: CareCell }) {
  const n = data.nurseries.find((x) => x.id === cell.nursery_id)!
  const p = data.patterns.find((x) => x.id === cell.pattern_id)!
  const c = cell.childcare
  const pay = cell.pay
  const cb = cell.child_benefit
  const mo = cell.month
  return (
    <section className="register care-detail" aria-labelledby="care-detail-title">
      <SectionHeading
        id="care-detail-title"
        title={`${n.name} · ${patternLabel(p)}`}
        aside={
          <span>
            {c.days_attended} days attended
            {c.bills_leave ? `, ${c.days_billed} billed` : ''} · {c.hours_per_day} hours a day ·{' '}
            {c.leave_days} days’ leave, {c.bank_holidays} bank holidays
          </span>
        }
      />
      <div className="care-columns">
        <div>
          <h3 className="plan-sub">Pay, a month</h3>
          <ul className="ledger care-ledger">
            <li>
              <span className="ledger-mark" aria-hidden="true" />
              <span className="ledger-name">Gross for {p.hours}h a week</span>
              <Money pence={mo.pay.gross_pence} />
            </li>
            <li>
              <span className="ledger-mark" aria-hidden="true">
                −
              </span>
              <span className="ledger-name">NHS pension, {pay.pension_rate}% tier</span>
              <Money pence={-mo.pay.pension_pence} />
            </li>
            {mo.pay.tax_slices.map((s) => (
              <li key={s.label}>
                <span className="ledger-mark" aria-hidden="true">
                  −
                </span>
                <span className="ledger-name">
                  {s.label} rate, {s.rate}%
                </span>
                <Money pence={-s.pence} />
              </li>
            ))}
            <li>
              <span className="ledger-mark" aria-hidden="true">
                −
              </span>
              <span className="ledger-name">National Insurance</span>
              <Money pence={-mo.pay.ni_pence} />
            </li>
            <li className="ledger-totals">
              <span className="ledger-mark" aria-hidden="true">
                =
              </span>
              <span className="ledger-name">Take-home</span>
              <Money pence={mo.pay.net_pence} />
            </li>
          </ul>
        </div>
        <div>
          <h3 className="plan-sub">Childcare{cb.net_pence > 0 ? ' and benefits' : ''}, a month</h3>
          <ul className="ledger care-ledger">
            {c.children.map((k, i) => (
              <li key={k.name} className="care-child">
                <span className="ledger-mark" aria-hidden="true">
                  −
                </span>
                <span className="ledger-name">
                  {k.name}
                  <span className="ledger-note">
                    {k.discount_applied
                      ? `${formatPence(k.rate_pence)} less ${k.discount_pct}% = ${formatPence(k.effective_pence)}`
                      : formatPence(k.rate_pence)}{' '}
                    a {n.basis}
                    {n.basis === 'hour' ? `, ${formatPence(k.per_day_pence)} a day` : ''}
                    {k.funded_pence > 0 &&
                      `; funded hours take ${formatPence(mo.childcare.children[i]?.funded_pence ?? 0)} a month off`}
                  </span>
                </span>
                <Money pence={-(mo.childcare.children[i]?.cost_pence ?? 0)} />
                {c.takes_tfc && (
                  <>
                    <span className="ledger-mark" aria-hidden="true">
                      +
                    </span>
                    <span className="ledger-name ledger-sub">Tax-Free Childcare</span>
                    <Money pence={mo.childcare.children[i]?.tfc_pence ?? 0} />
                  </>
                )}
              </li>
            ))}
            {cb.gross_pence > 0 && (
              <li>
                <span className="ledger-mark" aria-hidden="true">
                  +
                </span>
                <span className="ledger-name">
                  Child Benefit
                  {Number(cb.clawback_pct) > 0 && (
                    <span className="ledger-note">
                      {Number(cb.clawback_pct) >= 100
                        ? 'all clawed back by the high-income charge'
                        : `${cb.clawback_pct}% clawed back`}
                    </span>
                  )}
                </span>
                <Money pence={mo.child_benefit_pence} />
              </li>
            )}
            <li className="ledger-totals">
              <span className="ledger-mark" aria-hidden="true">
                =
              </span>
              <span className="ledger-name">Left over</span>
              <Money pence={cell.monthly_left_pence} />
            </li>
          </ul>
          {!c.takes_tfc && (
            <p className="field-hint">This nursery doesn’t take Tax-Free Childcare.</p>
          )}
        </div>
      </div>
      <h3 className="plan-sub">Where the money goes, a year</h3>
      <ol className="care-flow">
        <li>
          <span>Gross pay</span>
          <Money pence={pay.gross_pence} />
        </li>
        <li data-out>
          <span>Tax, NI and pension</span>
          <Money pence={-(pay.tax_pence + pay.ni_pence + pay.pension_pence)} />
        </li>
        <li data-out>
          <span>Childcare after Tax-Free Childcare</span>
          <Money pence={-c.net_pence} />
        </li>
        {cb.net_pence > 0 && (
          <li>
            <span>Child Benefit</span>
            <Money pence={cb.net_pence} />
          </li>
        )}
        <li className="care-flow-total">
          <span>Left over</span>
          <Money pence={cell.annual_left_pence} />
        </li>
      </ol>
      <p className="chart-summary">
        Childcare takes {cell.share_of_take_home}% of take-home.
        {c.leave_waste_pence > 0 && (
          <>
            {' '}
            Leave days billed but not used: <Money pence={c.leave_waste_pence} /> a year.
          </>
        )}
      </p>
      <Compare data={data} cell={cell} />
    </section>
  )
}

function Compare({ data, cell }: { data: Childcare; cell: CareCell }) {
  const p = data.patterns.find((x) => x.id === cell.pattern_id)!
  const others = data.cells.filter(
    (c) => c.nursery_id === cell.nursery_id && c.pattern_id !== cell.pattern_id,
  )
  if (others.length === 0) return null
  return (
    <>
      <h3 className="plan-sub">Against {patternLabel(p)} at the same nursery</h3>
      <ul className="figure-list">
        {others.map((o) => {
          const q = data.patterns.find((x) => x.id === o.pattern_id)!
          return (
            <li key={o.pattern_id}>
              <span className="figure-name">
                {patternLabel(q)}
                {q.hours === p.hours && <span className="plan-opening-source">Same pay</span>}
              </span>
              <span
                className={
                  o.monthly_left_pence >= cell.monthly_left_pence ? 'ok-text' : 'plan-behind'
                }
              >
                {o.monthly_left_pence >= cell.monthly_left_pence ? '+' : ''}
                {formatPence(o.monthly_left_pence - cell.monthly_left_pence)} a month
              </span>
            </li>
          )
        })}
      </ul>
    </>
  )
}

// ---- setup -----------------------------------------------------------------------------------------

const PROFILE_FIELDS = [
  {
    key: 'salary',
    label: 'Salary for the reference hours',
    hint: 'Whole-time salary, £ a year',
    money: true,
  },
  { key: 'ref_hours', label: 'Reference hours a week' },
  { key: 'ref_days', label: 'Reference days a week' },
  { key: 'leave_days', label: 'Leave days a year', hint: 'For the reference pattern' },
  { key: 'bank_holidays', label: 'Bank holidays a year' },
  { key: 'tax_code', label: 'Tax code', text: true },
  {
    key: 'partner_income',
    label: 'Partner’s income',
    hint: 'For the Child Benefit charge, £ a year',
    money: true,
  },
  {
    key: 'payslip_monthly',
    label: 'Take-home on a recent payslip',
    hint: 'At the reference hours, £ a month',
    money: true,
  },
] as const

function ProfileForm({ data, onDone }: { data: Childcare; onDone: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(PROFILE_FIELDS.map((f) => [f.key, String(data.fields[f.key] ?? '')])),
  )
  const [npa, setNpa] = useState(Boolean(data.fields.net_pay_arrangement ?? true))
  const [useCb, setUseCb] = useState(Boolean(data.fields.use_child_benefit ?? true))
  const save = useBudgetMutation(() =>
    api.put('/api/plan/childcare/profile', {
      fields: { ...values, net_pay_arrangement: npa, use_child_benefit: useCb },
    }),
  )
  return (
    <form
      className="form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        save.mutate(undefined, { onSuccess: onDone })
      }}
    >
      <div className="field-row">
        {PROFILE_FIELDS.map((f) => (
          <Field key={f.key} label={f.label} hint={'hint' in f ? f.hint : undefined}>
            {(id, hintId) => (
              <input
                id={id}
                inputMode={'text' in f ? 'text' : 'decimal'}
                aria-describedby={hintId}
                value={values[f.key] ?? ''}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
              />
            )}
          </Field>
        ))}
      </div>
      <label className="choice">
        <input type="checkbox" checked={npa} onChange={(e) => setNpa(e.target.checked)} />
        <span>Pension comes off before tax (a net pay arrangement, as the NHS scheme does)</span>
      </label>
      <label className="choice">
        <input type="checkbox" checked={useCb} onChange={(e) => setUseCb(e.target.checked)} />
        <span>Count Child Benefit</span>
      </label>
      <ErrorText error={save.error} />
      <div className="form-actions">
        <button type="submit" className="button button-primary" disabled={save.isPending}>
          Save
        </button>
      </div>
    </form>
  )
}

function ChildSheet({ child, onClose }: { child: Child | null; onClose: () => void }) {
  const [name, setName] = useState(child?.name ?? '')
  const [born, setBorn] = useState(child?.born_on ?? '')
  const [two, setTwo] = useState(child?.eligible_at_two ?? false)
  const save = useBudgetMutation(() => {
    const body = { name, born_on: born || null, eligible_at_two: two }
    return child
      ? api.put(`/api/plan/childcare/children/${child.id}`, body)
      : api.post('/api/plan/childcare/children', body)
  })
  const remove = useBudgetMutation(() => api.del(`/api/plan/childcare/children/${child!.id}`))
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={child ? child.name : 'A child'}
      description="The date of birth decides when funded hours start. It stays in this app only."
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
        <Field label="Date of birth">
          {(id) => (
            <input id={id} type="date" value={born} onChange={(e) => setBorn(e.target.value)} />
          )}
        </Field>
        <label className="choice">
          <input type="checkbox" checked={two} onChange={(e) => setTwo(e.target.checked)} />
          <span>Eligible for funded hours at two (certain benefits, or care experience)</span>
        </label>
        <ErrorText error={save.error ?? remove.error} />
        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={save.isPending}>
            Save
          </button>
          {child && (
            <button
              type="button"
              className="button button-quiet"
              onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
            >
              Remove {child.name}
            </button>
          )}
        </div>
      </form>
    </Sheet>
  )
}

function NurserySheet({
  nursery,
  children,
  onClose,
}: {
  nursery: Nursery | null
  children: Child[]
  onClose: () => void
}) {
  const [name, setName] = useState(nursery?.name ?? '')
  const [basis, setBasis] = useState<'day' | 'hour'>(nursery?.basis ?? 'day')
  const [tfc, setTfc] = useState(nursery?.takes_tfc ?? true)
  const [leave, setLeave] = useState(nursery?.bills_leave ?? false)
  const [confirmed, setConfirmed] = useState(nursery?.discounts_confirmed ?? false)
  const [rates, setRates] = useState(
    children.map((_, i) => nursery?.rates[i] ?? { rate: '', discount_pct: '0', min_days: '0' }),
  )
  const save = useBudgetMutation(() => {
    const body = {
      name,
      basis,
      takes_tfc: tfc,
      bills_leave: leave,
      discounts_confirmed: confirmed,
      rates,
    }
    return nursery
      ? api.put(`/api/plan/childcare/nurseries/${nursery.id}`, body)
      : api.post('/api/plan/childcare/nurseries', body)
  })
  const remove = useBudgetMutation(() => api.del(`/api/plan/childcare/nurseries/${nursery!.id}`))
  const set = (i: number, key: 'rate' | 'discount_pct' | 'min_days', value: string) =>
    setRates(rates.map((r, j) => (j === i ? { ...r, [key]: value } : r)))
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()} title={nursery ? nursery.name : 'A nursery'}>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <div className="field-row">
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
          <Field label="Charges">
            {(id) => (
              <select
                id={id}
                value={basis}
                onChange={(e) => setBasis(e.target.value as 'day' | 'hour')}
              >
                <option value="day">By the day</option>
                <option value="hour">By the hour</option>
              </select>
            )}
          </Field>
        </div>
        {children.map((c, i) => (
          <fieldset key={c.id} className="care-rate">
            <legend>{c.name}</legend>
            <div className="field-row">
              <Field label={`£ a ${basis}`}>
                {(id) => (
                  <input
                    id={id}
                    inputMode="decimal"
                    value={rates[i]?.rate ?? ''}
                    onChange={(e) => set(i, 'rate', e.target.value)}
                    required
                  />
                )}
              </Field>
              <Field label="Discount %">
                {(id) => (
                  <input
                    id={id}
                    inputMode="decimal"
                    value={rates[i]?.discount_pct ?? '0'}
                    onChange={(e) => set(i, 'discount_pct', e.target.value)}
                  />
                )}
              </Field>
              <Field label="From days a week" hint="The discount starts here">
                {(id, hintId) => (
                  <input
                    id={id}
                    inputMode="decimal"
                    aria-describedby={hintId}
                    value={rates[i]?.min_days ?? '0'}
                    onChange={(e) => set(i, 'min_days', e.target.value)}
                  />
                )}
              </Field>
            </div>
          </fieldset>
        ))}
        <label className="choice">
          <input type="checkbox" checked={tfc} onChange={(e) => setTfc(e.target.checked)} />
          <span>Takes Tax-Free Childcare</span>
        </label>
        <label className="choice">
          <input type="checkbox" checked={leave} onChange={(e) => setLeave(e.target.checked)} />
          <span>Bills for leave days, when the child isn’t there</span>
        </label>
        <label className="choice">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>The nursery has confirmed these discounts</span>
        </label>
        <ErrorText error={save.error ?? remove.error} />
        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={save.isPending}>
            Save
          </button>
          {nursery && (
            <button
              type="button"
              className="button button-quiet"
              onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
            >
              Remove {nursery.name}
            </button>
          )}
        </div>
      </form>
    </Sheet>
  )
}

function Patterns({ patterns }: { patterns: Pattern[] }) {
  const [days, setDays] = useState('')
  const [hours, setHours] = useState('')
  const add = useBudgetMutation(() => api.post('/api/plan/childcare/patterns', { days, hours }))
  const remove = useBudgetMutation((id: number) => api.del(`/api/plan/childcare/patterns/${id}`))
  return (
    <>
      <ul className="care-patterns">
        {patterns.map((p) => (
          <li key={p.id}>
            <WeekDots days={p.days} />
            {patternLabel(p)}
            <button
              type="button"
              className="button button-quiet"
              disabled={remove.isPending}
              onClick={() => remove.mutate(p.id)}
            >
              Remove<span className="visually-hidden">: {patternLabel(p)}</span>
            </button>
          </li>
        ))}
      </ul>
      <form
        className="care-add-pattern"
        onSubmit={(e) => {
          e.preventDefault()
          add.mutate(undefined, {
            onSuccess: () => {
              setDays('')
              setHours('')
            },
          })
        }}
      >
        <Field label="Days a week">
          {(id) => (
            <input
              id={id}
              inputMode="decimal"
              value={days}
              onChange={(e) => setDays(e.target.value)}
              required
            />
          )}
        </Field>
        <Field label="Hours a week">
          {(id) => (
            <input
              id={id}
              inputMode="decimal"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
              required
            />
          )}
        </Field>
        <button type="submit" className="button" disabled={add.isPending}>
          Add the pattern
        </button>
      </form>
      <ErrorText error={add.error ?? remove.error} />
    </>
  )
}

function ImportOld() {
  const [error, setError] = useState<unknown>(null)
  const load = useBudgetMutation((state: unknown) => api.post('/api/plan/childcare/import', state))
  const choose = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setError(null)
    try {
      load.mutate(JSON.parse(await file.text()))
    } catch {
      setError(new Error('That file isn’t JSON. Use the old calculator’s “Download a copy”.'))
    }
  }
  return (
    <Field
      label="From the old calculator"
      hint="Its “Download a copy” file (state.json). This replaces the setup here."
    >
      {(id, hintId) => (
        <>
          <input
            id={id}
            type="file"
            accept="application/json,.json"
            aria-describedby={hintId}
            onChange={(e) => void choose(e)}
            className="file-input"
          />
          <ErrorText error={error ?? load.error} />
          {load.isSuccess && <p className="ok-text">Loaded.</p>}
        </>
      )}
    </Field>
  )
}

function Setup({ data }: { data: Childcare }) {
  const [child, setChild] = useState<Child | 'new' | null>(null)
  const [nursery, setNursery] = useState<Nursery | 'new' | null>(null)
  const [editing, setEditing] = useState(false)
  const f = data.fields
  return (
    <details className="setup-details care-setup" open={data.nurseries.length === 0 || undefined}>
      <summary>Pay, children, nurseries and work patterns</summary>
      <section aria-labelledby="care-pay-title">
        <SectionHeading
          id="care-pay-title"
          title="Pay and household"
          aside={
            !editing && (
              <button
                type="button"
                className="button button-quiet"
                onClick={() => setEditing(true)}
              >
                Change
              </button>
            )
          }
        />
        {editing ? (
          <ProfileForm data={data} onDone={() => setEditing(false)} />
        ) : (
          <p className="section-lead">
            {f.salary ? formatPence(Math.round(Number(f.salary) * 100)) : 'No salary'} for{' '}
            {String(f.ref_hours)}h over {String(f.ref_days)} days · tax code{' '}
            {String(f.tax_code || 'standard')} · {String(f.leave_days)} days’ leave · partner’s
            income {formatPence(Math.round(Number(f.partner_income || 0) * 100))}. Tax year{' '}
            {data.tax_year}.
          </p>
        )}
      </section>
      <section aria-labelledby="care-children-title">
        <SectionHeading
          id="care-children-title"
          title="Children"
          aside={
            <button type="button" className="button button-quiet" onClick={() => setChild('new')}>
              Add a child
            </button>
          }
        />
        <ul className="figure-list">
          {data.children.map((c) => (
            <li key={c.id}>
              <span className="figure-name">
                {c.name}
                <span className="plan-opening-source">
                  {c.born_on
                    ? `Born ${formatLongDate(c.born_on)} · funded hours ${c.funded_now ? 'now' : c.funded_from ? `from ${formatMonthYear(c.funded_from)}` : '—'}`
                    : 'No date of birth, so no funded hours yet'}
                </span>
              </span>
              <button type="button" className="button button-quiet" onClick={() => setChild(c)}>
                Change<span className="visually-hidden">: {c.name}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="care-nurseries-title">
        <SectionHeading
          id="care-nurseries-title"
          title="Nurseries"
          aside={
            <button type="button" className="button button-quiet" onClick={() => setNursery('new')}>
              Add a nursery
            </button>
          }
        />
        <ul className="figure-list">
          {data.nurseries.map((n) => (
            <li key={n.id}>
              <span className="figure-name">
                {n.name}
                <span className="plan-opening-source">
                  {n.rates
                    .map(
                      (r) =>
                        `£${r.rate}${Number(r.discount_pct) > 0 ? ` less ${r.discount_pct}% from ${r.min_days} days` : ''}`,
                    )
                    .join(' + ')}{' '}
                  a {n.basis}
                  {n.rates.some((r) => Number(r.discount_pct) > 0) &&
                    !n.discounts_confirmed &&
                    ' · discount not confirmed'}
                </span>
              </span>
              <button type="button" className="button button-quiet" onClick={() => setNursery(n)}>
                Change<span className="visually-hidden">: {n.name}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="care-patterns-title">
        <SectionHeading
          id="care-patterns-title"
          title="Work patterns"
          aside={<span>Pay follows hours; the nursery bill follows days</span>}
        />
        <Patterns patterns={data.patterns} />
      </section>
      <section aria-labelledby="care-import-title">
        <SectionHeading id="care-import-title" title="Bring the old setup in" />
        <ImportOld />
      </section>
      {child && (
        <ChildSheet child={child === 'new' ? null : child} onClose={() => setChild(null)} />
      )}
      {nursery && (
        <NurserySheet
          nursery={nursery === 'new' ? null : nursery}
          children={data.children}
          onClose={() => setNursery(null)}
        />
      )}
    </details>
  )
}

// ---- scenarios linked to what-ifs ------------------------------------------------------------------

function Scenarios({ data, cell }: { data: Childcare; cell: CareCell | undefined }) {
  const versions = usePlanVersions()
  const accounts = useAccounts()
  const [name, setName] = useState('')
  const [account, setAccount] = useState('joint_billing')
  const save = useBudgetMutation(() =>
    api.post('/api/plan/childcare/scenarios', {
      name,
      nursery_id: cell!.nursery_id,
      pattern_id: cell!.pattern_id,
      account,
    }),
  )
  const link = useBudgetMutation((v: { id: number; version_id: number | null }) =>
    api.put(`/api/plan/childcare/scenarios/${v.id}/link`, { version_id: v.version_id }),
  )
  const remove = useBudgetMutation((id: number) => api.del(`/api/plan/childcare/scenarios/${id}`))
  const whatIfs = (versions.data ?? []).filter((v) => v.type === 'what_if')
  const usable = (accounts.data ?? []).filter((a) => a.role !== 'dormant')
  const label = (nurseryId: number, patternId: number) => {
    const n = data.nurseries.find((x) => x.id === nurseryId)
    const p = data.patterns.find((x) => x.id === patternId)
    return `${n?.name ?? 'Nursery'}, ${p ? patternLabel(p) : ''}`
  }
  return (
    <section className="register" aria-labelledby="care-scenarios-title">
      <SectionHeading
        id="care-scenarios-title"
        title="Into the budget"
        aside={<span>A scenario linked to a what-if</span>}
      />
      <p className="section-lead">
        Linking sets Person 2’s pay for those hours in the what-if and adds the nursery as a
        calculated bill, with Tax-Free Childcare as its own line. Ages move on with the forecast, so
        funded hours start on their own; that’s worked out from the dates of birth, not entered.
      </p>
      {data.scenarios.length > 0 && (
        <ul className="figure-list">
          {data.scenarios.map((s) => (
            <li key={s.id}>
              <span className="figure-name">
                {s.name}
                <span className="plan-opening-source">
                  {label(s.nursery_id, s.pattern_id)} · paid from{' '}
                  {shortName(usable.find((a) => a.key === s.account)?.name ?? s.account)}
                </span>
              </span>
              <span className="row-actions">
                <label className="plan-measure">
                  <span>Linked to</span>
                  <select
                    value={s.version_id ?? ''}
                    disabled={link.isPending}
                    onChange={(e) =>
                      link.mutate({
                        id: s.id,
                        version_id: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                  >
                    <option value="">No what-if</option>
                    {whatIfs.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                </label>
                {!s.version_id && (
                  <button
                    type="button"
                    className="button button-quiet"
                    onClick={() => remove.mutate(s.id)}
                  >
                    Remove<span className="visually-hidden">: {s.name}</span>
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {cell && (
        <form
          className="care-save"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate(undefined, { onSuccess: () => setName('') })
          }}
        >
          <Field label={`Save ${label(cell.nursery_id, cell.pattern_id)} as`}>
            {(id) => (
              <input
                id={id}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Back 3 days"
                required
              />
            )}
          </Field>
          <Field label="Paid from">
            {(id) => (
              <select id={id} value={account} onChange={(e) => setAccount(e.target.value)}>
                {usable.map((a) => (
                  <option key={a.key} value={a.key} data-ink={inkFor(a)}>
                    {shortName(a.name)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <button type="submit" className="button" disabled={save.isPending || !name}>
            Save the scenario
          </button>
        </form>
      )}
      <ErrorText error={save.error ?? link.error ?? remove.error} />
    </section>
  )
}

export default function ChildcareTab() {
  const care = useChildcare()
  const [metric, setMetric] = useState<Metric>('left')
  const [picked, setPicked] = useState<{ nursery_id: number; pattern_id: number } | null>(null)
  if (care.isPending) return <Loading what="childcare" />
  if (care.isError) return <ErrorText error={care.error} />
  const data = care.data
  const target = picked ?? data.best
  const selected = target
    ? data.cells.find(
        (c) => c.nursery_id === target.nursery_id && c.pattern_id === target.pattern_id,
      )
    : undefined
  const check = data.payslip_check
  return (
    <>
      {check && (
        <Notice tone={check.matches ? 'ok' : 'warn'}>
          {check.matches ? 'Matches the payslip: ' : 'Out from the payslip: '}
          take-home at the reference hours works out at <Money pence={check.calculated_pence} /> a
          month, the payslip says <Money pence={check.payslip_pence} />
          {check.matches ? (
            '.'
          ) : (
            <>
              , <Money pence={Math.abs(check.difference_pence)} />{' '}
              {check.difference_pence > 0 ? 'too high' : 'too low'}.
            </>
          )}
        </Notice>
      )}
      <Setup data={data} />
      {data.nurseries.length === 0 || data.patterns.length === 0 ? (
        <p className="empty">
          Add a nursery and a work pattern, or bring the old calculator’s setup in, to see the
          matrix.
        </p>
      ) : (
        <section className="register" aria-labelledby="care-matrix-title">
          <SectionHeading
            id="care-matrix-title"
            title="Each pattern, each nursery"
            aside={<span>{plural(data.cells.length, 'combination', 'combinations')}, a month</span>}
          />
          <fieldset className="care-metrics">
            <legend className="visually-hidden">Show</legend>
            {METRICS.map((m) => (
              <label key={m.id} data-on={metric === m.id || undefined}>
                <input
                  type="radio"
                  name="care-metric"
                  checked={metric === m.id}
                  onChange={() => setMetric(m.id)}
                />
                <span>{m.label}</span>
              </label>
            ))}
          </fieldset>
          <Matrix
            data={data}
            metric={metric}
            selected={selected}
            onSelect={(c) => setPicked({ nursery_id: c.nursery_id, pattern_id: c.pattern_id })}
          />
        </section>
      )}
      {selected && <Detail data={data} cell={selected} />}
      {data.nurseries.length > 0 && <Scenarios data={data} cell={selected} />}
    </>
  )
}
