import { useQuery } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'
import { personName } from '../accounts.ts'
import {
  api,
  useAuthConfig,
  useBudgetMutation,
  useHa,
  useIncomes,
  useSummary,
  useVersions,
  type SessionInfo,
  type Version,
} from '../api.ts'
import { useAuth } from '../auth.tsx'
import { formatShortMonthYear, formatTimestamp } from '../dates.ts'
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

type VersionIncome = {
  person: string
  net_monthly_pence: number | null
  as_of_date: string
  employer: string | null
}

function PasswordForm() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [done, setDone] = useState(false)
  const change = useBudgetMutation(() =>
    api.post('/api/auth/password', { current_password: current, new_password: next }),
  )
  const mismatch = again !== '' && next !== again
  function submit(e: FormEvent) {
    e.preventDefault()
    setDone(false)
    change.mutate(undefined, {
      onSuccess: () => {
        setCurrent('')
        setNext('')
        setAgain('')
        setDone(true)
      },
    })
  }
  return (
    <form className="form form-narrow" onSubmit={submit}>
      <Field label="Current password">
        {(id) => (
          <input
            id={id}
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        )}
      </Field>
      <Field label="New password" hint="At least 12 characters. A few words together is fine.">
        {(id) => (
          <input
            id={id}
            type="password"
            autoComplete="new-password"
            minLength={12}
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
          />
        )}
      </Field>
      <Field label="New password again">
        {(id) => (
          <input
            id={id}
            type="password"
            autoComplete="new-password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            aria-invalid={mismatch}
            required
          />
        )}
      </Field>
      {mismatch && <p className="error-text">The new passwords don’t match.</p>}
      <ErrorText error={change.error} />
      {done && (
        <p className="ok-text" role="status">
          Password changed. Your other sessions have been signed out.
        </p>
      )}
      <div className="form-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={mismatch || next.length < 12 || change.isPending}
        >
          Change password
        </button>
      </div>
    </form>
  )
}

function Sessions() {
  const sessions = useQuery({
    queryKey: ['sessions'],
    queryFn: () => api.get<SessionInfo[]>('/api/auth/sessions'),
  })
  const { signOut } = useAuth()
  const revoke = useBudgetMutation((id: number) => api.del(`/api/auth/sessions/${id}`))
  const revokeOthers = useBudgetMutation(() => api.post('/api/auth/sessions/revoke-others'))
  if (sessions.isPending) return <Loading what="sessions" />
  const others = (sessions.data ?? []).filter((s) => !s.current).length
  return (
    <>
      <ErrorText error={revoke.error ?? revokeOthers.error} />
      <div className="row-actions session-actions">
        {others > 0 && (
          <button
            type="button"
            className="button"
            disabled={revokeOthers.isPending}
            onClick={() =>
              revokeOthers.mutate(undefined, { onSuccess: () => void sessions.refetch() })
            }
          >
            Sign out everywhere else ({others})
          </button>
        )}
        <button type="button" className="button button-quiet" onClick={() => void signOut()}>
          Sign out of this browser
        </button>
      </div>
      <ul className="session-list">
        {(sessions.data ?? []).map((s) => (
          <li key={s.id}>
            <div>
              <span className="session-what">
                {s.current ? 'This browser' : describeAgent(s.user_agent)}
                {s.method === 'oidc' ? ' · single sign-on' : ''}
              </span>
              <span className="session-meta">
                Signed in {formatTimestamp(s.created_at)} · last seen{' '}
                {formatTimestamp(s.last_seen_at)}
                {s.ip ? ` · ${s.ip}` : ''}
              </span>
            </div>
            {!s.current && (
              <button
                type="button"
                className="button"
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(s.id, { onSuccess: () => void sessions.refetch() })}
              >
                Sign out
              </button>
            )}
          </li>
        ))}
      </ul>
    </>
  )
}

function describeAgent(agent: string | null): string {
  if (!agent) return 'Another browser'
  const os = /iPhone|iPad/.test(agent)
    ? 'iPhone'
    : /Android/.test(agent)
      ? 'Android'
      : /Mac OS X/.test(agent)
        ? 'Mac'
        : /Windows/.test(agent)
          ? 'Windows'
          : 'Another device'
  const browser = /Edg\//.test(agent)
    ? 'Edge'
    : /Chrome\//.test(agent)
      ? 'Chrome'
      : /Firefox\//.test(agent)
        ? 'Firefox'
        : /Safari\//.test(agent)
          ? 'Safari'
          : 'a browser'
  return `${browser} on ${os}`
}

