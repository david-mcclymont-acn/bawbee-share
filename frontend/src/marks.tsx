import type { AccountInk } from './accounts.ts'
import { rosettePaths, type RosetteSpec } from './guilloche.ts'

/** The bawbee: a beaded coin rim around a crowned thistle, as on the James V coins. */
export function CoinMark({ size = 40 }: { size?: number }) {
  const beads = Array.from({ length: 28 }, (_, i) => {
    const a = (i / 28) * Math.PI * 2
    return [24 + 20.6 * Math.cos(a), 24 + 20.6 * Math.sin(a)] as const
  })
  return (
    <svg className="coin" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <circle cx="24" cy="24" r="23" className="coin-face" />
      <circle cx="24" cy="24" r="18.6" className="coin-line" />
      {beads.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="0.85" className="coin-ink" />
      ))}
      {/* crown */}
      <path
        className="coin-line"
        d="M18.5 15.2 L19.6 11.6 L22 14 L24 10.6 L26 14 L28.4 11.6 L29.5 15.2 Z"
      />
      {/* thistle head */}
      <path
        className="coin-line"
        d="M19.4 21.6 C19.4 17.4 28.6 17.4 28.6 21.6 C28.6 25.8 26.2 28 24 28 C21.8 28 19.4 25.8 19.4 21.6 Z"
      />
      <path
        className="coin-hatch"
        d="M20.6 19.2 L27 25.6 M20 22.4 L25.2 27.6 M23 18.4 L28.2 23.6 M27.4 19.2 L21 25.6 M28 22.4 L22.8 27.6 M25 18.4 L19.8 23.6"
      />
      {/* stem and leaves */}
      <path
        className="coin-line"
        d="M24 28 L24 37.4 M24 33.6 C21.4 33.2 19.4 31.4 18.2 29.2 M24 32 C26.6 31.6 28.6 29.8 29.8 27.6"
      />
    </svg>
  )
}

// Each account's rosette has its own weave, so the geometry identifies it as well as the ink.
const ROSETTES: Record<AccountInk, RosetteSpec> = {
  joint: { rings: 9, lobes: 12, amp: 0.06, radius: 0.33, harmonic: { lobes: 3, amp: 0.025 } },
  person_1: { rings: 8, lobes: 8, amp: 0.07, radius: 0.32 },
  person_2: { rings: 10, lobes: 10, amp: 0.05, radius: 0.34, harmonic: { lobes: 5, amp: 0.03 } },
  subs: { rings: 8, lobes: 14, amp: 0.045, radius: 0.35, harmonic: { lobes: 2, amp: 0.04 } },
  savings: { rings: 9, lobes: 6, amp: 0.075, radius: 0.3 },
  invest: { rings: 8, lobes: 16, amp: 0.035, radius: 0.36, harmonic: { lobes: 4, amp: 0.025 } },
}

export function Rosette({ ink, size = 48 }: { ink: AccountInk; size?: number }) {
  const spec = ROSETTES[ink]
  const inner: RosetteSpec = { rings: 5, lobes: spec.lobes, amp: 0.03, radius: 0.15 }
  return (
    <svg className="rosette" width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      {[...rosettePaths(100, spec), ...rosettePaths(100, inner)].map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  )
}
