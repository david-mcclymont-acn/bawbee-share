import { Link } from 'react-router'
import { useAuth } from '../auth.tsx'
import {
  IconBank,
  IconChevron,
  IconLedger,
  IconPlan,
  IconSettings,
  IconStatement,
  IconVersions,
} from '../icons.tsx'
import { PageHeading } from '../ui.tsx'

const SCREENS = [
  {
    to: '/plan',
    title: 'Plan',
    note: 'Forecast, compare, childcare, mortgage, take-home, renewals',
    Icon: IconPlan,
  },
  {
    to: '/transactions',
    title: 'Transactions',
    note: 'Bank exports, imports and matching',
    Icon: IconStatement,
  },
  {
    to: '/regular',
    title: 'Regular payments',
    note: 'Bills the bank sees, in the budget or not',
    Icon: IconLedger,
  },
  { to: '/banks', title: 'Banks', note: 'Bank links, syncs and consents', Icon: IconBank },
  {
    to: '/versions',
    title: 'Versions',
    note: 'What-ifs, periods and promotion',
    Icon: IconVersions,
  },
  {
    to: '/settings',
    title: 'Settings',
    note: 'Take-home pay, split, sign-in, Home Assistant',
    Icon: IconSettings,
  },
]

/** The phone's fifth dock slot: the screens that don't need to be at the thumb. */
export default function More() {
  const { me, signOut } = useAuth()
  return (
    <>
      <PageHeading title="More" />
      <ul className="more-list">
        {SCREENS.map(({ to, title, note, Icon }) => (
          <li key={to}>
            <Link to={to} className="more-link">
              <Icon />
              <span className="more-what">
                <span className="more-title">{title}</span>
                <span className="more-note">{note}</span>
              </span>
              <IconChevron size={18} />
            </Link>
          </li>
        ))}
      </ul>
      <div className="more-foot">
        <span>Signed in as {me?.display_name}</span>
        <button type="button" className="button" onClick={() => void signOut()}>
          Sign out
        </button>
      </div>
    </>
  )
}
