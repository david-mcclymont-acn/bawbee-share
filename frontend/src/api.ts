import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

// ---- transport ------------------------------------------------------------------------

let csrfToken: string | null = null

export function setCsrfToken(token: string | null) {
  csrfToken = token
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken
  const response = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    let message = `Something went wrong (${response.status}).`
    try {
      const data = (await response.json()) as { detail?: unknown }
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail)) message = 'Some of those values aren’t valid.'
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}

// ---- types (mirror backend/app/api) --------------------------------------------------------

export type Health = { status: string; app: string; version: string }

export type Me = {
  id: number
  username: string
  display_name: string
  person: string | null
  oidc_linked: boolean
  has_password: boolean
  csrf_token: string
}

export type AuthConfig = { local: boolean; oidc: { enabled: boolean; label: string } }

export type Version = {
  id: number
  name: string
  description: string | null
  type: 'live' | 'what_if' | 'period'
  status: 'active' | 'draft' | 'promoted' | 'archived'
  base_version_id: number | null
  planned_start: string | null
  period_start: string | null
  period_end: string | null
}

export type Transfer = {
  line_id: number
  name: string
  from_account: string
  to_account: string
  rule: 'joint_share' | 'cover_account_lines' | 'fixed'
  suggested_pence: number
  actual_pence: number | null
  effective_pence: number
  difference_pence: number
}

export type Summary = {
  version: Version
  as_of: string
  split_basis: string
  settings: {
    split_basis: string
    rounding_step_pence: number
    buffer_pence: number
    extra_lines_mode: string
    fixed_shares: Record<string, string>
  }
  joint_total_pence: number
  shares: Record<string, { exact: string; percent: string }>
  transfers: Transfer[]
  accounts: {
    key: string
    name: string
    role: string
    owner: string | null
    inflow_pence: number
    outflow_pence: number
  }[]
  surpluses: { personal: Record<string, number>; joint: number; household: number }
  split_options: Record<string, Record<string, number>>
  warnings: { code: string; message: string; subject: string | null }[]
}

export type LineValues = {
  name: string
  provider: string | null
  kind: 'bill' | 'allocation' | 'transfer'
  account: string
  to_account: string | null
  category: string | null
  owner: string | null
  amount_pence: number | null
  frequency: 'monthly' | 'annual' | 'quarterly' | 'weekly'
  day_of_month: number | null
  renewal_date: string | null
  transfer_rule: Record<string, unknown> | null
  actual_override_pence: number | null
  components: { label: string | null; amount_pence: number }[]
  agreement: Record<string, unknown> | null
  tags: string[]
  flags: string[]
  notes: string | null
}

export type Line = {
  line_id: number
  origin: 'base' | 'modified' | 'added' | 'computed'
  override_id: number | null
  monthly_pence: number
  values: LineValues
}

export type LineChange = {
  line_id: number
  label: string
  account: string
  change: 'added' | 'removed' | 'modified'
  fields: Record<string, [unknown, unknown]>
}

export type Conflict = {
  override_id: number
  line_id: number
  label: string
  action: string
  based_on: Partial<LineValues>
  live_now: Partial<LineValues>
  override: Partial<LineValues>
}

export type Diff = {
  version: Version
  base_version_id: number | null
  as_of: string
  changes: LineChange[]
  conflicts: Conflict[]
  redundant_override_ids: number[]
}

export type Projection = {
  account: string
  payday_month: string
  bill_month: string
  opening_pence: number
  minimum_pence: number
  minimum_day: string
  goes_negative: boolean
  undated_lines: string[]
  days: { day: string; closing_pence: number; movements: { name: string; pence: number }[] }[]
}

