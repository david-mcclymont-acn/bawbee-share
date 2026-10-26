import { lazy, Suspense } from 'react'
import { Navigate, NavLink, useParams } from 'react-router'
import { Loading, PageHeading } from '../ui.tsx'

const TABS = [
  {
    slug: 'forecast',
    label: 'Forecast',
    lead: 'The next 36 months, path by path: what’s left over, what each account holds, and when things change.',
    Tab: lazy(() => import('./plan/Forecast.tsx')),
  },
  {
    slug: 'compare',
    label: 'Compare',
    lead: 'Two to four versions side by side: every line, the transfers, and the joint split under each basis.',
    Tab: lazy(() => import('./plan/Compare.tsx')),
  },
  {
    slug: 'childcare',
    label: 'Childcare',
    lead: 'Each work pattern against each nursery: pay follows hours, the nursery bill follows days.',
    Tab: lazy(() => import('./plan/Childcare.tsx')),
  },
  {
    slug: 'mortgage',
    label: 'Mortgage',
    lead: 'What’s owed, when the fix ends, and what a new rate would cost.',
    Tab: lazy(() => import('./plan/Mortgage.tsx')),
  },
  {
    slug: 'take-home',
    label: 'Take-home',
    lead: 'Pay worked out from gross with this tax year’s Scottish rates, and checked against what lands.',
    Tab: lazy(() => import('./plan/TakeHome.tsx')),
  },
  {
    slug: 'renewals',
    label: 'Renewals',
    lead: 'Policies that renew on a date, so there’s time to shop around.',
    Tab: lazy(() => import('./plan/Renewals.tsx')),
  },
] as const

/** Plan: looking ahead (Phases 6 and 7). One heading, a tab per job. */
export default function Plan() {
  const { tab } = useParams()
  const current = TABS.find((t) => t.slug === tab)
  if (!current) return <Navigate to="/plan/forecast" replace />
  const { Tab } = current
  return (
    <>
      <PageHeading title="Plan" lead={current.lead} />
      <nav className="plan-tabs" aria-label="Plan">
        {TABS.map((t) => (
          <NavLink key={t.slug} to={`/plan/${t.slug}`} className="plan-tab">
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Suspense fallback={<Loading what={current.label.toLowerCase()} />}>
        <Tab />
      </Suspense>
    </>
  )
}
