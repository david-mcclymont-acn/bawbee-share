import { useQuery } from '@tanstack/react-query'
import { api, type Summary } from './api.ts'

// ---- types (mirror backend/app/api/plan.py) -------------------------------------------------

/** Inbox items about planning (Phases 6 and 7), shown under Looking ahead. */
export const PLAN_KINDS = [
  'goal_adjust',
  'salary_mismatch',
  'renewal_due',
  'remortgage_window',
  'agreement_ending',
] as const

export type PlanVersion = {
  id: number
  name: string
  type: 'live' | 'what_if'
  planned_start: string | null
  childcare: string | null
}

export type PathStep = { version_id: number; version_name: string; from_month: string }
export type ForecastPath = {
  id: number
  name: string
  is_default: boolean
  notes: string | null
  steps: PathStep[]
}
export type Paths = {
  live: { id: 0; name: string; version_id: number }
  paths: ForecastPath[]
  default_id: number
}

export type ForecastMonth = {
  month: string
  version_id: number
  version_name: string
  household_pence: number
  joint_pence: number
  personal_pence: Record<string, number>
  balances: Record<string, number>
  events: string[]
  one_offs: { account: string; pence: number; label: string }[]
  goals: Record<string, number>
}
export type GoalRun = {
  key: string
  id: number | undefined
  name: string
  target_pence: number
  target_month: string
  saved_pence: number
  needed_pence: number
  allocation_pence: number
  on_track: boolean
  short_by_pence: number
}
export type ForecastRun = {
  path_id: number
  name: string
  months: ForecastMonth[]
  goals: GoalRun[]
  warnings: { month: string; code: string; subject: string; message: string }[]
}
export type ForecastAccount = {
  key: string
  name: string
  role: string
  owner: string | null
  opening_pence: number
  opening_source: 'bank' | 'entered' | 'none'
  opening_as_of: string | null
}
export type Forecast = { start: string; accounts: ForecastAccount[]; runs: ForecastRun[] }

export type OneOff = {
  id: number
  label: string
  account: string
  on: string
  amount_pence: number
  path_id: number | null
}

export type Goal = {
  id: number
  name: string
  account: string
  line_id: number | null
  line_label: string | null
  target_pence: number
  target_date: string
  saved_pence: number
  saved_on: string
  repeats_yearly: boolean
  status: string
}
export type Goals = {
  goals: Goal[]
  candidates: { line_id: number; label: string; account: string; monthly_pence: number }[]
}

export type CompareLine = {
  line_id: number
  label: string
  account: string
  kind: string
  computed: boolean
  amounts: Record<string, number | undefined>
  differs: boolean
}
export type Compare = { versions: Summary[]; lines: CompareLine[] }

export type TaxSlice = { label: string; rate: string; pence: number }
export type TakeHomePerson = {
  person: string
  name: string
  income: {
    id: number
    employer: string | null
    gross_annual_pence: number
    benefits_allowance_pence: number | null
    salary_sacrifice_pence: number | null
    pension_scheme: 'nhs_scotland' | 'workplace_pct' | 'none'
    pension_pct: string | null
    tax_code: string | null
    payslip_net_monthly_pence: number | null
    payslip_on: string | null
  }
  calculated: {
    gross_pence: number
    sacrifice_pence: number
    pension_rate: string
    pension_pence: number
    allowance_pence: number
    tax_pence: number
    tax_slices: TaxSlice[]
    ni_pence: number
    net_pence: number
    net_monthly_pence: number
  }
  budget_monthly_pence: number | null
  expected_monthly_pence: number
  payslip_monthly_pence: number | null
  salaries: { on: string; pence: number; description: string; difference_pence: number }[]
}
export type TakeHome = {
  tax_year: string
  exact_year: boolean
  sources: { bands: string | null; national_insurance: string | null; pension: string | null }
  tolerance_pence: number
  people: TakeHomePerson[]
}

export type ChildcareRate = { rate: string; discount_pct: string; min_days: string }
export type Nursery = {
  id: number
  name: string
  basis: 'day' | 'hour'
  takes_tfc: boolean
  bills_leave: boolean
  discounts_confirmed: boolean
  rates: ChildcareRate[]
}
export type Pattern = { id: number; days: string; hours: string }
export type Child = {
  id: number
  name: string
  born_on: string | null
  eligible_at_two: boolean
  funded_from: string | null
  funded_now: boolean
}
export type CareCell = {
  nursery_id: number
  pattern_id: number
  monthly_left_pence: number
  annual_left_pence: number
  share_of_take_home: string
  pay: {
    gross_pence: number
    pension_rate: string
    pension_pence: number
    tax_pence: number
    tax_slices: TaxSlice[]
    ni_pence: number
    net_pence: number
  }
  childcare: {
    days_billed: string
    days_attended: string
    leave_days: string
    bank_holidays: string
    hours_per_day: string
    bills_leave: boolean
    takes_tfc: boolean
    before_tfc_pence: number
    tfc_pence: number
    net_pence: number
    funded_pence: number
    leave_waste_pence: number
    children: {
      name: string
      rate_pence: number
      effective_pence: number
      per_day_pence: number
      discount_applied: boolean
      discount_pct: string
      funded_pence: number
      cost_pence: number
      tfc_pence: number
    }[]
  }
  child_benefit: { gross_pence: number; net_pence: number; clawback_pct: string }
  /** The same figures a month: divided exactly, rounded once. */
  month: {
    pay: {
      gross_pence: number
      pension_pence: number
      tax_slices: TaxSlice[]
      ni_pence: number
      net_pence: number
    }
    childcare: {
      before_tfc_pence: number
      tfc_pence: number
      net_pence: number
      children: { cost_pence: number; tfc_pence: number; funded_pence: number }[]
    }
    child_benefit_pence: number
  }
}
export type CareScenario = {
  id: number
  name: string
  nursery_id: number
  pattern_id: number
  account: string
  version_id: number | null
  version_name: string | null
}
export type Childcare = {
  tax_year: string
  fields: Record<string, string | boolean | null>
  children: Child[]
  nurseries: Nursery[]
  patterns: Pattern[]
  cells: CareCell[]
  best: { nursery_id: number; pattern_id: number } | null
  payslip_check: {
    calculated_pence: number
    payslip_pence: number
    difference_pence: number
    matches: boolean
  } | null
  scenarios: CareScenario[]
}