export type PaydayTransfer = {
  id: number | null
  line_id: number | null
  name: string
  from_account: string
  from_owner: string | null
  to_account: string
  suggested_pence: number
  actual_pence: number | null
  effective_pence: number
  /** What the budget says to send, and why this payday's figure is what it is. */
  budget_pence: number
  basis: 'calculated' | 'fixed' | 'changed'
  rule: 'joint_share' | 'cover_account_lines' | 'fixed'
  done_at: string | null
  done_by: string | null
  /** "bank" when an imported bank transfer ticked it. */
  done_via: 'bank' | 'manual' | null
}

export type Payday = {
  month: string
  funds_month: string
  pay_dates: Record<string, { official: string; expected: string }>
  transfer_date: string
  run_id: number | null
  transfers: PaydayTransfer[]
  complete: boolean
}

export type Account = {
  key: string
  name: string
  role: string
  owner: string | null
  provider: string | null
}

export type Person = { key: string; name: string }

export type FollowUp = {
  id: number
  line_id: number | null
  line_name: string | null
  reason: 'cancel' | 'check_price' | 'check_account' | 'check' | 'other'
  note: string | null
  status: 'open' | 'done' | 'dismissed'
}

export type Income = {
  person: string
  employer: string | null
  as_of_date: string
  gross_annual_pence: number
  benefits_allowance_pence: number | null
}

export type PromotionPreview = {
  effective_date: string
  changes: LineChange[]
  conflicts: Conflict[]
  settings_changes: Record<string, [unknown, unknown]>
  income_changes: Record<string, [number | null, number | null]>
}

export type Promotion = {
  id: number
  what_if_id: number
  what_if_name: string
  period_id: number | null
  effective_date: string
  undoable: boolean
}

export type SessionInfo = {
  id: number
  method: 'local' | 'oidc'
  created_at: string
  last_seen_at: string
  ip: string | null
  user_agent: string | null
  current: boolean
}

export type BankFormat = 'monzo' | 'starling' | 'rbs'

export type ColumnMapping = {
  date: string
  description: string
  amount?: string | null
  money_out?: string | null
  money_in?: string | null
  counterparty?: string | null
  date_format?: string
}

export type CsvRequest = {
  text: string
  filename?: string | null
  account?: string | null
  format?: BankFormat | null
  mapping?: ColumnMapping | null
}

export type ImportPreview = {
  format: BankFormat | 'mapped'
  headers: string[]
  rows: {
    line_number: number
    booked_date: string
    amount_pence: number
    description: string
    counterparty: string | null
    merchant: string
    duplicate: boolean
  }[]
  total: number
  new: number
  duplicates: number
  first_date: string | null
  last_date: string | null
  errors: [number, string][]
  last_format?: string | null
  /** Rows for days this account's bank link already covers: skipped. */
  bank_covered: number
}

export type ImportSummary = {
  batch_id: number
  format: string
  total: number
  new: number
  duplicates: number
  errors: [number, string][]
  bills_matched: number
  transfers_matched: number
  payday_ticked: number
  rule_matched: number
  price_changes: number
  unmatched: number
  /** [year, month, matched bills, bill lines] */
  coverage: [number, number, number, number][]
  bank_covered: number
  /** What the new rows showed straight away: in the inbox now. */
  regular_proposed: number
  missed: number
  still_paying: number
}

export type BankTransaction = {
  id: number
  account: string
  booked_date: string
  amount_pence: number
  description: string
  counterparty: string | null
  merchant: string
  category: string | null
  line_id: number | null
  line_name: string | null
  match_method: 'auto' | 'rule' | 'manual' | 'transfer' | null
  status: 'pending' | 'booked'
  source: 'bank' | 'csv'
}

export type MatchResult = {
  transaction: BankTransaction
  rule_id: number | null
  also_matched: number
  price_changes: number
}

export type OverridingWhatIf = { version_id: number; name: string; override_pence: number | null }

