import { useState } from 'react'
import { api, useBudgetMutation } from '../../api.ts'
import { formatLongDate, parseDate, today } from '../../dates.ts'
import { parsePounds, penceToInput } from '../../money.ts'
import { useRenewals, type Renewal, type Renewals } from '../../plan.ts'
import { ErrorText, Field, Loading, Money, MoneyInput, SectionHeading, Sheet } from '../../ui.tsx'
import { plural } from './util.ts'

type Draft = Partial<Renewal>

function daysTo(iso: string): number {
  return Math.round((parseDate(iso).getTime() - today().getTime()) / 86_400_000)
}

function RenewalSheet({
  draft,
  candidates,
  onClose,
}: {
  draft: Draft
  candidates: Renewals['candidates']
  onClose: () => void
}) {
  const [name, setName] = useState(draft.name ?? '')
  const [provider, setProvider] = useState(draft.provider ?? '')
  const [lineId, setLineId] = useState(draft.line_id ? String(draft.line_id) : '')
  const [renewsOn, setRenewsOn] = useState(draft.renews_on ?? '')
  const [cost, setCost] = useState(penceToInput(draft.cost_pence ?? null))
  const [remind, setRemind] = useState(String(draft.remind_days ?? 30))
  const [notes, setNotes] = useState(draft.notes ?? '')
  const lines =
    draft.line_id && draft.line_label
      ? [{ line_id: draft.line_id, label: draft.line_label }, ...candidates]
      : candidates
  const save = useBudgetMutation(() => {
    const body = {
      name,
      provider: provider || null,
      line_id: lineId ? Number(lineId) : null,
      renews_on: renewsOn,
      cost_pence: cost ? parsePounds(cost) : null,
      remind_days: Number(remind) || 30,
      notes: notes || null,
    }
    return draft.id
      ? api.put(`/api/plan/renewals/${draft.id}`, body)
      : api.post('/api/plan/renewals', body)
  })
  const remove = useBudgetMutation(() => api.del(`/api/plan/renewals/${draft.id}`))
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={draft.id ? `Change ${draft.name}` : 'A renewal'}
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate(undefined, { onSuccess: onClose })
        }}
      >
        <div className="field-row">
          <Field label="What it is">
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
          <Field label="Provider">
            {(id) => (
              <input id={id} value={provider} onChange={(e) => setProvider(e.target.value)} />
            )}
          </Field>
        </div>
        <Field label="Paid by the budget line">
          {(id) => (
            <select id={id} value={lineId} onChange={(e) => setLineId(e.target.value)}>
              <option value="">None</option>
              {lines.map((l) => (
                <option key={l.line_id} value={l.line_id}>
                  {l.label}
                </option>
              ))}
            </select>
          )}
        </Field>
        <div className="field-row">
          <Field label="Renews on">
            {(id) => (
              <input
                id={id}
                type="date"
                value={renewsOn}
                onChange={(e) => setRenewsOn(e.target.value)}
                required
              />
            )}
          </Field>
          <Field label="Last year’s price">
            {(id) => <MoneyInput id={id} value={cost} onChange={setCost} />}
          </Field>
          <Field label="Remind this many days before">
            {(id) => (
              <input
                id={id}
                inputMode="numeric"
                value={remind}
                onChange={(e) => setRemind(e.target.value)}
              />
            )}
          </Field>
        </div>
        <Field label="Notes">
          {(id) => (
            <textarea id={id} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
          )}
        </Field>
        <ErrorText error={save.error ?? remove.error} />
        <div className="form-actions">
          <button
            type="submit"
            className="button button-primary"
            disabled={save.isPending || !name || !renewsOn}
          >
            Save
          </button>
          {draft.id && (
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

export default function RenewalsTab() {
  const view = useRenewals()
  const [open, setOpen] = useState<Draft | null>(null)
  if (view.isPending) return <Loading what="renewals" />
  if (view.isError) return <ErrorText error={view.error} />
  const { renewals, candidates } = view.data
  return (
    <>
      <section className="register" aria-labelledby="renewals-title">
        <SectionHeading
          id="renewals-title"
          title="Coming up"
          aside={
            <button type="button" className="button" onClick={() => setOpen({})}>
              Add a renewal
            </button>
          }
        />
        <p className="section-lead">
          The inbox reminds you before each one: 30 days by default. The mortgage is reminded seven
          months before its fix ends, from the Mortgage tab.
        </p>
        {renewals.length === 0 ? (
          <p className="empty">
            Nothing on the register yet. The insurance lines below are a good start.
          </p>
        ) : (
          <ul className="figure-list">
            {renewals.map((r) => {
              const days = daysTo(r.renews_on)
              return (
                <li key={r.id}>
                  <span className="figure-name">
                    {r.name}
                    <span className="plan-opening-source">
                      {[r.provider, r.line_label ? `paid by ${r.line_label}` : null]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                    <span
                      className={
                        days <= r.remind_days && days >= 0 ? 'plan-behind' : 'plan-opening-source'
                      }
                    >
                      {formatLongDate(r.renews_on)}
                      {days >= 0 ? `, in ${plural(days, 'day', 'days')}` : ', passed'}
                    </span>
                  </span>
                  <span className="row-actions">
                    {r.cost_pence !== null && <Money pence={r.cost_pence} />}
                    <button
                      type="button"
                      className="button button-quiet"
                      onClick={() => setOpen(r)}
                    >
                      Change<span className="visually-hidden">: {r.name}</span>
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </section>
      {candidates.length > 0 && (
        <section className="register" aria-labelledby="renewal-candidates">
          <SectionHeading
            id="renewal-candidates"
            title="Insurance in the budget"
            aside={<span>Not on the register yet</span>}
          />
          <ul className="figure-list">
            {candidates.map((c) => (
              <li key={c.line_id}>
                <span className="figure-name">
                  {c.label}
                  <span className="plan-opening-source">
                    <Money pence={c.monthly_pence} /> a month
                  </span>
                </span>
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() =>
                    setOpen({
                      name: c.name,
                      provider: c.provider,
                      line_id: c.line_id,
                      line_label: c.label,
                      renews_on: c.renewal_date ?? undefined,
                    })
                  }
                >
                  Add its renewal date<span className="visually-hidden">: {c.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {open && <RenewalSheet draft={open} candidates={candidates} onClose={() => setOpen(null)} />}
    </>
  )
}
