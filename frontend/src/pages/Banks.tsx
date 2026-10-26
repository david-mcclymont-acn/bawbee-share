import { useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { inkFor, personName, shortName } from '../accounts.ts'
import {
  api,
  ApiError,
  useAccounts,
  useBanks,
  useBudgetMutation,
  useInstitutions,
  type Account,
  type BankConnection,
  type BanksOverview,
} from '../api.ts'
import { formatDay, formatLongDate, formatTimestamp, parseDate, today } from '../dates.ts'
import { Rosette } from '../marks.tsx'
import {
  ErrorText,
  Field,
  Loading,
  Money,
  Notice,
  PageHeading,
  SectionHeading,
  Sheet,
} from '../ui.tsx'

const PROVIDER: Record<BankConnection['provider'], string> = {
  enable_banking: 'Through Enable Banking',
  starling_pat: 'Starling token',
  monzo: 'Monzo’s own API',
  demo: 'Made-up data',
}

// What people call their bank, for searching the list the banks give themselves.
const ALIASES: Record<string, string> = {
  rbs: 'royal bank of scotland',
  natwest: 'national westminster',
  bos: 'bank of scotland',
}

function daysUntil(iso: string): number {
  const end = parseDate(iso.slice(0, 10))
  return Math.round((end.getTime() - today().getTime()) / 86_400_000)
}

function clock(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' }).format(
    new Date(iso),
  )
}

/** A change that needs the password typed recently: true once the server said so (428). */
function isReauth(e: unknown): boolean {
  return e instanceof ApiError && e.status === 428
}

// ---- the password, as the first step of a sheet that needs it --------------------------

function PasswordStep({ purpose, onDone }: { purpose: string; onDone: () => void }) {
  const [password, setPassword] = useState('')
  const confirm = useBudgetMutation(() => api.post('/api/auth/confirm', { password }))
  return (
    <form
      className="form password-step"
      onSubmit={(e) => {
        e.preventDefault()
        confirm.mutate(undefined, { onSuccess: onDone })
      }}
    >
      <p>
        Type your password to {purpose}. It’s asked for when you signed in more than 12 hours ago.
      </p>
      <Field label="Your password">
        {(id) => (
          <input
            id={id}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            required
          />
        )}
      </Field>
      <ErrorText error={confirm.error} />
      <div className="form-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={!password || confirm.isPending}
        >
          {confirm.isPending ? 'Checking…' : 'Continue'}
        </button>
      </div>
    </form>
  )
}

/** Shows the password step first when the session needs it, then the sheet's own content. */
function Guarded({
  confirmed,
  purpose,
  children,
}: {
  confirmed: boolean
  purpose: string
  children: (onReauth: () => void) => ReactNode
}) {
  const [needed, setNeeded] = useState(!confirmed)
  if (needed) return <PasswordStep purpose={purpose} onDone={() => setNeeded(false)} />
  return <>{children(() => setNeeded(true))}</>
}

// ---- choosing a bank -----------------------------------------------------------------------

/** Why a search found nothing, when it's this server's setup rather than the bank's name. */
function notSetUp(q: string, setup: BanksOverview['setup']): string | null {
  if (q.includes('monzo')) {
    return setup.monzo ? `Monzo isn’t set up on this server: ${setup.monzo}` : null
  }
  return setup.enable_banking
    ? `Banks other than Monzo link through Enable Banking, which isn’t set up on this server: ${setup.enable_banking}`
    : null
}

