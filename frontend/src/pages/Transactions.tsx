import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { inkFor, shortName } from '../accounts.ts'
import {
  api,
  ApiError,
  useAccounts,
  useBudgetMutation,
  useCategories,
  useTransactions,
  type Account,
  type BankTransaction,
  type ColumnMapping,
  type CsvRequest,
  type ImportPreview,
  type ImportSummary,
  type MatchResult,
  type TransactionFilters,
} from '../api.ts'
import { formatDay, formatMonthYear, isoDate, today } from '../dates.ts'
import { BillLineSelect, NotListed } from '../lines.tsx'
import { formatPence } from '../money.ts'
import { Rosette } from '../marks.tsx'
import { ErrorText, Field, Loading, Money, Notice, PageHeading, Sheet } from '../ui.tsx'

const BANKS: Record<string, string> = {
  monzo: 'Monzo',
  starling: 'Starling',
  rbs: 'RBS',
  mapped: 'your column choices',
}
const MAX_BYTES = 5_000_000
const LAST_ACCOUNT = 'bawbee.import.account'

/** The header row of a CSV, quotes and all: enough to offer the columns to map. */
function csvHeaders(text: string): string[] {
  const first =
    text
      .replace(/^\uFEFF/, '')
      .split(/\r?\n/)
      .find((l) => l.trim() !== '') ?? ''
  const out: string[] = []
  let cell = ''
  let quoted = false
  for (let i = 0; i < first.length; i++) {
    const ch = first[i]
    if (quoted) {
      if (ch === '"' && first[i + 1] === '"') {
        cell += '"'
        i++
      } else if (ch === '"') quoted = false
      else cell += ch
    } else if (ch === '"') quoted = true
    else if (ch === ',') {
      out.push(cell.trim())
      cell = ''
    } else cell += ch
  }
  out.push(cell.trim())
  return out.filter(Boolean)
}

// ---- import ----------------------------------------------------------------------------

