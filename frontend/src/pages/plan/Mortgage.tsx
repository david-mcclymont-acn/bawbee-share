import { useState } from 'react'
import { Link } from 'react-router'
import { api, useBudgetMutation } from '../../api.ts'
import { formatLongDate, formatMonthYear, isoDate, today } from '../../dates.ts'
import { parsePounds, penceToInput } from '../../money.ts'
import { useMortgage, usePlanVersions, type MortgageView, type Overpay } from '../../plan.ts'
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
import { fromMonthInput, plural, toMonthInput } from './util.ts'

const BANDS = [60, 75, 80, 85, 90]

function fixEnd(m: NonNullable<MortgageView['mortgage']>) {
  if (!m.fix_end_on) return 'not set'
  return m.fix_end_exact
    ? formatLongDate(m.fix_end_on)
    : `${formatMonthYear(m.fix_end_on)} (approx.)`
}

function LtvRuler({ ltv }: { ltv: NonNullable<MortgageView['ltv']> }) {
  const pct = Number(ltv.pct)
  const lo = 50
  const hi = 95
  const at = (v: number) => `${((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * 100}%`
  return (
    <div className="ltv-ruler" aria-hidden="true">
      <div className="ltv-scale">
        {BANDS.map((b) => (
          <span
            key={b}
            className="ltv-tick"
            style={{ left: at(b) }}
            data-current={ltv.band === b || undefined}
          >
            {b}%
          </span>
        ))}
        <span className="ltv-mark" style={{ left: at(pct) }} />
      </div>
    </div>
  )
}

function DetailsSheet({ view, onClose }: { view: MortgageView; onClose: () => void }) {
  const m = view.mortgage!
  const [fixEndOn, setFixEndOn] = useState(m.fix_end_on ?? '')
  const [exact, setExact] = useState(m.fix_end_exact)
  const [payment, setPayment] = useState(penceToInput(m.payment_pence))
  const [rate, setRate] = useState(m.rate_pct ?? '')
  const [allowance, setAllowance] = useState(m.overpay_allowance_pct)
  const [basis, setBasis] = useState(m.allowance_basis)
  const [value, setValue] = useState(penceToInput(m.property_value_pence))
  const [valueOn, setValueOn] = useState(
    m.property_value_on ? toMonthInput(m.property_value_on) : '',
  )
  const save = useBudgetMutation(() =>
    api.put('/api/plan/mortgage', {
      fix_end_on: fixEndOn || null,
      fix_end_exact: exact,
      payment_pence: parsePounds(payment),
      rate_pct: rate,
      overpay_allowance_pct: allowance,
      allowance_basis: basis,
      property_value_pence: parsePounds(value),
      property_value_on: valueOn ? fromMonthInput(valueOn) : null,
    }),
  )
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={`The ${m.lender} mortgage`}
      description="The payment is the one place the mortgage amount lives: changing it moves the budget line too."
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <div className="field-row">
          <Field label="The fix ends" hint="From the offer letter">
            {(id, hintId) => (
              <input
                id={id}
                type="date"
                aria-describedby={hintId}
                value={fixEndOn}
                onChange={(e) => setFixEndOn(e.target.value)}
              />
            )}
          </Field>
          <label className="choice">
            <input type="checkbox" checked={exact} onChange={(e) => setExact(e.target.checked)} />
            <span>That’s the exact date</span>
          </label>
        </div>
        <div className="field-row">
          <Field label="Monthly payment">
            {(id) => <MoneyInput id={id} value={payment} onChange={setPayment} />}
          </Field>
          <Field label="Rate, %">
            {(id) => (
              <input
                id={id}
                inputMode="decimal"
                value={rate}
                onChange={(e) => setRate(e.target.value)}
              />
            )}
          </Field>
        </div>
        <div className="field-row">
          <Field label="Overpay each year without a charge, %">
            {(id) => (
              <input
                id={id}
                inputMode="decimal"
                value={allowance}
                onChange={(e) => setAllowance(e.target.value)}
              />
            )}
          </Field>
          <Field label="Allowance year runs from">
            {(id) => (
              <select
                id={id}
                value={basis}
                onChange={(e) => setBasis(e.target.value as typeof basis)}
              >
                <option value="anniversary">The mortgage’s anniversary</option>
                <option value="calendar_year">1 January</option>
              </select>
            )}
          </Field>
        </div>
        <div className="field-row">
          <Field label="Property value">
            {(id) => <MoneyInput id={id} value={value} onChange={setValue} />}
          </Field>
          <Field label="Valued in">
            {(id) => (
              <input
                id={id}
                type="month"
                value={valueOn}
                onChange={(e) => setValueOn(e.target.value)}
              />
            )}
          </Field>
        </div>
        <ErrorText error={save.error} />
        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={save.isPending}>
            Save
          </button>
        </div>
      </form>
    </Sheet>
  )
}