function BankChooser({
  relink,
  setup,
  onReauth,
}: {
  relink: BankConnection | null
  setup: BanksOverview['setup']
  onReauth: () => void
}) {
  const institutions = useInstitutions(!relink)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const q = query.trim().toLowerCase()
  const list = (institutions.data ?? []).filter((b) => {
    const name = b.name.toLowerCase()
    return name.includes(q) || (ALIASES[q] !== undefined && name.includes(ALIASES[q]))
  })
  const start = (name: string, country: string) => {
    setBusy(name)
    setError(null)
    api
      .post<{ url: string }>('/api/banks/link', {
        bank: name,
        country,
        relink_connection_id: relink?.id ?? null,
      })
      .then((r) => {
        // To the bank's own sign-in; it sends the browser back to /banks.
        window.location.assign(r.url)
      })
      .catch((e: unknown) => {
        setBusy(null)
        if (isReauth(e)) onReauth()
        else setError(e)
      })
  }

  if (relink) {
    return (
      <div className="form">
        <p>
          You’ll sign in at {relink.bank} and agree to share your accounts again, read-only.
          Everything already brought in stays, and the same accounts carry on.
        </p>
        <ErrorText error={error} />
        <div className="form-actions">
          <button
            type="button"
            className="button button-primary"
            disabled={busy !== null}
            onClick={() => start(relink.bank, 'GB')}
          >
            {busy ? 'Opening your bank…' : `Continue to ${relink.bank}`}
          </button>
        </div>
      </div>
    )
  }
  return (
    <div className="form">
      <Field label="Find your bank" hint="Type its name, e.g. Monzo or RBS.">
        {(id, hintId) => (
          <input
            id={id}
            type="search"
            value={query}
            aria-describedby={hintId}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
        )}
      </Field>
      {institutions.isPending && <Loading what="banks" />}
      <ErrorText error={institutions.error ?? error} />
      <ul className="bank-list">
        {list.slice(0, 30).map((b) => (
          <li key={`${b.name}-${b.country}`}>
            <button
              type="button"
              className="bank-choice"
              disabled={busy !== null}
              onClick={() => start(b.name, b.country)}
            >
              <span className="bank-choice-name">{b.name}</span>
              <span className="bank-choice-note">
                {busy === b.name
                  ? 'Opening your bank…'
                  : b.provider === 'monzo'
                    ? 'Read-only · you approve it in the Monzo app'
                    : b.max_consent_days
                      ? `Read-only, for up to ${b.max_consent_days} days, then it asks again`
                      : 'Read-only'}
                {b.beta && ' · beta'}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {list.length > 30 && (
        <p className="field-hint">{list.length - 30} more: type more of the name.</p>
      )}
      {institutions.data && list.length === 0 && (
        <p className="field-hint">
          {notSetUp(q, setup) ?? 'No bank by that name. Starling? Add a Starling token instead.'}{' '}
          {notSetUp(q, setup) && 'See docs/providers.md, or run bawbee providers on the server.'}
        </p>
      )}
    </div>
  )
}

function StarlingToken({
  replace,
  onReauth,
  onLinked,
}: {
  replace: BankConnection | null
  onReauth: () => void
  onLinked: (c: BankConnection) => void
}) {
  const [token, setToken] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault()
        setBusy(true)
        setError(null)
        api
          .post<BankConnection>('/api/banks/starling', {
            token,
            replace_connection_id: replace?.id ?? null,
          })
          .then(onLinked)
          .catch((err: unknown) => {
            if (isReauth(err)) onReauth()
            else setError(err)
          })
          .finally(() => setBusy(false))
      }}
    >
      <p className="field-hint">
        In the Starling developer portal, make a personal access token with <strong>read</strong>{' '}
        scopes only: account:read, account-identifier:read, balance:read and transaction:read.
        Nothing here can move money.
      </p>
      <Field label="Personal access token">
        {(id) => (
          <input
            id={id}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            required
          />
        )}
      </Field>
      <ErrorText error={error} />
      <div className="form-actions">
        <button className="button button-primary" type="submit" disabled={!token.trim() || busy}>
          {busy ? 'Checking the token…' : replace ? 'Replace the token' : 'Add the token'}
        </button>
      </div>
    </form>
  )
}

// ---- which accounts a link brings in ---------------------------------------------------------