export type InboxItem = {
  id: number
  kind:
    | 'price_change'
    | 'new_recurring'
    | 'missed_payment'
    | 'still_paying'
    | 'consent_expiring'
    | 'remortgage_window'
    | 'goal_adjust'
    | 'renewal_due'
    | 'agreement_ending'
    | 'salary_mismatch'
  line_id: number | null
  transaction_id: number | null
  created_at: string
  /** "approved" while what-ifs that override the line still have a choice to make. */
  status: 'open' | 'snoozed' | 'approved'
  overriding: OverridingWhatIf[]
  /** Approved: the what-ifs still to choose what to do with the new price. */
  pending: OverridingWhatIf[]
  // price_change payload
  label?: string
  account?: string
  old_pence?: number
  new_pence?: number
  seen_on?: string
  frequency?: string
  component_old_pence?: number
  component_new_pence?: number
  // new_recurring payload
  candidate_id?: number
  merchant?: string
  cadence?: 'weekly' | 'monthly' | 'quarterly' | 'annual'
  amount_pence?: number
  day_of_month?: number | null
  occurrences?: number
  first_seen?: string
  last_seen?: string
  previous_amount_pence?: number | null
  price_changed_on?: string | null
  likely_line_id?: number | null
  likely_label?: string | null
  // missed_payment payload
  month?: string
  due_on?: string
  last_paid_on?: string
  last_paid_pence?: number
  // still_paying payload
  follow_up_id?: number
  note?: string | null
  paid_on?: string
  payments?: number
  taken_pence?: number
  // consent_expiring payload
  connection_id?: number
  bank?: string
  owner?: string
  owner_user_id?: number
  expires_on?: string | null
  expired?: boolean
  accounts?: string[]
  // goal_adjust payload
  goal_id?: number
  name?: string
  current_pence?: number
  needed_pence?: number
  target_pence?: number
  target_date?: string
  // renewal_due payload
  renewal_id?: number
  provider?: string | null
  renews_on?: string
  cost_pence?: number | null
  // remortgage_window payload
  lender?: string
  fix_end?: string
  fix_end_exact?: boolean
  balance_at_fix_end_pence?: number
  balance_anchored?: boolean
  payment_pence?: number | null
  // agreement_ending payload
  type?: string | null
  end_month?: string
  end_confirmed?: boolean
  final_payment_pence?: number | null
  // salary_mismatch payload
  person?: string
  on?: string
  landed_pence?: number
  expected_pence?: number
  budget_is_override?: boolean
  calculated_pence?: number
  description?: string
}

export type Reminder =
  | { kind: 'promotion_due'; version_id: number; name: string; planned_start: string }
  | {
      kind: 'redundant_override'
      version_id: number
      name: string
      line_id: number
      line_name: string
    }
  | { kind: 'stale_input'; label: string; as_of: string }

export type Inbox = {
  /** What the nav counts: bank proposals and choices still to make. Chores aren't counted. */
  count: number
  /** The other person's ending bank links: theirs to re-link, shown as a quiet line. */
  others_consents: {
    bank: string
    owner: string
    expires_on: string | null
    expired: boolean
    accounts: string[]
  }[]
  chores_count: number
  items: InboxItem[]
  /** watching_since: answered "I've cancelled it"; watching for another payment. */
  follow_ups: (Omit<FollowUp, 'status'> & { watching_since?: string | null })[]
  reminders: Reminder[]
}

export type Actual = {
  line_id: number
  label: string
  account: string
  kind: 'bill' | 'allocation' | 'transfer'
  planned_pence: number
  actual_pence: number
  status: 'paid' | 'partly' | 'not_seen' | 'due' | 'over' | 'under' | 'no_data'
  paid_on: string[]
  due_on: string | null
}

export type Actuals = {
  lines: Actual[]
  /** Account key -> the first and last day its exports cover this month. */
  covered: Record<string, { from: string; to: string }>
}

export type BankAccountView = {
  uid: string
  name: string
  detail: string | null
  /** Our account key it feeds, or null if it isn't brought in. */
  account: string | null
  synced_from: string | null
  synced_through: string | null
  balance_pence: number | null
  balance_at: string | null
  /** Already linked by the other person (the joint account): can't be brought in twice. */
  claimed_by: string | null
}