function SingleSignOn() {
  const { me, refresh } = useAuth()
  const config = useAuthConfig()
  const [params] = useSearchParams()
  const link = useBudgetMutation(() => api.post<{ redirect: string }>('/api/auth/oidc/link'))
  const unlink = useBudgetMutation(() => api.del('/api/auth/oidc/link'))
  const oidc = config.data?.oidc
  if (!oidc?.enabled || !me) return null
  return (
    <section className="register" aria-labelledby="sso-title">
      <SectionHeading id="sso-title" title={`Sign in with ${oidc.label}`} />
      {params.get('linked') && (
        <Notice tone="ok">Linked. You can now sign in with {oidc.label}.</Notice>
      )}
      <ErrorText error={link.error ?? unlink.error} />
      {me.oidc_linked ? (
        <div className="row-actions">
          <p>Your {oidc.label} account is linked.</p>
          <button
            type="button"
            className="button"
            onClick={() => unlink.mutate(undefined, { onSuccess: () => void refresh() })}
          >
            Unlink
          </button>
        </div>
      ) : (
        <div className="row-actions">
          <p>Link your {oidc.label} account to sign in with it as well as your password.</p>
          <button
            type="button"
            className="button"
            onClick={() =>
              link.mutate(undefined, { onSuccess: (r) => window.location.assign(r.redirect) })
            }
          >
            Link {oidc.label}
          </button>
        </div>
      )}
    </section>
  )
}

const BASIS_NAMES: Record<string, string> = {
  take_home: 'Take-home pay',
  gross: 'Gross pay',
  surplus: 'What’s left over',
  fixed_pct: 'Fixed split',
}

/** "60" or "60.5" -> 600 / 605 tenths of a percent; anything else -> null. */
function tenths(percent: string): number | null {
  const m = /^(\d{1,3})(?:\.(\d))?$/.exec(percent.trim())
  if (!m) return null
  const value = Number(m[1]) * 10 + Number(m[2] ?? 0)
  return value <= 1000 ? value : null
}

/** A stored share ("600/1000", "0.6") shown as a percentage for editing. */
function sharePercent(share: string | undefined): string {
  if (!share) return '50'
  const [a, b] = share.split('/').map(Number)
  const fraction = b ? a! / b : Number(share)
  return String(Math.round(fraction * 1000) / 10)
}

