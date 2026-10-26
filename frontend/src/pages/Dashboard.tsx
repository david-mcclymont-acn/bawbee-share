import { Link } from 'react-router'
import {
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { inkFor, personName } from '../accounts.ts'
import {
  useAccounts,
  useInbox,
  usePayday,
  useProjection,
  useSummary,
  useVersions,
  type Inbox,
  type Projection,
} from '../api.ts'
import { useAuth } from '../auth.tsx'
import {
  formatDay,
  formatLongDate,
  formatMonth,
  formatMonthYear,
  isoDate,
  parseDate,
  today,
} from '../dates.ts'
import { formatPence } from '../money.ts'
import { useForecast, type ForecastRun } from '../plan.ts'
import { ErrorText, Loading, Money, Notice, PageHeading, SectionHeading } from '../ui.tsx'

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`
}

/** The first thing waiting, by name: "TV (Sky) went up from £104.75 to £108.50." */
function InboxLead({ inbox }: { inbox: Inbox }) {
  const [first, ...rest] = inbox.items
  if (!first) return null
  const more = rest.length > 0 ? ` And ${plural(rest.length, 'more', 'more')}.` : ''
  if (first.kind === 'consent_expiring') {
    const ended =
      first.expired ||
      (first.expires_on !== null &&
        first.expires_on !== undefined &&
        first.expires_on < isoDate(today()))
    return (
      <>
        Your {first.bank} link{' '}
        {ended ? 'has ended' : `ends ${first.expires_on ? formatDay(first.expires_on) : 'soon'}`}.
        Re-link it from Banks.{more}
      </>
    )
  }
  if (first.kind === 'new_recurring') {
    return (
      <>
        A new regular payment to {first.merchant}, <Money pence={first.amount_pence ?? 0} />{' '}
        {first.cadence === 'weekly'
          ? 'a week'
          : first.cadence === 'annual'
            ? 'a year'
            : first.cadence === 'quarterly'
              ? 'a quarter'
              : 'a month'}
        .{more}
      </>
    )
  }
  if (first.kind === 'missed_payment') {
    return (
      <>
        {first.label} hasn’t been paid this month.{more}
      </>
    )
  }
  if (first.kind === 'still_paying') {
    return (
      <>
        {first.label} is still being paid, though it’s marked to cancel.{more}
      </>
    )
  }
  if (first.kind === 'goal_adjust') {
    return (
      <>
        {first.name} needs <Money pence={first.needed_pence ?? 0} /> a month, not{' '}
        <Money pence={first.current_pence ?? 0} />.{more}
      </>
    )
  }
  if (first.kind === 'renewal_due') {
    return (
      <>
        {first.name} renews {formatDay(first.renews_on ?? '')}: time to compare prices.{more}
      </>
    )
  }
  if (first.kind === 'remortgage_window') {
    return (
      <>
        The {first.lender} fix ends {formatMonthYear(first.fix_end ?? '')}: time to look at
        remortgaging.{more}
      </>
    )
  }
  if (first.kind === 'agreement_ending') {
    return (
      <>
        {first.label} ends {formatMonthYear(first.end_month ?? '')}.{more}
      </>
    )
  }
  if (first.kind === 'salary_mismatch') {
    return (
      <>
        {first.name}’s pay landed at <Money pence={first.landed_pence ?? 0} />, not{' '}
        <Money pence={first.expected_pence ?? 0} />.{more}
      </>
    )
  }
  if (first.status === 'approved') {
    return (
      <>
        Choose what {first.pending.map((w) => w.name).join(' and ')} does with the new {first.label}{' '}
        price.{more}
      </>
    )
  }
  const old = first.old_pence ?? 0
  const now = first.new_pence ?? 0
  return (
    <>
      {first.label} went {now > old ? 'up' : 'down'} from <Money pence={old} /> to{' '}
      <Money pence={now} />.{more}
    </>
  )
}

/** The days up to the next payday: after that the account is topped up again. */
function untilNextPayday(projection: Projection) {
  const landed = projection.days.findIndex(
    (d, i) => i > 0 && d.closing_pence > projection.days[i - 1]!.closing_pence,
  )
  return landed > 0 ? projection.days.slice(0, landed) : projection.days
}

function JointChart({ projection }: { projection: Projection }) {
  const days = untilNextPayday(projection)
  const data = days.map((d) => ({
    day: parseDate(d.day).getDate(),
    pounds: d.closing_pence / 100,
  }))
  const lowest = parseDate(projection.minimum_day).getDate()
  return (
    <div className="chart" aria-hidden="true">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="day"
            tickLine={false}
            axisLine={{ stroke: 'var(--rule-strong)' }}
            tick={{ fill: 'var(--ink-muted)', fontSize: 12 }}
            interval="preserveStartEnd"
            minTickGap={16}
          />
          <YAxis
            width={64}
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--ink-muted)', fontSize: 12 }}
            tickFormatter={(v: number) => `£${Math.round(v).toLocaleString('en-GB')}`}
          />
          <ReferenceLine y={0} stroke="var(--danger)" strokeDasharray="3 3" />
          <Tooltip
            cursor={{ stroke: 'var(--rule-strong)' }}
            contentStyle={{
              background: 'var(--paper)',
              border: '1px solid var(--rule-strong)',
              borderRadius: 0,
              color: 'var(--ink)',
            }}
            formatter={(v) => [formatPence(Math.round(Number(v) * 100)), 'Balance']}
            labelFormatter={(d) => `Day ${String(d)}`}
          />
          <ReferenceDot
            x={lowest}
            y={projection.minimum_pence / 100}
            r={4}
            fill="var(--paper)"
            stroke="var(--ink-joint)"
            strokeWidth={1.5}
          />
          <Line
            type="stepAfter"
            dataKey="pounds"
            stroke="var(--ink-joint)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

/** The default forecast path, small: household surplus over the next 36 months. */
function Timeline({ run }: { run: ForecastRun }) {
  const data = run.months.map((m) => ({ month: m.month, pounds: m.household_pence / 100 }))
  const next = run.months.find((m) => m.events.length > 0)
  const last = run.months[run.months.length - 1]
  return (
    <section className="register" aria-labelledby="ahead-title">
      <SectionHeading
        id="ahead-title"
        title="The next three years"
        aside={<Link to="/plan/forecast">Open the forecast</Link>}
      />
      <p className="chart-summary">
        On <strong>{run.name}</strong>, the household has{' '}
        <Money pence={run.months[0]?.household_pence ?? 0} /> left over next month
        {last && (
          <>
            {' '}
            and <Money pence={last.household_pence} /> by {formatMonth(last.month)}{' '}
            {last.month.slice(0, 4)}
          </>
        )}
        .
        {next && (
          <>
            {' '}
            Next change: {next.events[0]} in {formatMonth(next.month)} {next.month.slice(0, 4)}.
          </>
        )}
      </p>
      <div className="chart" aria-hidden="true">
        <ResponsiveContainer width="100%" height={120}>
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <XAxis dataKey="month" hide />
            <YAxis hide domain={['auto', 'auto']} />
            <ReferenceLine y={0} stroke="var(--danger)" strokeDasharray="3 3" />
            {run.months
              .filter((m) => m.events.length > 0)
              .map((m) => (
                <ReferenceLine
                  key={m.month}
                  x={m.month}
                  stroke="var(--rule-strong)"
                  strokeWidth={0.75}
                />
              ))}
            <Line
              type="stepAfter"
              dataKey="pounds"
              stroke="var(--ink)"
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

export default function Dashboard() {
  const { me } = useAuth()
  const versions = useVersions()
  const live = versions.data?.find((v) => v.type === 'live')
  const summary = useSummary(live?.id)
  const accounts = useAccounts()
  const now = today()
  const [year, month] = [now.getFullYear(), now.getMonth() + 1]
  const payday = usePayday(year, month)
  const projection = useProjection(live?.id, year, month)
  const inbox = useInbox()
  const forecast = useForecast(undefined)

  if (versions.isPending || summary.isPending) return <Loading what="the dashboard" />
  if (summary.isError) return <ErrorText error={summary.error} />
  const s = summary.data
  const hub = (person: string) =>
    (accounts.data ?? []).find((a) => a.role === 'salary_hub' && a.owner === person)
  const joint = (accounts.data ?? []).find((a) => a.role === 'joint_bills')
  const pay = payday.data
  const done = pay?.transfers.filter((t) => t.done_at).length ?? 0
  const p = projection.data

  return (
    <>
      <PageHeading
        title={me?.display_name ? `Hello, ${me.display_name}` : 'Dashboard'}
        lead={
          <>
            Live budget: <strong>{s.version.name}</strong>
            {s.version.period_start && <>, since {formatLongDate(s.version.period_start)}</>}.
          </>
        }
      />

      <section className="todo" aria-label="To do">
        {pay && (
          <Notice
            tone={pay.complete ? 'ok' : 'info'}
            action={
              <Link className="button" to="/payday">
                Open payday
              </Link>
            }
          >
            <strong className="todo-title">{formatMonth(pay.month)} payday</strong> is on{' '}
            {formatDay(pay.transfer_date)}.{' '}
            {pay.complete
              ? 'Everything’s sent.'
              : `${done} of ${pay.transfers.length} transfers sent.`}
          </Notice>
        )}
        {(inbox.data?.count ?? 0) > 0 && (
          <Notice
            tone="warn"
            action={
              <Link className="button" to="/inbox">
                Open the inbox
              </Link>
            }
          >
            <strong className="todo-title">Inbox</strong> <InboxLead inbox={inbox.data!} />
          </Notice>
        )}
        {(inbox.data?.others_consents ?? []).map((c) => (
          <p className="todo-quiet" key={`${c.owner}-${c.bank}`}>
            {c.owner}’s {c.bank} link{' '}
            {c.expired ? 'has ended' : `ends ${c.expires_on ? formatDay(c.expires_on) : 'soon'}`};
            it’s theirs to re-link.
          </p>
        ))}
        {(inbox.data?.chores_count ?? 0) > 0 && (
          <p className="todo-quiet">
            {plural(inbox.data!.chores_count, 'chore', 'chores')} when you’ve a minute, like the
            spreadsheet’s follow-ups. <Link to="/inbox#chores-title">See chores</Link>
          </p>
        )}
      </section>

      <section className="register" aria-labelledby="surplus-title">
        <SectionHeading
          id="surplus-title"
          title="This month"
          aside={<span>Left over after every transfer and bill</span>}
        />
        <ul className="figure-list">
          {Object.entries(s.surpluses.personal).map(([person, pence]) => (
            <li key={person} data-ink={inkFor(hub(person))}>
              <span className="figure-name">{personName(person)}</span>
              <Money pence={pence} />
            </li>
          ))}
          <li data-ink={inkFor(joint)}>
            <span className="figure-name">Joint Billing</span>
            <Money pence={s.surpluses.joint} />
          </li>
          <li className="figure-total">
            <span className="figure-name">Household</span>
            <Money pence={s.surpluses.household} />
          </li>
        </ul>
        {s.warnings
          .filter((w) => w.code.endsWith('shortfall'))
          .map((w) => (
            <Notice key={w.message} tone="danger">
              {w.message}
            </Notice>
          ))}
      </section>

      {p && (
        <section className="register" aria-labelledby="joint-title" data-ink={inkFor(joint)}>
          <SectionHeading
            id="joint-title"
            title={`Joint Billing through ${formatMonth(p.bill_month)}`}
            aside={
              <span>
                Opens with this payday’s <Money pence={p.opening_pence} />
              </span>
            }
          />
          <p className="chart-summary">
            Lowest point <Money pence={p.minimum_pence} /> on {formatDay(p.minimum_day)}, before the
            next payday tops it up
            {p.goes_negative ? '. The account goes overdrawn before the next payday.' : '.'}
            {p.undated_lines.length > 0 &&
              ` ${p.undated_lines.length} bills have no set day, so they’re counted on the 1st.`}
          </p>
          {p.goes_negative && (
            <Notice tone="danger">
              Joint Billing would be overdrawn by <Money pence={-p.minimum_pence} /> on{' '}
              {formatDay(p.minimum_day)}.
            </Notice>
          )}
          <JointChart projection={p} />
          <details className="chart-table">
            <summary>Show day by day</summary>
            <table>
              <thead>
                <tr>
                  <th scope="col">Day</th>
                  <th scope="col">Movements</th>
                  <th scope="col" className="num">
                    Balance
                  </th>
                </tr>
              </thead>
              <tbody>
                {p.days
                  .filter((d) => d.movements.length > 0)
                  .map((d) => (
                    <tr key={d.day}>
                      <td>{formatDay(d.day)}</td>
                      <td>
                        {d.movements.map((m) => `${m.name} ${formatPence(m.pence)}`).join(', ')}
                      </td>
                      <td className="num">
                        <Money pence={d.closing_pence} />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </details>
        </section>
      )}
      {forecast.data?.runs[0] && <Timeline run={forecast.data.runs[0]} />}
    </>
  )
}