export type BankConnection = {
  id: number
  provider: 'enable_banking' | 'starling_pat' | 'monzo' | 'demo'
  bank: string
  owner: string
  owner_user_id: number
  can_manage: boolean
  /** Rows it has brought in, and the first day of them (what Disconnect would delete). */
  transactions: number
  first_transaction: string | null
  /** Sync now waits until then; banks limit how often they can be asked. */
  next_sync_now_at: string | null
  status: 'pending' | 'active' | 'expired' | 'revoked' | 'error'
  consent_expires_at: string | null
  last_sync_at: string | null
  last_error: string | null
  last_run: {
    status: string
    new: number
    updated: number
    removed: number
    at: string
    trigger: string
  } | null
  accounts: BankAccountView[]
}

export type BanksOverview = {
  can_link: boolean
  /** The password was typed (or the session started) in the last 12 hours. */
  confirmed: boolean
  providers: { enable_banking: boolean; starling: boolean; monzo: boolean; demo: boolean }
  // Why a provider isn't available on this server (null when it is).
  setup: { monzo: string | null; enable_banking: string | null }
  connections: BankConnection[]
  unlinked: { key: string; name: string; provider: string | null }[]
}

export type Institution = {
  name: string
  country: string
  provider: 'enable_banking' | 'monzo' | 'demo'
  max_consent_days: number | null
  beta: boolean
}

export type RegularPayment = {
  id: number
  account: string
  payee: string
  cadence: 'weekly' | 'monthly' | 'quarterly' | 'annual'
  amount_pence: number
  day_of_month: number | null
  occurrences: number
  first_seen: string
  last_seen: string
  previous_amount_pence: number | null
  price_changed_on: string | null
  status: 'new' | 'known' | 'accepted' | 'ignored'
  line_id: number | null
  line_label: string | null
}

export type HaStatus = {
  webhook: boolean
  low_balance_pence: number
  summary_url: string
  tokens: { id: number; label: string; created_at: string; last_used_at: string | null }[]
}

// ---- queries --------------------------------------------------------------------------

export const keys = {
  me: ['me'] as const,
  versions: ['versions'] as const,
  summary: (id: number) => ['summary', id] as const,
  lines: (id: number) => ['lines', id] as const,
  diff: (id: number) => ['diff', id] as const,
  projection: (id: number, y: number, m: number) => ['projection', id, y, m] as const,
  payday: (y: number, m: number) => ['payday', y, m] as const,
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api.get<Health>('/health'),
    refetchInterval: 60_000,
  })
}

export function useAuthConfig() {
  return useQuery({
    queryKey: ['auth-config'],
    queryFn: () => api.get<AuthConfig>('/api/auth/config'),
  })
}

export function useVersions() {
  return useQuery({ queryKey: keys.versions, queryFn: () => api.get<Version[]>('/api/versions') })
}

export function useSummary(id: number | undefined) {
  return useQuery({
    queryKey: keys.summary(id ?? 0),
    queryFn: () => api.get<Summary>(`/api/versions/${id}/summary`),
    enabled: id !== undefined,
  })
}

export function useLines(id: number | undefined) {
  return useQuery({
    queryKey: keys.lines(id ?? 0),
    queryFn: () => api.get<Line[]>(`/api/versions/${id}/lines`),
    enabled: id !== undefined,
  })
}

export function useDiff(id: number | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.diff(id ?? 0),
    queryFn: () => api.get<Diff>(`/api/versions/${id}/diff`),
    enabled: id !== undefined && enabled,
  })
}

export function useProjection(id: number | undefined, year: number, month: number) {
  return useQuery({
    queryKey: keys.projection(id ?? 0, year, month),
    queryFn: () =>
      api.get<Projection>(`/api/versions/${id}/projection?year=${year}&month=${month}`),
    enabled: id !== undefined,
  })
}