function BudgetSettings({ live }: { live: Version }) {
  const summary = useSummary(live.id)
  const [basis, setBasis] = useState<string | null>(null)
  const [rounding, setRounding] = useState<string | null>(null)
  const [buffer, setBuffer] = useState<string | null>(null)
  const [share, setShare] = useState<string | null>(null)
  const save = useBudgetMutation(() =>
    api.patch(`/api/versions/${live.id}/settings`, {
      ...(basis !== null ? { split_basis: basis } : {}),
      ...(share !== null && tenths(share) !== null
        ? {
            fixed_shares: {
              person_1: `${tenths(share)}/1000`,
              person_2: `${1000 - tenths(share)!}/1000`,
            },
          }
        : {}),
      ...(rounding !== null ? { rounding_step_pence: parsePounds(rounding) } : {}),
      ...(buffer !== null ? { buffer_pence: parsePounds(buffer) } : {}),
    }),
  )
  if (!summary.data) return <Loading what="settings" />
  const s = summary.data
  const roundingOk = rounding === null || (parsePounds(rounding) ?? 0) > 0
  const bufferOk = buffer === null || parsePounds(buffer) !== null
  const shownBasis = basis ?? s.split_basis
  const currentShare = sharePercent(s.settings.fixed_shares?.person_1)
  const shareOk = shownBasis !== 'fixed_pct' || tenths(share ?? currentShare) !== null
  return (
    <form
      className="form form-narrow"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(undefined, {
          onSuccess: () => {
            setBasis(null)
            setRounding(null)
            setBuffer(null)
            setShare(null)
          },
        })
      }}
    >
      <Field
        label="Split the joint account by"
        hint="What-ifs can set their own; they follow live unless they do."
      >
        {(id) => (
          <select id={id} value={shownBasis} onChange={(e) => setBasis(e.target.value)}>
            <option value="take_home">Take-home pay</option>
            <option value="gross">Gross pay (with benefits allowance)</option>
            <option value="surplus">What’s left over</option>
            <option value="fixed_pct">A fixed split</option>
          </select>
        )}
      </Field>
      {shownBasis === 'fixed_pct' && (
        <Field label="Person 1’s share (%)" hint="Person 2 pays the rest. Up to one decimal place.">
          {(id) => (
            <input
              id={id}
              inputMode="decimal"
              value={share ?? currentShare}
              onChange={(e) => setShare(e.target.value)}
              aria-invalid={!shareOk}
            />
          )}
        </Field>
      )}
      <div className="split-options">
        <p>What each of you would transfer into Joint Billing, by each way of splitting:</p>
        <dl>
          {Object.entries(s.split_options).map(([b, people]) => (
            <div key={b}>
              <dt>{BASIS_NAMES[b] ?? b}</dt>
              <dd>
                {Object.entries(people)
                  .map(([p, pence]) => `${personName(p)} ${formatPence(pence)}`)
                  .join(' · ')}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="field-row">
        <Field label="Round transfers up to the next">
          {(id) => (
            <MoneyInput
              id={id}
              value={rounding ?? penceToInput(s.settings.rounding_step_pence)}
              onChange={setRounding}
            />
          )}
        </Field>
        <Field label="Then add a buffer of">
          {(id) => (
            <MoneyInput
              id={id}
              value={buffer ?? penceToInput(s.settings.buffer_pence)}
              onChange={setBuffer}
            />
          )}
        </Field>
      </div>
      <ErrorText error={save.error} />
      <div className="form-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={
            (basis === null && rounding === null && buffer === null && share === null) ||
            !roundingOk ||
            !shareOk ||
            !bufferOk ||
            save.isPending
          }
        >
          Save
        </button>
      </div>
    </form>
  )
}

