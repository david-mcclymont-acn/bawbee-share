// Guilloche line work, generated as SVG path data. Banknotes build these from
// overlapping periodic curves; we do the same so the pattern stays crisp at any size.

const TAU = Math.PI * 2

export type RosetteSpec = {
  /** Number of phase-shifted rings woven together. */
  rings: number
  /** Petals per ring. */
  lobes: number
  /** Petal depth as a fraction of the mark size. */
  amp: number
  /** Ring radius as a fraction of the mark size. */
  radius: number
  /** Second harmonic, which gives each account's rosette its own weave. */
  harmonic?: { lobes: number; amp: number }
}

/**
 * A rosette: closed curves r(t) = radius + amp·sin(lobes·t + phase) (+ a harmonic), each
 * ring phase-shifted so neighbouring curves cross and form the woven look.
 */
export function rosettePaths(size: number, spec: RosetteSpec, steps = 480): string[] {
  const c = size / 2
  return Array.from({ length: spec.rings }, (_, ring) => {
    const phase = (ring / spec.rings) * (TAU / spec.lobes)
    let d = ''
    for (let s = 0; s < steps; s++) {
      const t = (s / steps) * TAU
      let r = spec.radius * size + spec.amp * size * Math.sin(spec.lobes * t + phase)
      if (spec.harmonic) {
        r += spec.harmonic.amp * size * Math.cos(spec.harmonic.lobes * t - phase * 2)
      }
      d += `${s === 0 ? 'M' : 'L'}${(c + r * Math.cos(t)).toFixed(2)} ${(c + r * Math.sin(t)).toFixed(2)}`
    }
    return d + 'Z'
  })
}
