// Each account's denomination ink (DESIGN.md "One Ink Per Account"). The ink follows the
// account's role and owner, so it holds for whatever the accounts API returns.

export type AccountInk = 'joint' | 'person_1' | 'person_2' | 'subs' | 'savings' | 'invest'

export type AccountLike = { role: string; owner: string | null }

export function inkFor(account: AccountLike | undefined): AccountInk | undefined {
  if (!account) return undefined
  switch (account.role) {
    case 'joint_bills':
      return 'joint'
    case 'salary_hub':
      return account.owner === 'person_2'
        ? 'person_2'
        : account.owner === 'person_1'
          ? 'person_1'
          : undefined
    case 'subscriptions':
      return 'subs'
    case 'savings':
      return 'savings'
    case 'investment':
      return 'invest'
    default:
      return undefined // dormant accounts carry no ink
  }
}

/** A short name for an account in running text: "Joint Billing (\"Future…" -> "Joint Billing". */
export function shortName(name: string): string {
  return name.replace(/\s*\(.*\)\s*$/, '')
}

/** "person_1" -> "Person 1". People are keyed by a lower-case slug. */
export function personName(key: string | null | undefined): string {
  if (!key) return 'Joint'
  const spaced = key.replace(/_/g, ' ')
  return spaced[0]!.toUpperCase() + spaced.slice(1)
}