function ColumnPicker({
  headers,
  mapping,
  onChange,
}: {
  headers: string[]
  mapping: ColumnMapping
  onChange: (m: ColumnMapping) => void
}) {
  const [split, setSplit] = useState(Boolean(mapping.money_out))
  const pick = (label: string, key: keyof ColumnMapping, optional = false) => (
    <Field label={label}>
      {(id) => (
        <select
          id={id}
          value={(mapping[key] as string | null | undefined) ?? ''}
          onChange={(e) => onChange({ ...mapping, [key]: e.target.value || null })}
          required={!optional}
        >
          <option value="">{optional ? 'None' : 'Choose a column'}</option>
          {headers.map((h) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
  return (
    <fieldset className="mapping">
      <legend>Which column holds what?</legend>
      <div className="field-row">
        {pick('Date', 'date')}
        <Field label="Dates look like">
          {(id) => (
            <select
              id={id}
              value={mapping.date_format ?? '%d/%m/%Y'}
              onChange={(e) => onChange({ ...mapping, date_format: e.target.value })}
            >
              <option value="%d/%m/%Y">30/10/2026</option>
              <option value="%Y-%m-%d">2026-10-30</option>
              <option value="%d %b %Y">30 Oct 2026</option>
              <option value="%d-%m-%Y">30-10-2026</option>
            </select>
          )}
        </Field>
      </div>
      <div className="field-row">
        {pick('Description', 'description')}
        {pick('Payee', 'counterparty', true)}
      </div>
      <label className="choice">
        <input
          type="checkbox"
          checked={split}
          onChange={(e) => {
            setSplit(e.target.checked)
            onChange({ ...mapping, amount: null, money_out: null, money_in: null })
          }}
        />
        Money out and money in are separate columns
      </label>
      {split ? (
        <div className="field-row">
          {pick('Money out', 'money_out')}
          {pick('Money in', 'money_in')}
        </div>
      ) : (
        pick('Amount (money out negative)', 'amount')
      )}
    </fieldset>
  )
}

function ImportWizard({ accounts, onClose }: { accounts: Account[]; onClose: () => void }) {
  const [file, setFile] = useState<{ name: string; text: string } | null>(null)
  const [account, setAccount] = useState(() => localStorage.getItem(LAST_ACCOUNT) ?? '')
  const [mapping, setMapping] = useState<ColumnMapping | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [needsMapping, setNeedsMapping] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [summary, setSummary] = useState<ImportSummary | null>(null)

  const request = (): CsvRequest => ({
    text: file!.text,
    filename: file!.name,
    account: account || null,
    mapping: needsMapping ? mapping : null,
  })
  // A preview changes nothing, so it refreshes nothing.
  const check = useMutation({
    mutationFn: (body: CsvRequest) => api.post<ImportPreview>('/api/imports/preview', body),
  })
  const run = useBudgetMutation((body: CsvRequest) => api.post<ImportSummary>('/api/imports', body))

  const doPreview = (body: CsvRequest) => {
    setError(null)
    setPreview(null)
    check.mutate(body, {
      onSuccess: setPreview,
      onError: (e) => {
        if (
          e instanceof ApiError &&
          e.status === 422 &&
          /column/i.test(e.message) &&
          !body.mapping
        ) {
          const headers = csvHeaders(body.text)
          setNeedsMapping(true)
          setMapping({ date: headers.find((h) => /date/i.test(h)) ?? '', description: '' })
        }
        setError(e)
      },
    })
  }

  if (summary) {
    const cover = summary.coverage.at(-1)
    const inboxNew =
      summary.price_changes + summary.regular_proposed + summary.missed + summary.still_paying
    return (
      <div className="form import-summary">
        <p className="import-headline">
          {summary.new === 0
            ? 'Nothing new: every row was already here.'
            : `${summary.new} new transactions imported.`}
        </p>
        <dl className="import-figures">
          <div>
            <dt>Already here</dt>
            <dd>{summary.duplicates}</dd>
          </div>
          {summary.bank_covered > 0 && (
            <div>
              <dt>Already brought in by the bank link</dt>
              <dd>{summary.bank_covered}</dd>
            </div>
          )}
          <div>
            <dt>Bills matched</dt>
            <dd>{summary.bills_matched + summary.rule_matched}</dd>
          </div>
          <div>
            <dt>Transfers matched</dt>
            <dd>{summary.transfers_matched}</dd>
          </div>
          <div>
            <dt>Payday ticked</dt>
            <dd>{summary.payday_ticked}</dd>
          </div>
          <div>
            <dt>Price changes</dt>
            <dd>{summary.price_changes}</dd>
          </div>
          {summary.regular_proposed > 0 && (
            <div>
              <dt>New regular payments</dt>
              <dd>{summary.regular_proposed}</dd>
            </div>
          )}
          {summary.missed + summary.still_paying > 0 && (
            <div>
              <dt>Payments to check</dt>
              <dd>{summary.missed + summary.still_paying}</dd>
            </div>
          )}
          <div>
            <dt>Left to look at</dt>
            <dd>{summary.unmatched}</dd>
          </div>
        </dl>
        {cover && (
          <p>
            {formatMonthYear(isoDate(new Date(cover[0], cover[1] - 1, 1)))}: {cover[2]} of{' '}
            {cover[3]} monthly bills seen so far.
          </p>
        )}
        {summary.errors.length > 0 && (
          <Notice tone="warn">
            {summary.errors.length} rows couldn’t be read and were skipped (first: line{' '}
            {summary.errors[0]![0]}, {summary.errors[0]![1]}).
          </Notice>
        )}
        <div className="form-actions">
          {inboxNew > 0 && (
            <Link className="button button-primary" to="/inbox" onClick={onClose}>
              {summary.price_changes > 0 && inboxNew === summary.price_changes
                ? 'Review price changes'
                : 'Open the inbox'}
            </Link>
          )}
          <button
            type="button"
            className={inboxNew > 0 ? 'button' : 'button button-primary'}
            onClick={onClose}
          >
            Done
          </button>
        </div>
      </div>
    )
  }

  const chosen = accounts.find((a) => a.key === account)
  const formatMismatch =
    preview?.last_format && preview.format !== 'mapped' && preview.last_format !== preview.format

  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault()
        if (!file || !account || !preview) return
        localStorage.setItem(LAST_ACCOUNT, account)
        run.mutate(request(), { onSuccess: setSummary })
      }}
    >
      <Field
        label="Bank export"
        hint="A CSV from Monzo, Starling or RBS is recognised. Any other bank: choose its columns once."
      >
        {(id) => (
          <input
            id={id}
            type="file"
            accept=".csv,text/csv"
            className="file-input"
            onChange={(e) => {
              const f = e.target.files?.[0]
              setPreview(null)
              setNeedsMapping(false)
              setError(null)
              if (!f) return setFile(null)
              if (f.size > MAX_BYTES) {
                setFile(null)
                setError(new Error('That file is over 5 MB. Export a shorter date range.'))
                return
              }
              void f.text().then((text) => {
                setFile({ name: f.name, text })
                doPreview({ text, filename: f.name, account: account || null })
              })
            }}
          />
        )}
      </Field>
      <Field label="Which account is it from?">
        {(id) => (
          <select
            id={id}
            value={account}
            required
            onChange={(e) => {
              setAccount(e.target.value)
              if (file) doPreview({ ...request(), account: e.target.value || null })
            }}
          >
            <option value="">Choose an account</option>
            {accounts.map((a) => (
              <option key={a.key} value={a.key}>
                {shortName(a.name)}
                {a.provider ? ` · ${a.provider}` : ''}
              </option>
            ))}
          </select>
        )}
      </Field>

      {needsMapping && file && mapping && (
        <>
          <ColumnPicker headers={csvHeaders(file.text)} mapping={mapping} onChange={setMapping} />
          <div>
            <button
              type="button"
              className="button"
              disabled={check.isPending || !mapping.date || !mapping.description}
              onClick={() => doPreview(request())}
            >
              Read the file with these columns
            </button>
          </div>
        </>
      )}

      {check.isPending && <Loading what="the file" />}
      {!preview && <ErrorText error={error} />}

      {preview && (
        <section className="import-preview" aria-label="Preview" data-ink={inkFor(chosen)}>
          <p>
            <strong>{BANKS[preview.format] ?? preview.format}</strong> export · {preview.total} rows
            {preview.first_date && preview.last_date && (
              <>
                , {formatDay(preview.first_date)} to {formatDay(preview.last_date)}
              </>
            )}
            .{' '}
            {account
              ? preview.duplicates > 0
                ? preview.bank_covered > 0
                  ? `${preview.new} new, ${preview.duplicates} already here (${preview.bank_covered} from the bank link).`
                  : `${preview.new} new, ${preview.duplicates} already imported.`
                : `All ${preview.new} are new.`
              : 'Choose the account to see which are new.'}
          </p>
          {formatMismatch && (
            <Notice tone="warn">
              Last time, {shortName(chosen?.name ?? '')} sent a {BANKS[preview.last_format!]} file.
              Is this the right account?
            </Notice>
          )}
          {preview.errors.length > 0 && (
            <Notice tone="warn">
              {preview.errors.length} rows can’t be read and will be skipped (first: line{' '}
              {preview.errors[0]![0]}, {preview.errors[0]![1]}).
            </Notice>
          )}
          <ul className="txn-list txn-list-compact">
            {preview.rows.map((r) => (
              <li
                key={r.line_number}
                data-duplicate={r.duplicate || undefined}
                data-in={r.amount_pence > 0 || undefined}
              >
                <span className="txn-date">{formatDay(r.booked_date)}</span>
                <span className="txn-what">
                  <span className="txn-merchant">{r.merchant}</span>
                  {r.duplicate && <span className="txn-note">Already imported</span>}
                </span>
                <Money pence={r.amount_pence} />
              </li>
            ))}
          </ul>
          {preview.total > preview.rows.length && (
            <p className="field-hint">And {preview.total - preview.rows.length} more.</p>
          )}
        </section>
      )}

      <ErrorText error={run.error} />
      <div className="form-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={!preview || !account || preview.new === 0 || run.isPending}
        >
          {run.isPending
            ? 'Importing…'
            : preview && account
              ? `Import ${preview.new} new`
              : 'Import'}
        </button>
      </div>
    </form>
  )
}

// ---- match ------------------------------------------------------------------------------

function MatchForm({ txn, onClose }: { txn: BankTransaction; onClose: () => void }) {
  const categories = useCategories()
  const [lineId, setLineId] = useState(txn.line_id ? String(txn.line_id) : '')
  const [category, setCategory] = useState(txn.category ?? '')
  const [always, setAlways] = useState(false)
  const [result, setResult] = useState<MatchResult | null>(null)
  const save = useBudgetMutation(() =>
    api.patch<MatchResult>(`/api/transactions/${txn.id}`, {
      line_id: lineId ? Number(lineId) : null,
      category: category.trim() || null,
      always,
    }),
  )

  if (result) {
    return (
      <div className="form">
        <Notice tone="ok" urgent>
          {result.transaction.line_name ? `Matched to ${result.transaction.line_name}.` : 'Saved.'}
          {result.also_matched > 0 &&
            ` ${result.also_matched} other ${result.also_matched === 1 ? 'payment' : 'payments'} from ${txn.merchant} matched too.`}
        </Notice>
        {result.price_changes > 0 && (
          <Notice
            tone="warn"
            action={
              <Link className="button" to="/inbox" onClick={onClose}>
                Review
              </Link>
            }
          >
            It was paid at a different price from the budget, so there’s a price change in the
            Inbox.
          </Notice>
        )}
        <div className="form-actions">
          <button type="button" className="button button-primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    )
  }

  // Clearing both fields on something already matched or categorised is an unmatch.
  const wasSet = Boolean(txn.line_id || txn.category)
  const clearing = wasSet && !lineId && !category.trim()

  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(undefined, { onSuccess: setResult })
      }}
    >
      <dl className="txn-facts">
        <div>
          <dt>Paid</dt>
          <dd>
            <Money pence={txn.amount_pence} /> on {formatDay(txn.booked_date)}
          </dd>
        </div>
        <div>
          <dt>Bank text</dt>
          <dd>{[txn.counterparty, txn.description].filter(Boolean).join(' · ')}</dd>
        </div>
      </dl>
      <Field
        label="Budget line"
        hint={
          <>
            Leave as none for everyday spending; give it a category instead. <NotListed />
          </>
        }
      >
        {(id, hintId) => (
          <BillLineSelect
            id={id}
            account={txn.account}
            value={lineId}
            onChange={setLineId}
            none="None: not a bill"
            describedBy={hintId}
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
      <label className="choice">
        <input type="checkbox" checked={always} onChange={(e) => setAlways(e.target.checked)} />
        <span>
          Always match <strong>{txn.merchant}</strong> on this account like this
        </span>
      </label>
      <ErrorText error={save.error} />
      <div className="form-actions">
        <button
          className="button button-primary"
          type="submit"
          disabled={save.isPending || (!lineId && !category.trim() && !wasSet)}
        >
          {save.isPending ? 'Saving…' : clearing ? 'Clear the match' : 'Save'}
        </button>
      </div>
      {clearing && (
        <p className="field-hint">
          It’ll stay unmatched: automatic matching leaves a payment alone once you’ve cleared it.
        </p>
      )}
    </form>
  )
}

// ---- the page ----------------------------------------------------------------------------

function matchText(t: BankTransaction): string | null {
  if (t.match_method === 'transfer') return t.line_name ? `Transfer · ${t.line_name}` : 'Transfer'
  if (!t.line_name) return null
  if (t.match_method === 'rule') return `${t.line_name} · by your rule`
  return t.line_name
}

/** Money out that's neither a bill, a transfer nor categorised spending. */
function needsLook(t: BankTransaction): boolean {
  return t.amount_pence < 0 && !t.line_id && t.match_method !== 'transfer' && !t.category
}

function TransactionRow({
  t,
  account,
  showAccount,
  onMatch,
}: {
  t: BankTransaction
  account: Account | undefined
  showAccount: boolean
  onMatch: () => void
}) {
  const ink = inkFor(account)
  const matched = matchText(t)
  const needs = needsLook(t)
  const name = shortName(account?.name ?? t.account)
  return (
    <li data-ink={ink} data-in={t.amount_pence > 0 || undefined} data-needs={needs || undefined}>
      <span className="txn-mark" aria-hidden="true">
        {ink && <Rosette ink={ink} size={28} />}
      </span>
      <span className="txn-what">
        <span className="txn-merchant">
          {t.merchant}
          {t.status === 'pending' && <span className="txn-pending">Pending</span>}
        </span>
        <span className="txn-note">
          {showAccount && <span className="txn-account">{name}</span>}
          {t.description}
        </span>
        {matched ? (
          <span className="txn-matched">{matched}</span>
        ) : needs ? (
          <span className="txn-matched" data-needs>
            Needs a look
          </span>
        ) : t.category ? (
          <span className="txn-matched" data-open>
            {t.category}
          </span>
        ) : null}
      </span>
      <span className="txn-side">
        <Money pence={t.amount_pence} />
        {t.amount_pence < 0 && t.match_method !== 'transfer' && (
          <button
            type="button"
            className={needs ? 'button txn-action' : 'button button-quiet txn-action'}
            onClick={onMatch}
          >
            {needs ? 'Match' : 'Change'}
            <span className="visually-hidden">
              : {t.merchant}, {formatPence(t.amount_pence)} on {formatDay(t.booked_date)}
            </span>
          </button>
        )}
      </span>
    </li>
  )
}

export default function Transactions() {
  const [params, setParams] = useSearchParams()
  const accounts = useAccounts()
  const filters: TransactionFilters = {
    account: params.get('account') ?? undefined,
    month: params.get('month') ?? isoDate(today()).slice(0, 7),
    status: (params.get('status') as TransactionFilters['status']) ?? undefined,
    q: params.get('q') ?? undefined,
  }
  const txns = useTransactions(filters)
  const [importing, setImporting] = useState(false)
  const [matching, setMatching] = useState<BankTransaction | null>(null)
  const [search, setSearch] = useState(filters.q ?? '')
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }
  // Search as you type, once the typing pauses.
  const query = filters.q ?? ''
  useEffect(() => {
    if (search.trim() === query) return
    const timer = window.setTimeout(() => set('q', search.trim()), 300)
    return () => window.clearTimeout(timer)
  })
  const usable = (accounts.data ?? []).filter((a) => a.role !== 'dormant')
  const byKey = new Map(usable.map((a) => [a.key, a]))
  const rows = txns.data ?? []
  const needs = rows.filter(needsLook)
  const days: [string, BankTransaction[]][] = []
  for (const row of rows) {
    const last = days.at(-1)
    if (last && last[0] === row.booked_date) last[1].push(row)
    else days.push([row.booked_date, [row]])
  }

  return (
    <>
      <PageHeading
        title="Transactions"
        lead={
          <>
            From bank links and CSV exports. Read-only: nothing here can move money.{' '}
            <Link to="/banks">Bank links</Link> · <Link to="/regular">Regular payments</Link>
          </>
        }
      >
        <button type="button" className="button button-primary" onClick={() => setImporting(true)}>
          Import a CSV
        </button>
      </PageHeading>

      <form
        className="filters"
        role="search"
        onSubmit={(e) => {
          e.preventDefault()
          set('q', search.trim())
        }}
      >
        <Field label="Account">
          {(id) => (
            <select
              id={id}
              value={filters.account ?? ''}
              onChange={(e) => set('account', e.target.value)}
            >
              <option value="">All accounts</option>
              {usable.map((a) => (
                <option key={a.key} value={a.key}>
                  {shortName(a.name)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Month">
          {(id) => (
            <input
              id={id}
              type="month"
              value={filters.month ?? ''}
              onChange={(e) => set('month', e.target.value)}
            />
          )}
        </Field>
        <Field label="Show">
          {(id) => (
            <select
              id={id}
              value={filters.status ?? ''}
              onChange={(e) => set('status', e.target.value)}
            >
              <option value="">Everything</option>
              <option value="needs">Needs a look</option>
              <option value="matched">Matched to the budget</option>
              <option value="categorised">Everyday spending</option>
            </select>
          )}
        </Field>
        <Field label="Search">
          {(id) => (
            <input
              id={id}
              type="search"
              value={search}
              placeholder="Payee or reference"
              onChange={(e) => setSearch(e.target.value)}
            />
          )}
        </Field>
      </form>

      {txns.isPending ? (
        <Loading what="transactions" />
      ) : txns.isError ? (
        <ErrorText error={txns.error} />
      ) : rows.length === 0 ? (
        <div className="empty">
          <p>
            {filters.status === 'needs' && !filters.q
              ? 'Nothing needs a look. Every payment is matched or categorised.'
              : filters.q || filters.status || filters.account
                ? 'Nothing matches those filters.'
                : `No transactions for ${formatMonthYear(`${filters.month}-01`)} yet.`}
          </p>
          {!filters.status && <p>Export a CSV from your bank app and import it here.</p>}
        </div>
      ) : (
        <>
          <p className="split-line">
            {rows.length} transactions.{' '}
            {needs.length > 0 && filters.status !== 'needs' ? (
              <button type="button" className="text-button" onClick={() => set('status', 'needs')}>
                {needs.length} {needs.length === 1 ? 'payment needs' : 'payments need'} a look
              </button>
            ) : filters.status !== 'needs' ? (
              'Every payment is matched or categorised.'
            ) : null}
          </p>
          {days.map(([day, list]) => (
            <section key={day} className="txn-day" aria-label={formatDay(day)}>
              <h2 className="txn-day-head">{formatDay(day)}</h2>
              <ul className="txn-list">
                {list.map((t) => (
                  <TransactionRow
                    key={t.id}
                    t={t}
                    account={byKey.get(t.account)}
                    showAccount={!filters.account}
                    onMatch={() => setMatching(t)}
                  />
                ))}
              </ul>
            </section>
          ))}
        </>
      )}

      <Sheet
        open={importing}
        onOpenChange={setImporting}
        title="Import a bank CSV"
        description="The file stays on this server. Rows already imported are skipped, so overlapping exports are fine."
      >
        {importing && <ImportWizard accounts={usable} onClose={() => setImporting(false)} />}
      </Sheet>
      <Sheet
        open={matching !== null}
        onOpenChange={(o) => !o && setMatching(null)}
        title={matching ? matching.merchant : 'Match'}
        description="Match it to a budget line, or give it a category."
      >
        {matching && (
          <MatchForm key={matching.id} txn={matching} onClose={() => setMatching(null)} />
        )}
      </Sheet>
    </>
  )
}