function TakeHome({ versions }: { versions: Version[] }) {
  const incomes = useIncomes()
  const shown = versions.filter(
    (v) => v.type === 'live' || (v.type === 'what_if' && v.status === 'draft'),
  )
  const perVersion = useQuery({
    queryKey: ['version-incomes', shown.map((v) => v.id)],
    queryFn: async () =>
      Object.fromEntries(
        await Promise.all(
          shown.map(
            async (v) =>
              [v.id, await api.get<VersionIncome[]>(`/api/versions/${v.id}/incomes`)] as const,
          ),
        ),
      ),
  })
  const [edit, setEdit] = useState<{ version: number; person: string; value: string } | null>(null)
  const save = useBudgetMutation(
    ({ version, person, pence }: { version: number; person: string; pence: number }) =>
      api.put(`/api/versions/${version}/incomes/${person}`, { net_monthly_pence: pence }),
  )
  if (perVersion.isPending || incomes.isPending) return <Loading what="pay" />
  const people = (incomes.data ?? []).map((i) => i.person)
  return (
    <>
      <p className="section-lead">
        Monthly take-home pay as each budget sees it. Salaries are from{' '}
        {[...new Set((incomes.data ?? []).map((i) => formatShortMonthYear(i.as_of_date)))].join(
          ', ',
        )}
        . To work take-home out from gross with this year’s tax rates, see{' '}
        <Link to="/plan/take-home">Plan, Take-home</Link>.
      </p>
      <ErrorText error={save.error} />
      <div className="table-wrap">
        <table className="income-table">
          <thead>
            <tr>
              <th scope="col">Budget</th>
              {people.map((p) => (
                <th key={p} scope="col" className="num">
                  {personName(p)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((v) => (
              <tr key={v.id}>
                <th scope="row">{v.type === 'live' ? `${v.name} (live)` : v.name}</th>
                {people.map((p) => {
                  const current =
                    perVersion.data?.[v.id]?.find((i) => i.person === p)?.net_monthly_pence ?? null
                  const editing = edit?.version === v.id && edit.person === p
                  return (
                    <td key={p} className="num">
                      {editing ? (
                        <form
                          className="inline-edit"
                          onSubmit={(e) => {
                            e.preventDefault()
                            const pence = parsePounds(edit.value)
                            if (pence === null) return
                            save.mutate(
                              { version: v.id, person: p, pence },
                              {
                                onSuccess: () => {
                                  setEdit(null)
                                  void perVersion.refetch()
                                },
                              },
                            )
                          }}
                        >
                          <MoneyInput
                            label={`${personName(p)}’s take-home in ${v.name}`}
                            value={edit.value}
                            autoFocus
                            onChange={(value) => setEdit({ ...edit, value })}
                          />
                          <button className="button" type="submit">
                            Save
                          </button>
                        </form>
                      ) : (
                        <button
                          type="button"
                          className="cell-button"
                          onClick={() =>
                            setEdit({ version: v.id, person: p, value: penceToInput(current) })
                          }
                          aria-label={`Change ${p}’s take-home in ${v.name}`}
                        >
                          {current === null ? 'Not set' : <Money pence={current} />}
                        </button>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="button"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1600)
        })
      }}
    >
      <span aria-live="polite">{copied ? 'Copied' : label}</span>
    </button>
  )
}

function HomeAssistant() {
  const ha = useHa()
  const [label, setLabel] = useState('Home Assistant')
  const [made, setMade] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<number | null>(null)
  const make = useBudgetMutation(() =>
    api.post<{ id: number; token: string }>('/api/ha/tokens', { label: label.trim() }),
  )
  const revoke = useBudgetMutation((id: number) => api.del(`/api/ha/tokens/${id}`))
  const test = useBudgetMutation(() => api.post('/api/ha/test'))
  if (ha.isPending) return <Loading what="Home Assistant" />
  if (ha.isError) return <ErrorText error={ha.error} />
  const s = ha.data
  const yaml = `rest:
  - resource: ${s.summary_url}
    headers:
      Authorization: "Bearer ${made ?? '<your token>'}"
    scan_interval: 900
    sensor:
      - name: Bawbee inbox
        value_template: "{{ value_json.inbox.count }}"
      - name: Bawbee joint low point
        value_template: "{{ value_json.joint_low_point.pence / 100 }}"
        unit_of_measurement: GBP`
  return (
    <>
      <p className="section-lead">
        {s.webhook ? (
          <>
            Home Assistant hears from Bawbee. After each sync, anything new in the inbox goes to it
            in one notification. A bank link ending goes to its owner, and Joint Billing projected
            under {formatPence(s.low_balance_pence)} before payday gets its own.
          </>
        ) : (
          <>
            Home Assistant isn’t hearing from Bawbee yet. That’s switched on where Bawbee is
            installed.
          </>
        )}
      </p>
      {s.webhook ? (
        <div className="row-actions">
          <button
            type="button"
            className="button"
            disabled={test.isPending}
            onClick={() => test.mutate(undefined)}
          >
            {test.isPending ? 'Sending…' : 'Send a test notification'}
          </button>
          <span role="status" className="ok-text">
            {test.isSuccess ? 'Sent. Check Home Assistant.' : ''}
          </span>
        </div>
      ) : (
        <details className="setup-details">
          <summary>Setup details</summary>
          <p>
            Set <code>BAWBEE_HA_WEBHOOK_URL</code> on the server to a Home Assistant webhook, then
            restart. The steps are in <code>docs/home-assistant.md</code>.
          </p>
        </details>
      )}
      <ErrorText error={test.error} />

      <h3 className="settings-sub">A sensor for Home Assistant</h3>
      <p className="section-lead">
        Home Assistant can read a summary for a dashboard: the inbox count, payday, the month’s
        surplus, Joint Billing’s low point and the bank links. It needs a token made here, which can
        read that summary and nothing else.
      </p>
      {s.tokens.length > 0 && (
        <ul className="session-list">
          {s.tokens.map((tok) => (
            <li key={tok.id}>
              <div>
                <span className="session-what">{tok.label}</span>
                <span className="session-meta">
                  Made {formatTimestamp(tok.created_at)} ·{' '}
                  {tok.last_used_at
                    ? `last read ${formatTimestamp(tok.last_used_at)}`
                    : 'not used yet'}
                </span>
              </div>
              {revoking === tok.id ? (
                <div className="row-actions" role="group" aria-label={`Revoke ${tok.label}?`}>
                  <span className="session-meta">Home Assistant stops reading the summary.</span>
                  <button
                    type="button"
                    className="button button-danger"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(tok.id, { onSuccess: () => setRevoking(null) })}
                  >
                    Revoke it
                  </button>
                  <button
                    type="button"
                    className="button button-quiet"
                    onClick={() => setRevoking(null)}
                  >
                    Keep it
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => setRevoking(tok.id)}
                >
                  Revoke<span className="visually-hidden">: {tok.label}</span>
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {made ? (
        <div className="token-once">
          <Notice tone="ok" urgent>
            The token is ready. Copy it now: it isn’t shown again.
          </Notice>
          <Field label="Token">
            {(id) => (
              <input
                id={id}
                readOnly
                value={made}
                spellCheck={false}
                onFocus={(e) => e.target.select()}
              />
            )}
          </Field>
          <div className="row-actions">
            <CopyField label="Copy token" value={made} />
            <CopyField label="Copy configuration" value={yaml} />
            <button type="button" className="button button-quiet" onClick={() => setMade(null)}>
              I’ve copied it
            </button>
          </div>
          <details className="setup-details">
            <summary>The configuration for Home Assistant</summary>
            <pre className="ha-yaml">{yaml}</pre>
          </details>
        </div>
      ) : (
        <form
          className="form form-narrow"
          onSubmit={(e) => {
            e.preventDefault()
            make.mutate(undefined, { onSuccess: (r) => setMade(r.token) })
          }}
        >
          <Field label="What it’s for" hint="e.g. Living room dashboard">
            {(id, hintId) => (
              <input
                id={id}
                value={label}
                aria-describedby={hintId}
                onChange={(e) => setLabel(e.target.value)}
                required
              />
            )}
          </Field>
          <ErrorText error={make.error ?? revoke.error} />
          <div className="form-actions">
            <button className="button" type="submit" disabled={!label.trim() || make.isPending}>
              {make.isPending ? 'Making…' : 'Make a token'}
            </button>
          </div>
        </form>
      )}
    </>
  )
}

export default function Settings() {
  const { me } = useAuth()
  const { hash } = useLocation()
  const versions = useVersions()
  // Links like /settings#take-home land on that section once it has rendered.
  useEffect(() => {
    if (hash && versions.data) document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [hash, versions.data])
  const live = versions.data?.find((v) => v.type === 'live')
  return (
    <>
      <PageHeading
        title="Settings"
        lead={me ? `Signed in as ${me.display_name} (${me.username}).` : undefined}
      />

      {me?.has_password && (
        <section className="register" aria-labelledby="password-title">
          <SectionHeading id="password-title" title="Password" />
          <PasswordForm />
        </section>
      )}

      <section className="register" aria-labelledby="sessions-title">
        <SectionHeading id="sessions-title" title="Where you’re signed in" />
        <Sessions />
      </section>

      <SingleSignOn />

      {live && (
        <section className="register" aria-labelledby="budget-settings-title">
          <SectionHeading
            id="budget-settings-title"
            title="Joint account"
            aside={<span>Live budget</span>}
          />
          <BudgetSettings live={live} />
        </section>
      )}

      {versions.data && (
        <section id="take-home" className="register" aria-labelledby="pay-title">
          <SectionHeading id="pay-title" title="Take-home pay" />
          <TakeHome versions={versions.data} />
        </section>
      )}

      <section id="home-assistant" className="register" aria-labelledby="ha-title">
        <SectionHeading id="ha-title" title="Home Assistant" />
        <HomeAssistant />
      </section>

      <section className="register" aria-labelledby="export-title">
        <SectionHeading id="export-title" title="Export" />
        <p className="section-lead">
          Download everything to keep a copy, or open the budget in Excel.
        </p>
        <div className="row-actions">
          <a className="button" href="/api/export/backup.json" download>
            JSON backup
          </a>
          <a className="button" href="/api/export/budget.xlsx" download>
            Excel workbook
          </a>
        </div>
      </section>
    </>
  )
}
