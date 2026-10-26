import { useState } from 'react'
import { inkFor, personName, shortName } from '../../accounts.ts'
import { useAccounts, type Summary } from '../../api.ts'
import { useCompare, usePlanVersions, type CompareLine } from '../../plan.ts'
import { ErrorText, Loading, Money, Notice, SectionHeading } from '../../ui.tsx'
import { AccountName } from './common.tsx'

const MAX = 4
const BASIS: Record<string, string> = {
  take_home: 'Take-home pay',
  gross: 'Gross pay',
  surplus: 'What’s left after own bills',
  fixed_pct: 'Fixed shares',
}

function Amount({ pence }: { pence: number | undefined }) {
  return pence === undefined ? (
    <span className="plan-none">Not in it</span>
  ) : (
    <Money pence={pence} />
  )
}

function LinesByAccount({ lines, versions }: { lines: CompareLine[]; versions: Summary[] }) {
  const accounts = useAccounts()
  const byKey = new Map((accounts.data ?? []).map((a) => [a.key, a]))
  const groups = [...new Set(lines.map((l) => l.account))]
  const ids = versions.map((v) => String(v.version.id))
  const differing = lines.filter((l) => l.differs).length
  return (
    <section className="register" aria-labelledby="lines-title">
      <SectionHeading
        id="lines-title"
        title="Every line"
        aside={<span>{differing === 0 ? 'No line differs' : `${differing} differ`}</span>}
      />
      <div className="table-wrap">
        <table className="plan-compare">
          <thead>
            <tr>
              <th scope="col">Line</th>
              {versions.map((v) => (
                <th scope="col" className="num" key={v.version.id}>
                  {v.version.name}
                </th>
              ))}
            </tr>
          </thead>
          {groups.map((key) => {
            const account = byKey.get(key)
            return (
              <tbody key={key} data-ink={inkFor(account)}>
                <tr className="plan-compare-account">
                  <th scope="colgroup" colSpan={versions.length + 1}>
                    <AccountName account={account}>{shortName(account?.name ?? key)}</AccountName>
                  </th>
                </tr>
                {lines
                  .filter((l) => l.account === key)
                  .map((l) => (
                    <tr key={l.line_id} data-differs={l.differs || undefined}>
                      <th scope="row">
                        {l.label}
                        {l.computed && <span className="plan-tag">From Childcare</span>}
                        {l.differs && <span className="visually-hidden"> (differs)</span>}
                      </th>
                      {ids.map((id) => (
                        <td className="num" key={id}>
                          <Amount pence={l.amounts[id]} />
                        </td>
                      ))}
                    </tr>
                  ))}
              </tbody>
            )
          })}
        </table>
      </div>
    </section>
  )
}

