// Line icons drawn for this app: one stroke weight, square ends, outlined rather than filled.

type IconProps = { size?: number }

function Icon({ size = 22, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

/** Dashboard: a banknote with its seal (money at a glance). */
export const IconNote = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="6" width="18" height="12" />
    <circle cx="12" cy="12" r="2.6" />
    <path d="M6 9v6M18 9v6" />
  </Icon>
)

/** Payday: money moving across. */
export const IconPayday = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 8h13M14 5l3 3-3 3" />
    <path d="M20 16H7M10 13l-3 3 3 3" />
  </Icon>
)

/** Budget: a ruled ledger. */
export const IconLedger = (p: IconProps) => (
  <Icon {...p}>
    <rect x="4" y="3" width="16" height="18" />
    <path d="M8 3v18M11 8h6M11 12h6M11 16h4" />
  </Icon>
)

/** Plan: a line running on into the months ahead, past a marker. */
export const IconPlan = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 20h18" />
    <path d="M4 16l5-5 4 3 7-8" />
    <path d="M15 4v16" strokeDasharray="2 2" />
  </Icon>
)

/** Versions: stacked sheets. */
export const IconVersions = (p: IconProps) => (
  <Icon {...p}>
    <path d="M7 4h13v13" />
    <path d="M4 7h13v13H4z" />
  </Icon>
)

/** Settings: sliders. */
export const IconSettings = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 7h16M4 17h16" />
    <path d="M9 4v6M15 14v6" />
  </Icon>
)

/** Inbox: a tray with a slip waiting in it. */
export const IconInbox = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 13l3-8h12l3 8v6H3z" />
    <path d="M3 13h5l1 2.5h6l1-2.5h5" />
    <path d="M9 9h6" />
  </Icon>
)

/** Transactions: a statement, lines with figures to the right. */
export const IconStatement = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 3h14v18l-2.3-1.5L14.3 21 12 19.5 9.7 21l-2.4-1.5L5 21z" />
    <path d="M8 8h4M15 8h1M8 12h4M15 12h1M8 16h4M15 16h1" />
  </Icon>
)

/** Banks: a bank front, columns under a pediment. */
export const IconBank = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 9l9-5 9 5z" />
    <path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18" />
  </Icon>
)

/** More: the rest of the screens. */
export const IconMore = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Icon>
)

export const IconCopy = (p: IconProps) => (
  <Icon {...p}>
    <rect x="8" y="8" width="12" height="12" />
    <path d="M16 8V4H4v12h4" />
  </Icon>
)

export const IconTick = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Icon>
)

export const IconChevron = ({ dir = 'right', ...p }: IconProps & { dir?: 'left' | 'right' }) => (
  <Icon {...p}>
    <path d={dir === 'left' ? 'M14.5 6l-6 6 6 6' : 'M9.5 6l6 6-6 6'} />
  </Icon>
)
