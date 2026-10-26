import type { ReactNode } from 'react'
import { inkFor, shortName, type AccountLike } from '../../accounts.ts'

/** An account's name in its own ink (DESIGN.md: ink plus name, never ink alone). */
export function AccountName({
  account,
  children,
}: {
  account: (AccountLike & { name: string }) | undefined
  children?: ReactNode
}) {
  if (!account) return <span>{children}</span>
  return (
    <span className="plan-account" data-ink={inkFor(account)}>
      {children ?? shortName(account.name)}
    </span>
  )
}