export function usePayday(year: number, month: number) {
  return useQuery({
    queryKey: keys.payday(year, month),
    queryFn: () => api.get<Payday>(`/api/payday/${year}/${month}`),
  })
}

export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: () => api.get<Account[]>('/api/accounts'),
    staleTime: Infinity,
  })
}

export function usePeople() {
  return useQuery({
    queryKey: ['people'],
    queryFn: () => api.get<Person[]>('/api/people'),
    staleTime: Infinity,
  })
}

export function useCategories() {
  return useQuery({
    queryKey: ['categories'],
    queryFn: () => api.get<string[]>('/api/categories'),
    staleTime: Infinity,
  })
}

export function useFollowUps() {
  return useQuery({
    queryKey: ['follow-ups'],
    queryFn: () => api.get<FollowUp[]>('/api/follow-ups'),
  })
}

export function useIncomes() {
  return useQuery({ queryKey: ['incomes'], queryFn: () => api.get<Income[]>('/api/incomes') })
}

export function useStale() {
  return useQuery({
    queryKey: ['stale'],
    queryFn: () => api.get<{ label: string; as_of: string }[]>('/api/checks/stale'),
  })
}

export function useLatestPromotion() {
  return useQuery({
    queryKey: ['promotion-latest'],
    queryFn: () => api.get<Promotion | null>('/api/promotions/latest'),
  })
}

export function useInbox() {
  return useQuery({ queryKey: ['inbox'], queryFn: () => api.get<Inbox>('/api/inbox') })
}

/** The nav's count: kept fresh so a price change from another tab shows up. */
export function useInboxCount() {
  return useQuery({
    queryKey: ['inbox', 'count'],
    queryFn: () => api.get<{ count: number }>('/api/inbox/count'),
    refetchInterval: 120_000,
  })
}

export type TransactionFilters = {
  account?: string
  month?: string
  status?: 'matched' | 'categorised' | 'needs'
  q?: string
}

export function useTransactions(filters: TransactionFilters) {
  const params = new URLSearchParams(
    Object.entries(filters).filter((e): e is [string, string] => Boolean(e[1])),
  )
  return useQuery({
    queryKey: ['transactions', filters],
    queryFn: () => api.get<BankTransaction[]>(`/api/transactions?${params.toString()}`),
  })
}

export function useActuals(month: string, enabled = true) {
  return useQuery({
    queryKey: ['actuals', month],
    queryFn: () => api.get<Actuals>(`/api/actuals?month=${month}`),
    enabled,
  })
}

export function useBanks() {
  return useQuery({ queryKey: ['banks'], queryFn: () => api.get<BanksOverview>('/api/banks') })
}

export function useInstitutions(enabled: boolean) {
  return useQuery({
    queryKey: ['institutions'],
    queryFn: () => api.get<Institution[]>('/api/banks/institutions'),
    enabled,
    staleTime: 60 * 60_000,
  })
}

export function useRegularPayments() {
  return useQuery({
    queryKey: ['recurring'],
    queryFn: () => api.get<RegularPayment[]>('/api/recurring'),
  })
}

export function useHa() {
  return useQuery({ queryKey: ['ha'], queryFn: () => api.get<HaStatus>('/api/ha') })
}

/** A mutation that refreshes every budget-derived query when it succeeds. */
export function useBudgetMutation<V, R = unknown>(fn: (vars: V) => Promise<R>) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      for (const key of [
        'versions',
        'summary',
        'lines',
        'diff',
        'projection',
        'payday',
        'follow-ups',
        'promotion-latest',
        'incomes',
        'inbox',
        'transactions',
        'actuals',
        'banks',
        'recurring',
        'ha',
        'plan',
      ]) {
        void client.invalidateQueries({ queryKey: [key] })
      }
    },
  })
}
