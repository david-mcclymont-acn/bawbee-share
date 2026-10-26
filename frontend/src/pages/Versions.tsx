import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import {
  api,
  useBudgetMutation,
  useLatestPromotion,
  useVersions,
  type LineChange,
  type Payday,
  type PromotionPreview,
  type Version,
} from '../api.ts'
import { addMonths, formatLongDate, isoDate, today } from '../dates.ts'
import { formatPence } from '../money.ts'
import { ErrorText, Field, Loading, Notice, PageHeading, SectionHeading, Sheet } from '../ui.tsx'

const SETTING_NAMES: Record<string, string> = {
  split_basis: 'Joint split',
  fixed_shares: 'Fixed shares',
  rounding_step_pence: 'Rounding',
  buffer_pence: 'Buffer',
  extra_lines_mode: 'Lines one person pays',
}

function describe(value: unknown, field: string): string {
  if (value === null || value === undefined || value === '') return 'nothing'
  if (typeof value === 'number' && field.endsWith('_pence')) return formatPence(value)
  if (typeof value === 'object') return 'changed'
  return String(value)
}

function ChangeList({ changes }: { changes: LineChange[] }) {
  if (changes.length === 0) return <p className="empty">No line changes.</p>
  return (
    <ul className="change-list">
      {changes.map((c) => (
        <li key={c.line_id} data-change={c.change}>
          <span className="change-kind">
            {c.change === 'modified' ? 'Changed' : c.change === 'added' ? 'Added' : 'Removed'}
          </span>
          <span className="change-label">{c.label}</span>
          <span className="change-detail">
            {c.change === 'modified'
              ? Object.entries(c.fields)
                  .map(
                    ([f, [a, b]]) =>
                      `${f === 'amount_pence' ? '' : `${f.replace(/_/g, ' ')}: `}${describe(a, f)} → ${describe(b, f)}`,
                  )
                  .join('; ')
              : c.fields.amount_pence
                ? describe(
                    c.change === 'added' ? c.fields.amount_pence[1] : c.fields.amount_pence[0],
                    'amount_pence',
                  )
                : ''}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** The next payday's transfer date: the default day a promotion takes effect (§5.4). */
function useNextPayday(): string | undefined {
  const now = today()
  const [y, m] = [now.getFullYear(), now.getMonth() + 1]
  const query = useQuery({
    queryKey: ['next-payday', y, m],
    queryFn: async () => {
      const current = await api.get<Payday>(`/api/payday/${y}/${m}`)
      if (current.transfer_date > isoDate(now)) return current.transfer_date
      const [ny, nm] = addMonths(y, m, 1)
      return (await api.get<Payday>(`/api/payday/${ny}/${nm}`)).transfer_date
    },
  })
  return query.data
}

function PromoteSheet({ version, onClose }: { version: Version; onClose: () => void }) {
  const next = useNextPayday()
  const [date, setDate] = useState<string | null>(null)
  const effective = date ?? next ?? isoDate(today())
  const [choices, setChoices] = useState<Record<number, 'override' | 'live'>>({})
  const preview = useQuery({
    queryKey: ['promotion-preview', version.id, effective],
    queryFn: () =>
      api.post<PromotionPreview>(`/api/versions/${version.id}/promotion-preview`, {
        effective_date: effective,
      }),
  })
  const promote = useBudgetMutation(() =>
    api.post(`/api/versions/${version.id}/promote`, {
      effective_date: effective,
      resolutions: choices,
    }),
  )
  const p = preview.data
  const unresolved = (p?.conflicts ?? []).filter((c) => !choices[c.override_id])

  return (
    <div className="form">
      <Field
        label="Takes effect from"
        hint="Defaults to the next payday. Until then, live stays as it is."
      >
        {(id) => (
          <input id={id} type="date" value={effective} onChange={(e) => setDate(e.target.value)} />
        )}
      </Field>
      {preview.isPending && <Loading what="the preview" />}
      <ErrorText error={preview.error} />
      {p && (
        <>
          {p.conflicts.length > 0 && (
            <fieldset className="conflicts">
              <legend>
                These lines changed on live after this what-if changed them. Which should win?
              </legend>
              {p.conflicts.map((c) => (
                <div key={c.override_id} className="conflict">
                  <p className="conflict-label">{c.label}</p>
                  {(['override', 'live'] as const).map((choice) => (
                    <label key={choice} className="choice">
                      <input
                        type="radio"
                        name={`conflict-${c.override_id}`}
                        checked={choices[c.override_id] === choice}
                        onChange={() => setChoices({ ...choices, [c.override_id]: choice })}
                      />
                      {choice === 'override'
                        ? `This what-if’s ${describe(c.override.amount_pence ?? null, 'amount_pence')}`
                        : `Live’s current ${describe(c.live_now.amount_pence ?? null, 'amount_pence')}`}
                    </label>
                  ))}
                </div>
              ))}
            </fieldset>
          )}
          <SectionHeading title="What changes on live" />
          <ChangeList changes={p.changes} />
          {Object.keys(p.settings_changes).length > 0 && (
            <ul className="change-list">
              {Object.entries(p.settings_changes).map(([k, [a, b]]) => (
                <li key={k} data-change="modified">
                  <span className="change-kind">Setting</span>
                  <span className="change-label">{SETTING_NAMES[k] ?? k}</span>
                  <span className="change-detail">
                    {describe(a, k)} → {describe(b, k)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {Object.keys(p.income_changes).length > 0 && (
            <ul className="change-list">
              {Object.entries(p.income_changes).map(([person, [a, b]]) => (
                <li key={person} data-change="modified">
                  <span className="change-kind">Take-home</span>
                  <span className="change-label">
                    {person === 'person_1'
                      ? 'Person 1'
                      : person === 'person_2'
                        ? 'Person 2'
                        : person}
                  </span>
                  <span className="change-detail">
                    {describe(a, '_pence')} → {describe(b, '_pence')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <ErrorText error={promote.error} />
      <div className="form-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={!p || unresolved.length > 0 || promote.isPending}
          onClick={() => promote.mutate(undefined, { onSuccess: onClose })}
        >
          {promote.isPending ? 'Promoting…' : `Make live from ${formatLongDate(effective)}`}
        </button>
        {unresolved.length > 0 && (
          <span className="field-hint">
            Choose for {unresolved.length} line{unresolved.length === 1 ? '' : 's'} first.
          </span>
        )}
      </div>
    </div>
  )
}

function WhatIfForm({
  versions,
  editing,
  onClose,
}: {
  versions: Version[]
  editing: Version | null
  onClose: () => void
}) {
  const bases = versions.filter(
    (v) =>
      v.type === 'live' || (v.type === 'what_if' && v.status === 'draft' && v.id !== editing?.id),
  )
  const [name, setName] = useState(editing?.name ?? '')
  const [base, setBase] = useState(editing?.base_version_id ?? bases[0]?.id ?? 0)
  const [start, setStart] = useState(editing?.planned_start ?? '')
  const [description, setDescription] = useState(editing?.description ?? '')
  const save = useBudgetMutation(async () => {
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      planned_start: start || null,
    }
    if (!editing) return api.post('/api/versions', { ...body, base_version_id: base })
    await api.patch(`/api/versions/${editing.id}`, body)
    if (base !== editing.base_version_id)
      await api.post(`/api/versions/${editing.id}/rebase`, { base_version_id: base })
  })
  return (
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
            placeholder="Back to work"
          />
        )}
      </Field>
      <Field label="Built on" hint="It follows this budget except where you change it.">
        {(id) => (
          <select id={id} value={base} onChange={(e) => setBase(Number(e.target.value))}>
            {bases.map((v) => (
              <option key={v.id} value={v.id}>
                {v.type === 'live' ? `${v.name} (live)` : v.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field
        label="Planned start"
        hint="Optional. You’ll be reminded on the day; it never switches by itself."
      >
        {(id) => (
          <input id={id} type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        )}
      </Field>
      <Field label="Notes">
        {(id) => (
          <textarea
            id={id}
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        )}
      </Field>
      <ErrorText error={save.error} />
      <div className="form-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={!name.trim() || save.isPending}
        >
          {editing ? 'Save' : 'Create what-if'}
        </button>
      </div>
    </form>
  )
}

export default function Versions() {
  const versions = useVersions()
  const latest = useLatestPromotion()
  const [sheet, setSheet] = useState<{
    kind: 'new' | 'edit' | 'promote'
    version?: Version
  } | null>(null)
  const duplicate = useBudgetMutation((v: Version) =>
    api.post(`/api/versions/${v.id}/duplicate`, { name: `${v.name} (copy)` }),
  )
  const archive = useBudgetMutation((v: Version) => api.post(`/api/versions/${v.id}/archive`))
  const restore = useBudgetMutation((v: Version) => api.post(`/api/versions/${v.id}/restore`))
  const undo = useBudgetMutation((id: number) => api.post(`/api/promotions/${id}/undo`))
  // The last what-if archived, so it can be brought straight back.
  const [archived, setArchived] = useState<Version | null>(null)

  if (versions.isPending) return <Loading what="versions" />
  const all = versions.data ?? []
  const live = all.find((v) => v.type === 'live')
  const whatIfs = all.filter((v) => v.type === 'what_if' && v.status === 'draft')
  const periods = all
    .filter((v) => v.type === 'period')
    .sort((a, b) => (b.period_end ?? '').localeCompare(a.period_end ?? ''))
  const name = (id: number | null) => all.find((v) => v.id === id)?.name ?? 'live'

  return (
    <>
      <PageHeading
        title="Versions"
        lead="The live budget, what-ifs for the stages ahead, and the periods behind."
      >
        <button
          type="button"
          className="button button-primary"
          onClick={() => setSheet({ kind: 'new' })}
        >
          New what-if
        </button>
      </PageHeading>

      {latest.data?.undoable && (
        <Notice
          tone="info"
          action={
            <button
              type="button"
              className="button"
              disabled={undo.isPending}
              onClick={() => undo.mutate(latest.data!.id)}
            >
              Undo
            </button>
          }
        >
          <strong>{latest.data.what_if_name}</strong> was promoted to live from{' '}
          {formatLongDate(latest.data.effective_date)}. You can undo it until the first payday on
          it.
        </Notice>
      )}
      <ErrorText error={undo.error ?? duplicate.error ?? archive.error ?? restore.error} />
      {archived && (
        <Notice
          tone="ok"
          action={
            <button
              type="button"
              className="button"
              disabled={restore.isPending}
              onClick={() => restore.mutate(archived, { onSuccess: () => setArchived(null) })}
            >
              Undo
            </button>
          }
        >
          Archived <strong>{archived.name}</strong>.
        </Notice>
      )}

      {live && (
        <section className="register" aria-labelledby="live-title">
          <SectionHeading id="live-title" title="Live" />
          <ul className="version-list">
            <li>
              <div className="version-what">
                <Link to="/budget" className="version-name">
                  {live.name}
                </Link>
                <span className="version-meta">
                  {live.period_start ? `Since ${formatLongDate(live.period_start)}` : 'Live'}
                </span>
              </div>
            </li>
          </ul>
        </section>
      )}

      <section className="register" aria-labelledby="whatif-title">
        <SectionHeading
          id="whatif-title"
          title="What-ifs"
          aside={<span>{whatIfs.length === 1 ? '1 what-if' : `${whatIfs.length} what-ifs`}</span>}
        />
        {whatIfs.length === 0 ? (
          <p className="empty">No what-ifs yet. Start one for the next stage of life.</p>
        ) : (
          <ul className="version-list">
            {whatIfs.map((v) => (
              <li key={v.id}>
                <div className="version-what">
                  <Link to={`/budget?v=${v.id}`} className="version-name">
                    {v.name}
                  </Link>
                  <span className="version-meta">
                    Built on {name(v.base_version_id)}
                    {v.planned_start && ` · planned for ${formatLongDate(v.planned_start)}`}
                  </span>
                  {v.description && <span className="version-note">{v.description}</span>}
                </div>
                <div className="row-actions">
                  <button
                    type="button"
                    className="button button-primary"
                    onClick={() => setSheet({ kind: 'promote', version: v })}
                  >
                    Promote…
                  </button>
                  <button
                    type="button"
                    className="button"
                    onClick={() => setSheet({ kind: 'edit', version: v })}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="button button-quiet"
                    disabled={duplicate.isPending}
                    onClick={() => duplicate.mutate(v)}
                  >
                    Duplicate
                  </button>
                  <button
                    type="button"
                    className="button button-quiet"
                    disabled={archive.isPending}
                    onClick={() => archive.mutate(v, { onSuccess: () => setArchived(v) })}
                  >
                    Archive
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="register" aria-labelledby="period-title">
        <SectionHeading id="period-title" title="Periods" aside={<span>Read-only</span>} />
        {periods.length === 0 ? (
          <p className="empty">
            Nothing archived yet. When a what-if is promoted, the old live budget is kept here.
          </p>
        ) : (
          <ul className="version-list">
            {periods.map((v) => (
              <li key={v.id}>
                <div className="version-what">
                  <Link to={`/budget?v=${v.id}`} className="version-name">
                    {v.name}
                  </Link>
                  <span className="version-meta">
                    {v.period_start && formatLongDate(v.period_start)} to{' '}
                    {v.period_end && formatLongDate(v.period_end)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Sheet
        open={sheet !== null}
        onOpenChange={(open) => !open && setSheet(null)}
        title={
          sheet?.kind === 'promote'
            ? `Promote “${sheet.version?.name}”`
            : sheet?.kind === 'edit'
              ? 'Edit what-if'
              : 'New what-if'
        }
        description={
          sheet?.kind === 'promote'
            ? 'It takes the live budget as it is now and applies only what this what-if changed. The current live budget is kept as a read-only period.'
            : undefined
        }
      >
        {sheet?.kind === 'promote' && sheet.version && (
          <PromoteSheet version={sheet.version} onClose={() => setSheet(null)} />
        )}
        {(sheet?.kind === 'new' || sheet?.kind === 'edit') && (
          <WhatIfForm
            versions={all}
            editing={sheet.version ?? null}
            onClose={() => setSheet(null)}
          />
        )}
      </Sheet>
    </>
  )
}
