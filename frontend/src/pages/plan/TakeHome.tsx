import { useState } from 'react'
import { inkFor, shortName } from '../../accounts.ts'
import { api, useAccounts, useBudgetMutation } from '../../api.ts'
import { formatDay, formatLongDate } from '../../dates.ts'
import { formatPence, parsePounds, penceToInput } from '../../money.ts'
import { useTakeHome, type TakeHomePerson } from '../../plan.ts'
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

const PENSION: Record<TakeHomePerson['income']['pension_scheme'], string> = {
  nhs_scotland: 'NHS Scotland pension',
  workplace_pct: 'Workplace pension',
  none: 'No pension',
}

const month = (annual: number) => Math.round(annual / 12)

function IncomeSheet({ person, onClose }: { person: TakeHomePerson; onClose: () => void }) {
  const i = person.income
  const [gross, setGross] = useState(penceToInput(i.gross_annual_pence))
  const [allowance, setAllowance] = useState(penceToInput(i.benefits_allowance_pence))
  const [sacrifice, setSacrifice] = useState(penceToInput(i.salary_sacrifice_pence))
  const [scheme, setScheme] = useState(i.pension_scheme)
  const [pct, setPct] = useState(i.pension_pct ?? '')
  const [code, setCode] = useState(i.tax_code ?? '')
  const [payslip, setPayslip] = useState(penceToInput(i.payslip_net_monthly_pence))
  const [payslipOn, setPayslipOn] = useState(i.payslip_on ?? '')
  const save = useBudgetMutation(() =>
    api.put(`/api/plan/take-home/${i.id}`, {
      gross_annual_pence: parsePounds(gross),
      benefits_allowance_pence: allowance ? parsePounds(allowance) : null,
      salary_sacrifice_pence: sacrifice ? parsePounds(sacrifice) : null,
      pension_scheme: scheme,
      pension_pct: scheme === 'workplace_pct' ? pct : null,
      tax_code: code || null,
      payslip_net_monthly_pence: payslip ? parsePounds(payslip) : null,
      payslip_on: payslipOn || null,
    }),
  )
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${person.name}’s pay`}
      description="Gross is the actual pay for the hours worked, not the whole-time figure."
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <div className="field-row">
          <Field label="Gross pay, a year">
            {(id) => <MoneyInput id={id} value={gross} onChange={setGross} autoFocus />}
          </Field>
          <Field label="Cash allowance, a year" hint="Paid with salary and taxed">
            {(id) => <MoneyInput id={id} value={allowance} onChange={setAllowance} />}
          </Field>
          <Field label="Salary sacrifice, a year" hint="Comes off before tax and NI">
            {(id) => <MoneyInput id={id} value={sacrifice} onChange={setSacrifice} />}
          </Field>
        </div>
        <div className="field-row">
          <Field label="Pension">
            {(id) => (
              <select
                id={id}
                value={scheme}
                onChange={(e) => setScheme(e.target.value as typeof scheme)}
              >
                <option value="nhs_scotland">NHS Scotland (tiered on actual pay)</option>
                <option value="workplace_pct">Workplace, a percentage</option>
                <option value="none">None, or all by salary sacrifice</option>
              </select>
            )}
          </Field>
          {scheme === 'workplace_pct' && (
            <Field label="Pension, %">
              {(id) => (
                <input
                  id={id}
                  inputMode="decimal"
                  value={pct}
                  onChange={(e) => setPct(e.target.value)}
                />
              )}
            </Field>
          )}
          <Field label="Tax code" hint="From a payslip, e.g. S1257L">
            {(id, hintId) => (
              <input
                id={id}
                aria-describedby={hintId}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                autoCapitalize="characters"
              />
            )}
          </Field>
        </div>
        <div className="field-row">
          <Field label="Net pay on a recent payslip">
            {(id) => <MoneyInput id={id} value={payslip} onChange={setPayslip} />}
          </Field>
          <Field label="Paid on">
            {(id) => (
              <input
                id={id}
                type="date"
                value={payslipOn}
                onChange={(e) => setPayslipOn(e.target.value)}
              />
            )}
          </Field>
        </div>
        <ErrorText error={save.error} />
        <div className="form-actions">
          <button
            type="submit"
            className="button button-primary"
            disabled={save.isPending || parsePounds(gross) === null}
          >
            Save
          </button>
        </div>
      </form>
    </Sheet>
  )
}

function Within({ difference, tolerance }: { difference: number; tolerance: number }) {
  const ok = Math.abs(difference) <= tolerance
  return (
    <span className={ok ? 'ok-text' : 'plan-behind'}>
      {difference === 0 ? 'Exact' : `${difference > 0 ? '+' : ''}${formatPence(difference)}`}
      {ok ? ', within £5' : ''}
    </span>
  )
}

function Person({ p, tolerance }: { p: TakeHomePerson; tolerance: number }) {
  const accounts = useAccounts()
  const hub = (accounts.data ?? []).find((a) => a.role === 'salary_hub' && a.owner === p.person)
  const [editing, setEditing] = useState(false)
  const use = useBudgetMutation(() => api.post(`/api/plan/take-home/${p.income.id}/use`))
  const c = p.calculated
  const differs = p.budget_monthly_pence !== null && p.budget_monthly_pence !== c.net_monthly_pence
  return (
    <section className="register" aria-labelledby={`th-${p.person}`} data-ink={inkFor(hub)}>
      <SectionHeading
        id={`th-${p.person}`}
        title={p.name}
        aside={
          <button type="button" className="button button-quiet" onClick={() => setEditing(true)}>
            Change the pay details<span className="visually-hidden"> for {p.name}</span>
          </button>
        }
      />
      <p className="section-lead">
        {p.income.employer ?? 'Employer not set'} · tax code{' '}
        {p.income.tax_code ?? 'not set, so the standard allowance'} ·{' '}
        {PENSION[p.income.pension_scheme]}
        {p.income.pension_scheme === 'workplace_pct' && p.income.pension_pct
          ? ` ${p.income.pension_pct}%`
          : ''}
      </p>
      <div className="table-wrap">
        <table className="takehome-table">
          <thead>
            <tr>
              <th scope="col">
                <span className="visually-hidden">What</span>
              </th>
              <th scope="col" className="num">
                A year
              </th>
              <th scope="col" className="num">
                A month
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Gross pay</th>
              <td className="num">
                <Money pence={c.gross_pence} />
              </td>
              <td className="num">
                <Money pence={month(c.gross_pence)} />
              </td>
            </tr>
            {c.sacrifice_pence > 0 && (
              <tr>
                <th scope="row">Salary sacrifice</th>
                <td className="num">
                  <Money pence={-c.sacrifice_pence} />
                </td>
                <td className="num">
                  <Money pence={-month(c.sacrifice_pence)} />
                </td>
              </tr>
            )}
            {c.pension_pence > 0 && (
              <tr>
                <th scope="row">Pension, {c.pension_rate}%</th>
                <td className="num">
                  <Money pence={-c.pension_pence} />
                </td>
                <td className="num">
                  <Money pence={-month(c.pension_pence)} />
                </td>
              </tr>
            )}
            {c.tax_slices.map((s) => (
              <tr key={s.label}>
                <th scope="row">
                  Income tax, {s.label.toLowerCase()} {s.rate}%
                </th>
                <td className="num">
                  <Money pence={-s.pence} />
                </td>
                <td className="num">
                  <Money pence={-month(s.pence)} />
                </td>
              </tr>
            ))}
            <tr>
              <th scope="row">National Insurance</th>
              <td className="num">
                <Money pence={-c.ni_pence} />
              </td>
              <td className="num">
                <Money pence={-month(c.ni_pence)} />
              </td>
            </tr>
            <tr className="plan-compare-total">
              <th scope="row">Take-home</th>
              <td className="num">
                <Money pence={c.net_pence} />
              </td>
              <td className="num">
                <Money pence={c.net_monthly_pence} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <h3 className="plan-sub">Checked against</h3>
      <ul className="figure-list">
        <li>
          <span className="figure-name">
            The live budget
            <span className="plan-opening-source">
              {p.budget_monthly_pence === null
                ? 'Uses the calculation'
                : differs
                  ? 'A figure set in the budget (such as maternity pay), not this calculation'
                  : 'The same as the calculation'}
            </span>
          </span>
          <span className="row-actions">
            {p.budget_monthly_pence !== null && <Money pence={p.budget_monthly_pence} />}
            {differs && (
              <button
                type="button"
                className="button button-quiet"
                disabled={use.isPending}
                onClick={() => use.mutate(undefined)}
              >
                <span>
                  Use <Money pence={c.net_monthly_pence} /> instead
                </span>
              </button>
            )}
          </span>
        </li>
        {p.payslip_monthly_pence !== null && (
          <li>
            <span className="figure-name">
              A payslip
              <span className="plan-opening-source">
                {p.income.payslip_on ? formatLongDate(p.income.payslip_on) : 'Date not given'}:{' '}
                <Money pence={p.payslip_monthly_pence} />
              </span>
            </span>
            <Within
              difference={c.net_monthly_pence - p.payslip_monthly_pence}
              tolerance={tolerance}
            />
          </li>
        )}
        {p.salaries.map((s) => (
          <li key={s.on}>
            <span className="figure-name">
              Landed {formatDay(s.on)}
              <span className="plan-opening-source">
                {s.description} into {shortName(hub?.name ?? '')}: <Money pence={s.pence} />
              </span>
            </span>
            <Within difference={-s.difference_pence} tolerance={tolerance} />
          </li>
        ))}
        {p.salaries.length === 0 && (
          <li>
            <span className="figure-name">
              The bank
              <span className="plan-opening-source">
                No salary found around a recent payday yet
              </span>
            </span>
          </li>
        )}
      </ul>
      <ErrorText error={use.error} />
      {editing && <IncomeSheet person={p} onClose={() => setEditing(false)} />}
    </section>
  )
}

function Source({ text }: { text: string | null }) {
  if (!text) return null
  const parts = text.split(/(https:\/\/\S+)/)
  return (
    <li>
      {parts.map((part, i) =>
        part.startsWith('https://') ? (
          <a key={i} href={part.replace(/[;,.]$/, '')} rel="noreferrer" target="_blank">
            {part.replace(/^https:\/\/(www\.)?/, '').replace(/[;,.]$/, '')}
          </a>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </li>
  )
}

export default function TakeHomeTab() {
  const view = useTakeHome()
  if (view.isPending) return <Loading what="take-home pay" />
  if (view.isError) return <ErrorText error={view.error} />
  const v = view.data
  return (
    <>
      {!v.exact_year && (
        <Notice tone="warn">
          This tax year’s rates aren’t in yet, so these use {v.tax_year}’s. Add the new year’s file
          to config/tax.
        </Notice>
      )}
      {v.people.map((p) => (
        <Person key={p.person} p={p} tolerance={v.tolerance_pence} />
      ))}
      <section className="register" aria-labelledby="th-sources">
        <SectionHeading
          id="th-sources"
          title={`Tax year ${v.tax_year}`}
          aside={<span>Where the rates come from</span>}
        />
        <ul className="takehome-sources">
          <Source text={v.sources.bands} />
          <Source text={v.sources.national_insurance} />
          <Source text={v.sources.pension} />
        </ul>
      </section>
    </>
  )
}