function Mapping({
  connection,
  accounts,
  taken,
  onDone,
}: {
  connection: BankConnection
  accounts: Account[]
  taken: Set<string>
  onDone: (c: BankConnection) => void
}) {
  const [choice, setChoice] = useState<Record<string, string>>(() =>
    Object.fromEntries(connection.accounts.map((a) => [a.uid, a.account ?? ''])),
  )
  const save = useBudgetMutation(() =>
    api.put<BankConnection>(`/api/banks/${connection.id}/accounts`, {
      accounts: Object.fromEntries(
        connection.accounts.filter((a) => !a.claimed_by).map((a) => [a.uid, choice[a.uid] || null]),
      ),
    }),
  )
  const chosen = Object.values(choice).filter(Boolean)
  const doubled = chosen.length !== new Set(chosen).size
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(undefined, { onSuccess: onDone })
      }}
    >
      {connection.accounts.map((a) => (
        <Field
          key={a.uid}
          label={`${a.name}${a.detail ? ` · ${a.detail}` : ''}`}
          hint={
            a.claimed_by
              ? `${a.claimed_by} has already linked this one. A joint account is linked once.`
              : undefined
          }
        >
          {(id, hintId) => (
            <select
              id={id}
              value={choice[a.uid] ?? ''}
              disabled={Boolean(a.claimed_by)}
              aria-describedby={hintId}
              onChange={(e) => setChoice({ ...choice, [a.uid]: e.target.value })}
            >
              <option value="">Don’t bring it in</option>
              {accounts
                .filter((acct) => acct.key === a.account || !taken.has(acct.key))
                .map((acct) => (
                  <option key={acct.key} value={acct.key}>
                    {shortName(acct.name)}
                    {acct.owner ? ` (${personName(acct.owner)})` : ' (Joint)'}
                  </option>
                ))}
            </select>
          )}
        </Field>
      ))}
      {connection.provider === 'monzo' && (
        <p className="field-hint">
          Save straight away: Monzo shares the full history only in the first five minutes after you
          approve. After that it’s the last 90 days.
        </p>
      )}
      <p className="field-hint">
        An account with CSV imports carries on from the day after its last one, so nothing comes in
        twice.
      </p>
      {doubled && (
        <p className="error-text">Each of our accounts can come from one bank account only.</p>
      )}
      <ErrorText error={save.error} />
      <div className="form-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={doubled || save.isPending}
        >
          {save.isPending ? 'Bringing them in…' : 'Save and sync'}
        </button>
      </div>
    </form>
  )
}

// ---- Monzo: approved in the Monzo app before anything can be read -----------------------------

function Approve({
  connection,
  onMap,
  onDone,
}: {
  connection: BankConnection
  onMap: (c: BankConnection) => void
  onDone: (text: string) => void
}) {
  const go = useBudgetMutation(() =>
    api.post<{ connection: BankConnection; map: boolean }>(`/api/banks/${connection.id}/continue`),
  )
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault()
        go.mutate(undefined, {
          onSuccess: (r) =>
            r.map
              ? onMap(r.connection)
              : onDone(`${r.connection.bank} re-linked. It carries on with the same accounts.`),
        })
      }}
    >
      <ol className="approve-steps">
        <li>Open the Monzo app. It shows a request from A Wee Bawbee.</li>
        <li>Allow access. It’s read-only: nothing here can move money.</li>
        <li>Come back and press Continue.</li>
      </ol>
      <ErrorText error={go.error} />
      <div className="form-actions">
        <button className="button button-primary" type="submit" disabled={go.isPending}>
          {go.isPending ? 'Checking with Monzo…' : 'Continue'}
        </button>
      </div>
    </form>
  )
}

// ---- a linked bank, as a register strip ------------------------------------------------------