function StatementSheet({ onClose }: { onClose: () => void }) {
  const [on, setOn] = useState(isoDate(today()))
  const [balance, setBalance] = useState('')
  const pence = parsePounds(balance)
  const save = useBudgetMutation(() =>
    api.post('/api/plan/mortgage/statements', { on, balance_pence: pence }),
  )
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title="A statement balance"
      description="The balance after that month’s payment. The schedule carries on from it."
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <div className="field-row">
          <Field label="Statement date">
            {(id) => (
              <input
                id={id}
                type="date"
                max={isoDate(today())}
                value={on}
                onChange={(e) => setOn(e.target.value)}
                required
                autoFocus
              />
            )}
          </Field>
          <Field label="Balance owed">
            {(id) => <MoneyInput id={id} value={balance} onChange={setBalance} />}
          </Field>
        </div>
        <ErrorText error={save.error} />
        <div className="form-actions">
          <button
            type="submit"
            className="button button-primary"
            disabled={save.isPending || !pence}
          >
            Save the statement
          </button>
        </div>
      </form>
    </Sheet>
  )
}

function Remortgage({ view }: { view: MortgageView }) {
  const [rate, setRate] = useState('')
  const [base, setBase] = useState('')
  const versions = usePlanVersions()
  const make = useBudgetMutation(() =>
    api.post<{ version_id: number; name: string; payment_pence: number }>(
      '/api/plan/mortgage/remortgage',
      {
        rate_pct: rate,
        base_version_id: base ? Number(base) : null,
      },
    ),
  )
  const m = view.mortgage!
  return (
    <section className="register" aria-labelledby="remortgage-title" id="remortgage">
      <SectionHeading
        id="remortgage-title"
        title="Remortgage what-if"
        aside={<span>From {m.fix_end_on ? formatMonthYear(m.fix_end_on) : 'the fix end'}</span>}
      />
      <p className="section-lead">
        About <Money pence={view.balance_at_fix_end_pence} /> will be owed when the fix ends. Enter
        a rate you’ve been offered: it makes a what-if starting then, with the new payment over the{' '}
        {plural(Math.max(0, view.months_left - (view.months_to_fix_end ?? 0)), 'month', 'months')}{' '}
        left.
      </p>
      <form
        className="care-save"
        onSubmit={(e) => {
          e.preventDefault()
          make.mutate(undefined)
        }}
      >
        <Field label="Builds on" hint="To keep a what-if’s changes after the fix ends, build on it">
          {(id, hintId) => (
            <select
              id={id}
              aria-describedby={hintId}
              value={base}
              onChange={(e) => setBase(e.target.value)}
            >
              <option value="">The live budget</option>
              {(versions.data ?? [])
                .filter((v) => v.type === 'what_if')
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
            </select>
          )}
        </Field>
        <Field label="New rate, %">
          {(id) => (
            <input
              id={id}
              inputMode="decimal"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              placeholder="e.g. 4.19"
              required
            />
          )}
        </Field>
        <button type="submit" className="button button-primary" disabled={make.isPending || !rate}>
          Make the what-if
        </button>
      </form>
      <ErrorText error={make.error} />
      {make.data && (
        <Notice tone="ok" urgent>
          {make.data.name}: <Money pence={make.data.payment_pence} /> a month from{' '}
          {m.fix_end_on ? formatMonthYear(m.fix_end_on) : 'the fix end'}. Put it on a path in{' '}
          <Link to="/plan/forecast">Forecast</Link>, or see it in{' '}
          <Link to="/versions">Versions</Link>.
        </Notice>
      )}
    </section>
  )
}

function Overpayments() {
  const [monthly, setMonthly] = useState('')
  const [lump, setLump] = useState('')
  const [lumpOn, setLumpOn] = useState(toMonthInput(isoDate(today())))
  const [result, setResult] = useState<Overpay | null>(null)
  const [error, setError] = useState<unknown>(null)
  const run = () => {
    const params = new URLSearchParams({
      monthly_pence: String(parsePounds(monthly || '0') ?? 0),
      lump_pence: String(parsePounds(lump || '0') ?? 0),
      lump_on: fromMonthInput(lumpOn),
    })
    setError(null)
    api
      .get<Overpay>(`/api/plan/mortgage/overpay?${params.toString()}`)
      .then(setResult)
      .catch(setError)
  }
  return (
    <section className="register" aria-labelledby="overpay-title">
      <SectionHeading id="overpay-title" title="Overpaying" />
      <form
        className="care-save"
        onSubmit={(e) => {
          e.preventDefault()
          run()
        }}
      >
        <Field label="Extra each month">
          {(id) => <MoneyInput id={id} value={monthly} onChange={setMonthly} />}
        </Field>
        <Field label="A lump sum">
          {(id) => <MoneyInput id={id} value={lump} onChange={setLump} />}
        </Field>
        <Field label="Paid in">
          {(id) => (
            <input
              id={id}
              type="month"
              value={lumpOn}
              onChange={(e) => setLumpOn(e.target.value)}
            />
          )}
        </Field>
        <button type="submit" className="button">
          Work it out
        </button>
      </form>
      <ErrorText error={error} />
      {result && (
        <div aria-live="polite">
          <ul className="figure-list">
            <li>
              <span className="figure-name">Interest saved</span>
              <Money pence={result.interest_saved_pence} className="money-large" />
            </li>
            <li>
              <span className="figure-name">Paid off sooner by</span>
              <span>{plural(result.months_saved, 'month', 'months')}</span>
            </li>
            {result.paid_off_with && (
              <li>
                <span className="figure-name">Paid off</span>
                <span>
                  {formatMonthYear(result.paid_off_with)}
                  {result.paid_off_without
                    ? `, not ${formatMonthYear(result.paid_off_without)}`
                    : ''}
                </span>
              </li>
            )}
          </ul>
          {result.over_allowance.map((o) => (
            <Notice key={o.year_from} tone="warn">
              In the allowance year from {formatMonthYear(o.year_from)} that’s{' '}
              <Money pence={o.overpaid_pence} /> overpaid, over the{' '}
              <Money pence={o.allowance_pence} /> allowed without a charge in the fix.
            </Notice>
          ))}
        </div>
      )}
    </section>
  )
}

export default function MortgageTab() {
  const view = useMortgage()
  const [open, setOpen] = useState<'details' | 'statement' | null>(null)
  if (view.isPending) return <Loading what="the mortgage" />
  if (view.isError) return <ErrorText error={view.error} />
  const v = view.data
  const m = v.mortgage
  if (!m) {
    return (
      <p className="empty">
        No mortgage set up yet. On the server, run{' '}
        <code>bawbee import-plan - &lt; seed/budget-seed.json</code> to bring in the Northfield
        mortgage from the seed.
      </p>
    )
  }
  const lineDiffers = m.line_amount_pence !== null && m.line_amount_pence !== m.payment_pence
  return (
    <>
      <section className="register" aria-labelledby="mortgage-title">
        <SectionHeading
          id="mortgage-title"
          title={`${m.lender}, ${m.rate_pct}% fixed`}
          aside={
            <span className="row-actions">
              <button
                type="button"
                className="button button-quiet"
                onClick={() => setOpen('statement')}
              >
                Enter a statement
              </button>
              <button
                type="button"
                className="button button-quiet"
                onClick={() => setOpen('details')}
              >
                Change the details
              </button>
            </span>
          }
        />
        <ul className="figure-list mortgage-facts">
          <li>
            <span className="figure-name">
              Owed now
              <span className="plan-opening-source">
                {v.balance_anchored && v.statement
                  ? `From the ${formatLongDate(v.statement.on)} statement, carried on`
                  : 'Estimated from the schedule: enter a statement to anchor it'}
              </span>
            </span>
            <Money pence={v.balance_pence} className="money-large" />
          </li>
          <li>
            <span className="figure-name">
              The fix ends
              <span className="plan-opening-source">{fixEnd(m)}</span>
            </span>
            <span className="money-large">
              {v.months_to_fix_end !== null ? plural(v.months_to_fix_end, 'month', 'months') : '—'}
            </span>
          </li>
          {v.remortgage_window && (
            <li>
              <span className="figure-name">
                Remortgage window opens
                <span className="plan-opening-source">
                  Seven months before the fix ends; the inbox reminds you
                </span>
              </span>
              <span>{formatMonthYear(v.remortgage_window)}</span>
            </li>
          )}
          <li>
            <span className="figure-name">
              Payment
              <span className="plan-opening-source">
                {m.line_id
                  ? lineDiffers
                    ? 'The budget line differs: save the details to bring it in line'
                    : 'The budget’s Mortgage line follows this'
                  : 'Not linked to a budget line'}
              </span>
            </span>
            <Money pence={m.payment_pence ?? 0} />
          </li>
          {v.ltv && m.property_value_pence && (
            <li className="mortgage-ltv">
              <span className="figure-name">
                Loan to value
                <span className="plan-opening-source">
                  Of <Money pence={m.property_value_pence} />
                  {m.property_value_on ? ` (${formatMonthYear(m.property_value_on)})` : ''}
                  {v.ltv.next_band !== null && (
                    <>
                      {' '}
                      · <Money pence={v.ltv.to_next_band_pence} /> more paid off reaches{' '}
                      {v.ltv.next_band}%
                    </>
                  )}
                </span>
              </span>
              <span className="money-large money">{v.ltv.pct}%</span>
              <LtvRuler ltv={v.ltv} />
            </li>
          )}
        </ul>
        {m.notes && <p className="field-hint">{m.notes}</p>}
        <details className="chart-table">
          <summary>Show the payments to the fix end</summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Month</th>
                  <th scope="col" className="num">
                    Interest
                  </th>
                  <th scope="col" className="num">
                    Payment
                  </th>
                  <th scope="col" className="num">
                    Owed after
                  </th>
                </tr>
              </thead>
              <tbody>
                {v.schedule.map((r) => (
                  <tr key={r.on}>
                    <th scope="row">{formatMonthYear(r.on)}</th>
                    <td className="num">
                      <Money pence={r.interest_pence} />
                    </td>
                    <td className="num">
                      <Money pence={r.payment_pence} />
                    </td>
                    <td className="num">
                      <Money pence={r.closing_pence} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>
      <Remortgage view={v} />
      <Overpayments />
      <section className="register" aria-labelledby="history-title">
        <SectionHeading id="history-title" title="Mortgages before" />
        <ol className="mortgage-history">
          {v.history.map((h, i) => (
            <li key={`${h.lender}-${i}`}>
              <span className="mortgage-year">{h.started ?? '—'}</span>
              <span>{h.lender}</span>
            </li>
          ))}
          <li data-current>
            <span className="mortgage-year">{m.started_on ? m.started_on.slice(0, 4) : '—'}</span>
            <span>
              {m.lender} <span className="plan-tag">Now</span>
            </span>
          </li>
        </ol>
        {v.statements.length > 0 && (
          <>
            <h3 className="plan-sub">Statements entered</h3>
            <ul className="figure-list">
              {v.statements.map((s) => (
                <li key={s.on}>
                  <span className="figure-name">{formatLongDate(s.on)}</span>
                  <Money pence={s.balance_pence} />
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      {open === 'details' && <DetailsSheet view={v} onClose={() => setOpen(null)} />}
      {open === 'statement' && <StatementSheet onClose={() => setOpen(null)} />}
    </>
  )
}
