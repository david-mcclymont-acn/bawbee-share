---
target: Phase 3 screens
total_score: 25
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 2
target_identity: "file:C:\\dev\\a-wee-bawbee\\frontend\\src\\pages"
timestamp: 2026-10-20T12-53-41Z
slug: frontend-src-pages
closed: true
---
# Critique: Phase 3 screens (Inbox, Transactions, More, Budget actuals, nav)

Method: dual-agent (A: design review · B: detector + Playwright overlay)

## Design Health Score: 25/40 (Acceptable)
| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of status | 2 | Not a change / Snooze vanish silently; focus lost after Approve; decision panel not announced |
| 2 | Real world | 3 | Mostly plain; "sets its own amount", "Amount (money out negative)" |
| 3 | User control | 2 | No undo on Approve/Not a change/Snooze; can't clear a wrong match |
| 4 | Consistency | 2 | Inbox strip invents a pattern; ok green ~ Person 2 ink; categorised rows still say Match |
| 5 | Error prevention | 3 | Import strong; Approve rewrites live with no preview of consequences |
| 6 | Recognition | 3 | Budget "Not seen yet" doesn't link to the unmatched payment |
| 7 | Flexibility | 2 | Rules + URL filters; no bulk, no suggestions, search on blur only |
| 8 | Minimalist | 3 | Green "Paid" everywhere; repeated follow-up notes; zero-heavy import summary |
| 9 | Error recovery | 3 | Specific, actionable messages |
| 10 | Help | 2 | Nothing explains Keep/Take consequences |

## Design specificity
Voice and shell authored; page bodies drift generic. Inbox strip uses a side-tab border instead of the register strip (rosette, inked title, denomination band). Detector: target files clean; tree scan 1 finding (side-tab, index.css:1367). Overlay: 9 findings / 6 rules; real = side-tab, placeholder contrast 4.3:1; false positives = cramped-padding on add-line, ai-color-palette on ink-subs, colophon edge, budget column lengths.

## Priority issues
- [P1] Budget shows overpayments as green "Paid" (actuals never returns `over` for bills); green ~ Person 2 ink. Fix: `over` for bills with the difference; quiet ink for as-planned. colorize, clarify.
- [P1] Approve: no visible consequences, no undo, focus lost, what-if decisions only in client state. Fix: show Joint Billing delta on strip; decisions in place with focus; persist pending decisions; Undo. harden.
- [P2] Inbox badge never empties (follow-ups + reminders counted). Fix: badge counts bank proposals; chores section. distill.
- [P2] Register-strip vocabulary dropped (side-tab; ungrouped 43 rows). Fix: shared register strip; day headers. extract, polish.
- [P2] Transactions triage: categorised counted as unmatched; 28px Match/Change; no unmatch; placeholder contrast. Fix: three states, 44px buttons, Unmatch, ::placeholder colour. clarify, adapt.

## Persona red flags
Alex: one-at-a-time clearing, no suggested matches, summary count not a link. Sam: repeated button names, lost focus, aria-current dropped on More. Casey: four filters above list, 28px targets, off-screen decisions.

## Minor
Delta always ochre even for drops; repeated spreadsheet note; "Inbox clear." lost its Scots warmth; Keep pre-selected; stale CSS comment at index.css:1761.

## Questions
Approve where consequences are visible? Inbox as decisions or chores? Does "paid as planned" need colour?