function ConnectionStrip({
  connection,
  accounts,
  onMap,
  onApprove,
  onRelink,
  onDisconnect,
}: {
  connection: BankConnection
  accounts: Map<string, Account>
  onMap: () => void
  onApprove: () => void
  onRelink: () => void
  onDisconnect: () => void
}) {
  const pending = connection.status === 'pending'
  const sync = useBudgetMutation(() => api.post<BankConnection>(`/api/banks/${connection.id}/sync`))
  const ends = connection.consent_expires_at
  const left = ends ? daysUntil(ends) : null
  const soon = connection.status === 'expired' || (left !== null && left <= 14)
  const fed = connection.accounts.filter((a) => a.account)
  const waiting = connection.next_sync_now_at
  const status =
    connection.status === 'expired'
      ? { tone: 'danger', text: 'The bank consent has ended. It needs re-linking to carry on.' }
      : pending
        ? {
            tone: 'warn',
            text: connection.can_manage
              ? 'Waiting for you to allow access in the Monzo app.'
              : `Waiting for ${connection.owner} to allow access in the Monzo app.`,
          }
        : connection.last_error
          ? { tone: 'warn', text: connection.last_error }
          : null
  return (
    <li className="bank-strip">
      <div className="bank-head">
        <div>
          <h3 className="bank-name">{connection.bank}</h3>
          <p className="bank-meta">
            {PROVIDER[connection.provider]} · linked by {connection.owner}
            {connection.last_sync_at && (
              <>
                {' '}
                · synced {formatTimestamp(connection.last_sync_at)}
                {connection.last_run?.status === 'ok' && connection.last_run.new > 0
                  ? `, ${connection.last_run.new} new`
                  : connection.last_run?.status === 'ok'
                    ? ', nothing new'
                    : ''}
              </>
            )}
          </p>
          {ends && left !== null && (
            <p className="bank-consent" data-soon={soon || undefined}>
              {left < 0
                ? `Consent ended ${formatDay(ends)}`
                : left === 0
                  ? 'Consent ends today'
                  : `Consent ends ${formatDay(ends)}, in ${left} ${left === 1 ? 'day' : 'days'}`}
            </p>
          )}
        </div>
        <div className="row-actions">
          {connection.can_manage && soon && (
            <button type="button" className="button button-primary" onClick={onRelink}>
              Re-link<span className="visually-hidden">: {connection.bank}</span>
            </button>
          )}
          {connection.can_manage && pending && (
            <button type="button" className="button button-primary" onClick={onApprove}>
              Continue<span className="visually-hidden">: {connection.bank}</span>
            </button>
          )}
          <button
            type="button"
            className="button"
            disabled={
              sync.isPending || connection.status === 'expired' || pending || waiting !== null
            }
            onClick={() => sync.mutate(undefined)}
          >
            {sync.isPending ? 'Syncing…' : 'Sync now'}
            <span className="visually-hidden">: {connection.bank}</span>
          </button>
          {waiting && <span className="bank-sync-wait">Again after {clock(waiting)}</span>}
        </div>
      </div>
      {status && <Notice tone={status.tone as 'warn' | 'danger'}>{status.text}</Notice>}
      <ErrorText error={sync.error} />
      <ul className="bank-accounts">
        {connection.accounts.map((a) => {
          const ours = a.account ? accounts.get(a.account) : undefined
          const ink = inkFor(ours)
          return (
            <li key={a.uid} data-ink={ink} data-off={!ours || undefined}>
              <span className="bank-mark" aria-hidden="true">
                {ink && <Rosette ink={ink} size={32} />}
              </span>
              <span className="bank-account">
                <span className="bank-account-ours">
                  {ours ? shortName(ours.name) : 'Not brought in'}
                </span>
                <span className="bank-account-theirs">
                  {a.name}
                  {a.detail ? ` · ${a.detail}` : ''}
                  {a.claimed_by ? ` · linked by ${a.claimed_by}` : ''}
                  {ours && a.synced_from ? ` · from ${formatDay(a.synced_from)}` : ''}
                </span>
              </span>
              {ours && (
                <span className="bank-side">
                  {a.balance_pence !== null && (
                    <span className="bank-balance">
                      <Money pence={a.balance_pence} />
                      {a.balance_at && (
                        <span className="bank-balance-at">{formatTimestamp(a.balance_at)}</span>
                      )}
                    </span>
                  )}
                  <span className="bank-owner">{personName(ours.owner)}</span>
                </span>
              )}
            </li>
          )
        })}
      </ul>
      {connection.can_manage ? (
        <div className="row-actions bank-manage">
          {!soon && (
            <button type="button" className="button button-quiet" onClick={onRelink}>
              {connection.provider === 'starling_pat' ? 'Replace token' : 'Re-link'}
              <span className="visually-hidden">: {connection.bank}</span>
            </button>
          )}
          {connection.accounts.length > 0 && (
            <button type="button" className="button button-quiet" onClick={onMap}>
              {fed.length === 0 ? 'Choose what it brings in' : 'Change what it brings in'}
              <span className="visually-hidden">: {connection.bank}</span>
            </button>
          )}
          <button type="button" className="button button-quiet" onClick={onDisconnect}>
            Disconnect<span className="visually-hidden">: {connection.bank}</span>
          </button>
        </div>
      ) : (
        <p className="bank-meta">
          Only {connection.owner} can re-link or disconnect it: it’s their bank sign-in.
        </p>
      )}
    </li>
  )
}