export type MortgageView = {
  mortgage: {
    id: number
    lender: string
    principal_pence: number | null
    rate_pct: string | null
    term_months: number | null
    started_on: string | null
    first_payment_on: string | null
    fix_end_on: string | null
    fix_end_exact: boolean
    payment_pence: number | null
    overpay_allowance_pct: string
    allowance_basis: 'anniversary' | 'calendar_year'
    property_value_pence: number | null
    property_value_on: string | null
    notes: string | null
    line_id: number | null
    line_amount_pence: number | null
  } | null
  balance_pence: number
  balance_anchored: boolean
  balance_as_of: string
  statement: { on: string; balance_pence: number } | null
  statements: { on: string; balance_pence: number }[]
  months_to_fix_end: number | null
  remortgage_window: string | null
  balance_at_fix_end_pence: number
  months_left: number
  ltv: {
    pct: string
    band: number | null
    next_band: number | null
    to_next_band_pence: number
  } | null
  schedule: {
    on: string
    opening_pence: number
    interest_pence: number
    payment_pence: number
    closing_pence: number
  }[]
  history: { lender: string; started: number | null }[]
}
export type Overpay = {
  months_saved: number
  interest_saved_pence: number
  interest_without_pence: number
  interest_with_pence: number
  paid_off_without: string | null
  paid_off_with: string | null
  over_allowance: { year_from: string; overpaid_pence: number; allowance_pence: number }[]
}

export type Renewal = {
  id: number
  name: string
  provider: string | null
  line_id: number | null
  line_label: string | null
  renews_on: string
  cost_pence: number | null
  remind_days: number
  notes: string | null
}
export type Renewals = {
  renewals: Renewal[]
  candidates: {
    line_id: number
    label: string
    name: string
    provider: string | null
    account: string
    monthly_pence: number
    renewal_date: string | null
  }[]
}

// ---- queries (all under 'plan', refreshed by every budget mutation) ---------------------------

export function usePlanVersions() {
  return useQuery({
    queryKey: ['plan', 'versions'],
    queryFn: () => api.get<PlanVersion[]>('/api/plan/versions'),
  })
}

export function usePaths() {
  return useQuery({ queryKey: ['plan', 'paths'], queryFn: () => api.get<Paths>('/api/plan/paths') })
}

export function useForecast(pathIds: number[] | undefined) {
  const params = new URLSearchParams((pathIds ?? []).map((id) => ['path', String(id)]))
  return useQuery({
    queryKey: ['plan', 'forecast', pathIds ?? 'default'],
    queryFn: () => api.get<Forecast>(`/api/plan/forecast?${params.toString()}`),
    placeholderData: (previous) => previous,
  })
}

export function useOneOffs() {
  return useQuery({
    queryKey: ['plan', 'one-offs'],
    queryFn: () => api.get<OneOff[]>('/api/plan/one-offs'),
  })
}

export function useGoals() {
  return useQuery({ queryKey: ['plan', 'goals'], queryFn: () => api.get<Goals>('/api/plan/goals') })
}

export function useCompare(versionIds: number[]) {
  const params = new URLSearchParams(versionIds.map((id) => ['version', String(id)]))
  return useQuery({
    queryKey: ['plan', 'compare', versionIds],
    queryFn: () => api.get<Compare>(`/api/plan/compare?${params.toString()}`),
    enabled: versionIds.length >= 2,
    placeholderData: (previous) => previous,
  })
}

export function useTakeHome() {
  return useQuery({
    queryKey: ['plan', 'take-home'],
    queryFn: () => api.get<TakeHome>('/api/plan/take-home'),
  })
}

export function useChildcare() {
  return useQuery({
    queryKey: ['plan', 'childcare'],
    queryFn: () => api.get<Childcare>('/api/plan/childcare'),
  })
}

export function useMortgage() {
  return useQuery({
    queryKey: ['plan', 'mortgage'],
    queryFn: () => api.get<MortgageView>('/api/plan/mortgage'),
  })
}

export function useRenewals() {
  return useQuery({
    queryKey: ['plan', 'renewals'],
    queryFn: () => api.get<Renewals>('/api/plan/renewals'),
  })
}
