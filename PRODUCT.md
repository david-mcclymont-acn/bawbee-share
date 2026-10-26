# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

Mobile-first responsive web app, used on laptop and phone browsers. It's an installable PWA so it can sit on a phone home screen. Offline support isn't required.

## Users

Two people: Person 1 and Person 2, a couple in Scotland who run one household's money together. Each has a separate login with full access, and every change is attributed to whoever made it.

- **Person 1** runs the system. They maintain the budget, model the what-ifs, link their own bank accounts, and approve price changes and new bills. They mostly use a laptop for setup and comparisons, and their phone on payday.
- **Person 2** is a full participant. They link and re-link their own RBS accounts and check what they need to transfer on payday, mostly on their phone.

The recurring jobs: on payday, find out exactly what to move between accounts and tick it off; clear the inbox of price changes and new recurring payments; and occasionally plan ahead ("what does our month look like when Person 2 goes back to work three days a week?").

## Product Purpose

A Wee Bawbee replaces the Excel workbook the household currently runs on ("Standard Finances" and "Maternity Finances"). It models how this household actually handles money:

- Each person is paid into their own account.
- Each moves a calculated share into a joint bills account, rounded up to the nearest £5 plus a £5 buffer.
- Person 1 runs a separate account for subscriptions.
- Alternative versions of the whole budget are kept for different stages of life.

Success means that:

- the spreadsheet is retired
- payday transfers come from the app alone
- price rises and new recurring payments get noticed and approved instead of slipping by
- what-if budgets stay in sync with the live budget instead of drifting apart as copy-pasted sheets

## Positioning

No off-the-shelf budgeting app models this household. What makes this one different:

- **Budget versions.** A what-if stores only its differences from the live budget, so changes to the live budget flow through, and a what-if is promoted to live when that stage of life arrives.
- **Calculated joint split.** The joint contribution is calculated from a split basis set per version: gross pay, take-home, surplus or a fixed percentage.
- **Inbox approval.** Bank transactions are inferred into bills, and nothing changes the budget without approval in the inbox.

It also absorbs the household's own childcare calculator and tracks the mortgage, so the whole household's money sits in one place.

## Operating Context

- **Payday ritual.** Person 1 is paid on the last working day of the month (Scottish bank holidays). Person 2 is officially paid on the last Thursday, but it usually arrives the Wednesday before. Once both salaries land, each person makes their transfers from a checklist that shows the suggested and actual amounts and ticks itself from bank data.
- **Accounts:**
  - Person 1 Monzo (salary hub)
  - Person 1 Starling (subscriptions)
  - Person 2 RBS (salary hub)
  - Starling joint "Future Billing" (joint bills)
  - savings and Monzo Investment
- **Line types.** Every budget line is a bill, an allocation or a transfer. Labels follow `Name (Provider)`.
- **Hosting.** Self-hosted with Docker Compose on the household's home VMs, behind nginx.
- **Sign-in.** Each person has a local username and password by default. Signing in through the household's authentik is optional, and the app must never depend on it.
- **Current state.** The "Maternity" budget is live. "Back to normal" is a what-if. A remortgage (Northfield fix ends Nov 2027) and the end of a car PCP (around Oct 2027) are both coming in autumn 2027.

## Capabilities and Constraints

- All money is shown in GBP and held as integer pence. Money is always shown with tabular figures and is never rounded silently. The only rounding is the explicit £5 ceilings.
- Bank access is read-only. Payment initiation is out of scope.
- Planned capabilities, in phase order:
  - budget versions and promotion
  - payday checklist
  - CSV import and the approval inbox
  - open banking sync
  - recurring detection and alerts
  - 36-month forecast and comparison
  - childcare calculator
  - mortgage tracking
- Out of scope: multi-household or SaaS use, investment and pension valuation, credit scores, tax returns, multi-currency.

## Brand Commitments

- **Name.** "A Wee Bawbee". The short name is "Bawbee", used in the PWA, mobile nav and browser tab. It comes from the Scottish nursery rhyme "Coulter's Candy" ("Ally Bally Bee"), in which a bairn is greetin' for a wee bawbee, an old Scots halfpenny.
- **Voice.** Warm, a bit cheeky and quietly Scottish, but money is always presented seriously and clearly.
- **Where personality is allowed:**
  - small touches in empty states, success messages and the logo (e.g. a simple bawbee coin mark)
  - the occasional light Scots word in microcopy, e.g. "Aw done!" when the payday checklist is complete
- **Where it isn't:** warnings, figures and anything involving bank connections stay plain and clear.
- **The look: "Clear", Apple-native** (chosen October 2026, replacing the first "Scottish banknote" look, which read as dated). It should feel like a first-party iPhone app: a soft grey ground with grouped white cards, the platform's own font, frosted glass chrome (a floating glass tab bar on phones), one blue accent, and each account in its own colour. Modern and sleek, never ornamented.
- **Explicitly rejected:**
  - a generic SaaS dashboard look (grids of metric tiles and charts)
  - engraving, guilloche and other period ornament
  - purple gradients
  - card soup: sections are grouped lists that stack, not grids of boxed cards
  - emoji icons
  - default component-library styling
- **Theme.** Light and dark mode are both required.

## Evidence on Hand

- `seed/budget-seed.json`: a made-up example budget in the spreadsheet's format, with the outputs the engine produces for it. It is example data, safe to share and to use in screenshots.
- `reference/childcare-app/`: the existing childcare calculator and an example `state.json`.
- Example figures in PLAN.md §3 and §10.
- There are no logo or brand assets yet. The bawbee coin mark still has to be designed.

## Product Principles

1. **Tell us what to do first.** Payday and the inbox come before charts and analysis.
2. **Figures are sacred.** Every number is exact, traceable to its inputs, and never hard-coded.
3. **Nothing changes without a yes.** Bank data proposes and a person approves.
4. **One source of truth.** What-ifs inherit from live, and shared values (e.g. the mortgage payment) live in one place.
5. **Personality at the edges.** Warmth lives in empty states and success moments, never in warnings or money.

## Accessibility & Inclusion

WCAG 2.2 AA. Neither user has specific needs beyond that. Meet standard contrast, support keyboard and screen readers, respect `prefers-reduced-motion` and OS text-size settings, use touch-sized targets, and avoid hover-only interactions.