function Disconnect({
  connection,
  onReauth,
  onDone,
}: {
  connection: BankConnection
  onReauth: () => void
  onDone: (text: string) => void
}) {
  const [remove, setRemove] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const n = connection.transactions
  const since = connection.first_transaction
  return (
    <div className="form">
      <p>
        The link ends here and at {connection.bank}. Syncing stops; CSV import still works for these
        accounts.
      </p>
      {n > 0 && (
        <label className="choice">
          <input type="checkbox" checked={remove} onChange={(e) => setRemove(e.target.checked)} />
          <span>
            Also delete the {n} transactions it brought in
            {since ? ` (since ${formatLongDate(since)})` : ''}. Payday ticks and bill matches made
            from them go too. This can’t be undone.
          </span>
        </label>
      )}
      <ErrorText error={error} />
      <div className="form-actions">
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            setBusy(true)
            setError(null)
            api
              .del<{ transactions_deleted: number }>(
                `/api/banks/${connection.id}?delete_transactions=${remove}`,
              )
              .then((r) =>
                onDone(
                  r.transactions_deleted > 0
                    ? `${connection.bank} disconnected and ${r.transactions_deleted} transactions deleted.`
                    : `${connection.bank} disconnected. Its transactions stay.`,
                ),
              )
              .catch((e: unknown) => {
                if (isReauth(e)) onReauth()
                else setError(e)
              })
              .finally(() => setBusy(false))
          }}
        >
          {busy
            ? 'Disconnecting…'
            : remove
              ? `Disconnect and delete ${n} transactions`
              : `Disconnect ${connection.bank}`}
        </button>
      </div>
    </div>
  )
}

// ---- the page --------------------------------------------------------------------------------

type Open =
  | { kind: 'choose' }
  | { kind: 'relink'; connection: BankConnection }
  | { kind: 'starling'; replace: BankConnection | null }
  | { kind: 'map'; connection: BankConnection }
  | { kind: 'approve'; connection: BankConnection }
  | { kind: 'disconnect'; connection: BankConnection }