function Totals({ versions }: { versions: Summary[] }) {
  const people = [...new Set(versions.flatMap((v) => Object.keys(v.surpluses.personal)))]
  const transfers = [
    ...new Map(versions.flatMap((v) => v.transfers.map((t) => [t.line_id, t.name]))).entries(),
  ]
  const bases = [...new Set(versions.flatMap((v) => Object.keys(v.split_options)))]
  const head = (
    <thead>
      <tr>
        <th scope="col">
          <span className="visually-hidden">What</span>
        </th>
        {versions.map((v) => (
          <th scope="col" className="num" key={v.version.id}>
            {v.version.name}
          </th>
        ))}
      </tr>
    </thead>
  )
  return (
    <>
      <section className="register" aria-labelledby="left-title">
        <SectionHeading id="left-title" title="Left over each month" />
        <div className="table-wrap">
          <table className="plan-compare">
            {head}
            <tbody>
              {people.map((p) => (
                <tr key={p}>
                  <th scope="row">{personName(p)}</th>
                  {versions.map((v) => (
                    <td className="num" key={v.version.id}>
                      <Money pence={v.surpluses.personal[p] ?? 0} />
                    </td>
                  ))}
                </tr>
              ))}
              <tr>
                <th scope="row">Joint Billing</th>
                {versions.map((v) => (
                  <td className="num" key={v.version.id}>
                    <Money pence={v.surpluses.joint} />
                  </td>
                ))}
              </tr>
              <tr className="plan-compare-total">
                <th scope="row">Household</th>
                {versions.map((v) => (
                  <td className="num" key={v.version.id}>
                    <Money pence={v.surpluses.household} />
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section className="register" aria-labelledby="transfers-title">
        <SectionHeading id="transfers-title" title="Transfers" />
        <div className="table-wrap">
          <table className="plan-compare">
            {head}
            <tbody>
              {transfers.map(([id, name]) => (
                <tr key={id}>
                  <th scope="row">{name}</th>
                  {versions.map((v) => {
                    const t = v.transfers.find((x) => x.line_id === id)
                    return (
                      <td className="num" key={v.version.id}>
                        <Amount pence={t?.effective_pence} />
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="register" aria-labelledby="split-title">
        <SectionHeading
          id="split-title"
          title="The joint split, every way"
          aside={<span>Each person’s suggested transfer to Joint Billing</span>}
        />
        <div className="table-wrap">
          <table className="plan-compare">
            {head}
            {bases.map((basis) => (
              <tbody key={basis}>
                <tr className="plan-compare-account">
                  <th scope="colgroup" colSpan={versions.length + 1}>
                    {BASIS[basis] ?? basis}
                  </th>
                </tr>
                {people.map((p) => (
                  <tr key={p}>
                    <th scope="row">{personName(p)}</th>
                    {versions.map((v) => (
                      <td
                        className="num"
                        key={v.version.id}
                        data-chosen={v.split_basis === basis || undefined}
                      >
                        <Amount pence={v.split_options[basis]?.[p]} />
                        {v.split_basis === basis && <span className="plan-chosen"> in use</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      </section>
    </>
  )
}

export default function CompareTab() {
  const versions = usePlanVersions()
  const [chosen, setChosen] = useState<number[] | null>(null)
  const defaults = (versions.data ?? []).slice(0, 2).map((v) => v.id)
  const ids = chosen ?? defaults
  const compare = useCompare(ids)
  if (versions.isPending) return <Loading what="versions" />
  if (versions.isError) return <ErrorText error={versions.error} />
  const toggle = (id: number, on: boolean) =>
    setChosen(on ? [...ids, id] : ids.filter((x) => x !== id))
  return (
    <>
      <section className="register" aria-labelledby="pick-title">
        <SectionHeading id="pick-title" title="Versions" aside={<span>Choose two to four</span>} />
        <ul className="plan-paths">
          {versions.data.map((v) => {
            const on = ids.includes(v.id)
            return (
              <li key={v.id}>
                <label className="plan-path-show">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={!on && ids.length >= MAX}
                    onChange={(e) => toggle(v.id, e.target.checked)}
                  />
                  <span className="plan-path-what">
                    <span className="plan-path-name">
                      {v.name}
                      {v.type === 'live' && <span className="plan-tag">Live</span>}
                    </span>
                    <span className="plan-path-note">
                      {v.childcare
                        ? `Childcare: ${v.childcare}`
                        : v.type === 'live'
                          ? 'The budget now'
                          : 'What-if'}
                    </span>
                  </span>
                </label>
              </li>
            )
          })}
        </ul>
      </section>
      {ids.length < 2 ? (
        <Notice>Choose at least two versions to compare.</Notice>
      ) : compare.isPending && !compare.data ? (
        <Loading what="the comparison" />
      ) : compare.isError ? (
        <ErrorText error={compare.error} />
      ) : (
        compare.data && (
          <>
            <Totals versions={compare.data.versions} />
            <LinesByAccount lines={compare.data.lines} versions={compare.data.versions} />
          </>
        )
      )}
    </>
  )
}
