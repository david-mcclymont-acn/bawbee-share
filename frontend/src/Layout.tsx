import { Link, NavLink, Outlet, useLocation } from 'react-router'
import { useHealth, useInboxCount } from './api.ts'
import { useAuth } from './auth.tsx'
import { IconInbox, IconLedger, IconMore, IconNote, IconPayday } from './icons.tsx'
import { CoinMark } from './marks.tsx'

const BAND = [
  { to: '/payday', label: 'Payday' },
  { to: '/inbox', label: 'Inbox' },
  { to: '/', label: 'Dashboard', end: true },
  { to: '/budget', label: 'Budget' },
  { to: '/plan', label: 'Plan' },
  { to: '/transactions', label: 'Transactions' },
  { to: '/banks', label: 'Banks' },
  { to: '/versions', label: 'Versions' },
  { to: '/settings', label: 'Settings' },
]

// The thumb gets the four screens used most; the rest sit behind More.
const DOCK = [
  { to: '/payday', label: 'Payday', Icon: IconPayday },
  { to: '/inbox', label: 'Inbox', Icon: IconInbox },
  { to: '/', label: 'Dashboard', Icon: IconNote, end: true },
  { to: '/budget', label: 'Budget', Icon: IconLedger },
  { to: '/more', label: 'More', Icon: IconMore },
]
const MORE = ['/more', '/plan', '/transactions', '/regular', '/banks', '/versions', '/settings']

function Count({ to }: { to: string }) {
  const count = useInboxCount()
  const n = count.data?.count ?? 0
  if (to !== '/inbox' || n === 0) return null
  return (
    <span className="nav-count">
      {n}
      <span className="visually-hidden"> waiting</span>
    </span>
  )
}

function ApiDown() {
  const health = useHealth()
  if (!health.isError) return null
  return (
    <p className="api-status" data-state="down" role="status">
      <span className="api-status-dot" aria-hidden="true" />
      Can’t reach the server. Changes won’t save until it’s back.
    </p>
  )
}

export function Band({ children }: { children?: React.ReactNode }) {
  return (
    <header className="band">
      <div className="band-inner">
        <NavLink className="wordmark" to="/" aria-label="A Wee Bawbee, dashboard">
          <CoinMark />
          <span className="wordmark-full">A Wee Bawbee</span>
          <span className="wordmark-short">Bawbee</span>
        </NavLink>
        {children}
      </div>
    </header>
  )
}

export default function Layout() {
  const { me, signOut } = useAuth()
  const { pathname } = useLocation()
  const inMore = MORE.some((p) => pathname.startsWith(p))
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Band>
        <nav className="nav-band" aria-label="Main">
          {BAND.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end} className="nav-band-link">
              {label}
              <Count to={to} />
            </NavLink>
          ))}
        </nav>
        <div className="band-user">
          <span>{me?.display_name}</span>
          <button type="button" className="band-button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </Band>
      <div className="band-alert">
        <ApiDown />
      </div>
      <main id="main" className="sheet" tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="colophon">
        <p>Read-only bank access. Every figure exact to the penny.</p>
      </footer>
      <nav className="nav-dock" aria-label="Main">
        {DOCK.map(({ to, label, Icon, end }) =>
          // More stands for several screens, so it marks itself current on any of them
          // (NavLink would only do that on /more itself).
          to === '/more' ? (
            <Link
              key={to}
              to={to}
              className={`nav-dock-link${inMore ? ' active' : ''}`}
              aria-current={inMore ? 'page' : undefined}
            >
              <Icon />
              <span>{label}</span>
            </Link>
          ) : (
            <NavLink key={to} to={to} end={end} className="nav-dock-link">
              <Icon />
              <span>
                {label}
                <Count to={to} />
              </span>
            </NavLink>
          ),
        )}
      </nav>
    </>
  )
}