export default function Banks() {
  const [params, setParams] = useSearchParams()
  const banks = useBanks()
  const accounts = useAccounts()
  const [open, setOpen] = useState<Open | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const byKey = useMemo(
    () => new Map((accounts.data ?? []).map((a) => [a.key, a])),
    [accounts.data],
  )

  if (banks.isPending || accounts.isPending) return <Loading what="bank links" />
  if (banks.isError) return <ErrorText error={banks.error} />
  const data = banks.data
  const find = (param: string) => {
    const id = params.get(param)
    return id ? data.connections.find((c) => String(c.id) === id) : undefined
  }
  // Arrivals: back from the bank (choose what it feeds), re-linked, sent to re-link, or back
  // from Monzo and waiting to be approved in the Monzo app.
  const returned = find('linked')
  const relinked = find('relinked')
  const toRelink = find('relink')
  const toApprove = find('approve')
  const error = params.get('error')
  const sheet: Open | null =
    open ??
    (returned
      ? { kind: 'map', connection: returned }
      : toApprove?.status === 'pending'
        ? { kind: 'approve', connection: toApprove }
        : toRelink
          ? toRelink.provider === 'starling_pat'
            ? { kind: 'starling', replace: toRelink }
            : { kind: 'relink', connection: toRelink }
          : null)
  const clearArrival = () => {
    if (params.size > 0) setParams({}, { replace: true })
  }
  const close = () => {
    setOpen(null)
    clearArrival()
  }
  const taken = new Set(
    data.connections.flatMap((c) => c.accounts.map((a) => a.account).filter(Boolean)),
  ) as Set<string>
  const usable = (accounts.data ?? []).filter((a) => a.role !== 'dormant')
  const canBank =
    data.can_link && (data.providers.enable_banking || data.providers.monzo || data.providers.demo)
  const notice = done ?? (relinked ? relinkedText(relinked) : null)

  return (
    <>
      <PageHeading
        title="Banks"
        lead="Read-only links that bring transactions in twice a day. Each link belongs to the person who made it."
      >
        {canBank && (
          <button
            type="button"
            className="button button-primary"
            onClick={() => setOpen({ kind: 'choose' })}
          >
            Link a bank
          </button>
        )}
      </PageHeading>

      {!data.can_link && (
        <Notice tone="warn">
          Bank links aren’t set up on this server yet: it needs an encryption key first (see
          docs/providers.md).
        </Notice>
      )}
      {error && (
        <Notice
          tone="danger"
          urgent
          action={
            <button type="button" className="button button-quiet" onClick={clearArrival}>
              Dismiss
            </button>
          }
        >
          The bank link didn’t finish: {error}
        </Notice>
      )}
      {notice && (
        <Notice tone="ok" urgent>
          {notice}
        </Notice>
      )}

      <section className="register" aria-labelledby="linked-title">
        <SectionHeading
          id="linked-title"
          title="Linked"
          aside={<span>Syncs at 06:00 and 18:00</span>}
        />
        {data.connections.length === 0 ? (
          <p className="empty">
            No banks linked yet. Until they are, import CSVs from{' '}
            <Link to="/transactions">Transactions</Link>.
          </p>
        ) : (
          <ul className="bank-strips">
            {data.connections.map((c) => (
              <ConnectionStrip
                key={c.id}
                connection={c}
                accounts={byKey}
                onMap={() => setOpen({ kind: 'map', connection: c })}
                onApprove={() => setOpen({ kind: 'approve', connection: c })}
                onRelink={() =>
                  setOpen(
                    c.provider === 'starling_pat'
                      ? { kind: 'starling', replace: c }
                      : { kind: 'relink', connection: c },
                  )
                }
                onDisconnect={() => setOpen({ kind: 'disconnect', connection: c })}
              />
            ))}
          </ul>
        )}
      </section>

      {data.unlinked.length > 0 && (
        <section className="register" aria-labelledby="unlinked-title">
          <SectionHeading
            id="unlinked-title"
            title="Not linked"
            aside={<span>CSV import only, for now</span>}
          />
          <ul className="bank-accounts bank-unlinked">
            {data.unlinked.map((u) => {
              const ours = byKey.get(u.key)
              const ink = inkFor(ours)
              const starling = (u.provider ?? '').toLowerCase().includes('starling')
              return (
                <li key={u.key} data-ink={ink}>
                  <span className="bank-mark" aria-hidden="true">
                    {ink && <Rosette ink={ink} size={32} />}
                  </span>
                  <span className="bank-account">
                    <span className="bank-account-ours">{shortName(u.name)}</span>
                    {u.provider && <span className="bank-account-theirs">{u.provider}</span>}
                  </span>
                  <span className="bank-side">
                    {data.can_link && (starling || canBank) && (
                      <button
                        type="button"
                        className="button button-quiet"
                        onClick={() =>
                          setOpen(
                            starling ? { kind: 'starling', replace: null } : { kind: 'choose' },
                          )
                        }
                      >
                        {starling ? 'Add a token' : 'Link'}
                        <span className="visually-hidden">: {shortName(u.name)}</span>
                      </button>
                    )}
                    <span className="bank-owner">{personName(ours?.owner)}</span>
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {sheet?.kind === 'choose' && (
        <Sheet
          open
          onOpenChange={(o) => !o && close()}
          title="Link a bank"
          description="You’ll sign in at your bank, which shares your accounts read-only, then sends you back here."
        >
          <Guarded confirmed={data.confirmed} purpose="hand over read-only access to a bank">
            {(onReauth) => <BankChooser relink={null} setup={data.setup} onReauth={onReauth} />}
          </Guarded>
        </Sheet>
      )}
      {sheet?.kind === 'relink' && (
        <Sheet open onOpenChange={(o) => !o && close()} title={`Re-link ${sheet.connection.bank}`}>
          <Guarded confirmed={data.confirmed} purpose={`re-link ${sheet.connection.bank}`}>
            {(onReauth) => (
              <BankChooser relink={sheet.connection} setup={data.setup} onReauth={onReauth} />
            )}
          </Guarded>
        </Sheet>
      )}
      {sheet?.kind === 'starling' && (
        <Sheet
          open
          onOpenChange={(o) => !o && close()}
          title={sheet.replace ? 'Replace the Starling token' : 'Add a Starling token'}
        >
          <Guarded confirmed={data.confirmed} purpose="hand over a Starling token">
            {(onReauth) => (
              <StarlingToken
                replace={sheet.replace}
                onReauth={onReauth}
                onLinked={(c) => setOpen({ kind: 'map', connection: c })}
              />
            )}
          </Guarded>
        </Sheet>
      )}
      {sheet?.kind === 'map' && (
        <Sheet
          open
          onOpenChange={(o) => !o && close()}
          title={`What does ${sheet.connection.bank} feed?`}
          description="Choose which of our accounts each bank account brings transactions into."
        >
          <Mapping
            connection={sheet.connection}
            accounts={usable}
            taken={taken}
            onDone={(c) => {
              setDone(
                c.last_run && c.last_run.status !== 'ok'
                  ? `${c.bank} is linked, but the first sync didn’t finish: ${c.last_error ?? 'try Sync now'}.`
                  : `${c.bank} is linked. ${c.last_run?.new ?? 0} transactions brought in so far.`,
              )
              close()
            }}
          />
        </Sheet>
      )}
      {sheet?.kind === 'approve' && (
        <Sheet
          open
          onOpenChange={(o) => !o && close()}
          title="Allow access in the Monzo app"
          description="You’re signed in to Monzo. One more step, on your phone, before anything can be read."
        >
          <Approve
            connection={sheet.connection}
            onMap={(c) => {
              clearArrival()
              setOpen({ kind: 'map', connection: c })
            }}
            onDone={(text) => {
              setDone(text)
              close()
            }}
          />
        </Sheet>
      )}
      {sheet?.kind === 'disconnect' && (
        <Sheet
          open
          onOpenChange={(o) => !o && close()}
          title={`Disconnect ${sheet.connection.bank}?`}
        >
          <Guarded confirmed={data.confirmed} purpose={`disconnect ${sheet.connection.bank}`}>
            {(onReauth) => (
              <Disconnect
                connection={sheet.connection}
                onReauth={onReauth}
                onDone={(text) => {
                  setDone(text)
                  close()
                  void banks.refetch()
                }}
              />
            )}
          </Guarded>
        </Sheet>
      )}
    </>
  )
}

function relinkedText(c: BankConnection): string {
  const until = c.consent_expires_at
    ? `, sharing until ${formatLongDate(c.consent_expires_at)}`
    : ''
  return `${c.bank} re-linked${until}. It carries on with the same accounts.`
}
