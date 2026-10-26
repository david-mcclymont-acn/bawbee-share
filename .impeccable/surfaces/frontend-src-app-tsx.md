---
version: 1
slug: "frontend-src-app-tsx"
primary_target: "frontend/src/App.tsx"
related_targets: ["frontend/src/index.css","frontend/index.html"]
---

## Scope

App shell and Phase 0 placeholder page (`frontend/src/App.tsx`). Mode: Operate. The audience is Person 1 and Person 2, on laptop and phone. Job: confirm the app is up, and preview the account colour system that later screens inherit. The placeholder has no figures yet. Its only live data is the API health check.

## Direction contract

THESIS: Money set like a Scottish banknote. Each account carries its own denomination colour, and every figure is printed with intaglio precision. This page refuses the grey fintech card grid with a single accent.

OWN-WORLD: Security-paper ground (cool grey-green, never cream) in ink near-black. Intaglio note-blue is the shell colour. Accounts take denomination inks: Joint in note blue, Person 1's hub in £10 sienna, Person 2's RBS in £50 green, Person 1's subscriptions in a muted violet ink, savings in slate, and Monzo Investment in deep teal. Fine authored SVG guilloche line work identifies each account: every account has its own rosette weave as well as its own ink. A 19th-century Modern serif (Old Standard TT) carries the wordmark and headings, and Public Sans with tabular figures carries the UI and money. There are hairline intaglio rules, no shadows on the paper, and no rounded card shells.

STORY: The visitor sees that it's theirs and that it's working: the name and coin mark, the API status, and the six accounts as notes in circulation. Personality stays at the edges.

FIRST VIEWPORT: A full-width note-blue header band with a guilloche strip on its lower edge. The bawbee coin mark and wordmark sit at left, and the API status sits at right as a plain legend item. Below it, on the paper, comes a Modern-serif heading with one line of plain copy. Then the account register: one full-width strip per account, each with a guilloche rosette and denomination band in its ink, the account name and its role. On mobile the strips stack and the header stays compact. There is no primary action yet.

FORM: Scottish banknote, IMPECCABLE'S PICK (position 1 on the grounded list); seed key eeb5510c.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved

- Exact ink values are to be confirmed against WCAG AA in both themes during the build.
- The coin mark is a first authored version, to be refined when the logo gets proper attention.
