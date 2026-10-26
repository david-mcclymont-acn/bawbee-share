---
target: Phase 4 Banks screen
total_score: 25
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 3
target_identity: "file:C:\\dev\\a-wee-bawbee\\frontend\\src\\pages\\Banks.tsx"
target_fingerprint: "sha256:b4b669188fa808899438b3ae1d890eb7425f42b3851ba9d319eb22645cdb74b6"
target_path: "C:\\dev\\a-wee-bawbee\\frontend\\src\\pages\\Banks.tsx"
timestamp: 2026-10-21T14-01-02Z
slug: frontend-src-pages-banks-tsx
---
# Critique: Phase 4 open-banking UI (Banks, consent items, nav)

Method: dual-agent (A: design review · B: detector + Playwright overlay)

## Design Health Score: 25/40 (Acceptable)
1 status 3 · 2 real world 3 · 3 control 2 · 4 consistency 2 · 5 error prevention 2 · 6 recognition 3 · 7 flexibility 2 · 8 minimalist 3 · 9 recovery 2 · 10 help 3

## Specificity
Copy authored; look generic because `.bank-accounts li` / `.txn-list li` set --note-ink at higher specificity than [data-ink], so inks never render. Detector clean (CLI 0; overlay 1 false positive: colophon viewport edge).

## Priority issues
- [P1] Inks never apply on Banks/Transactions rows. :where() default; owner lettering. colorize.
- [P1] Cancelling the 428 password sheet strands the sheet below in busy state. Ask password first inside the sheet; reset busy. harden.
- [P1] Re-link from consent item breaks: no scroll, Re-link not emphasised, forced mapping on return, no confirmation. harden, clarify.
- [P2] Disconnect+delete lacks count and irreversibility. clarify.
- [P2] Consent snooze global and unbounded. Owner-only, capped, undo. harden.

## Personas
Alex: no sync-all, sync-now not disabled in window, dead Not-linked rows. Sam: disabled select hint not described, stacked dialog focus. Casey: wrapping buttons, PWA vs Safari return. Person 2: jargon, 12h reconfirm, third-person dashboard copy.

## Minor
Repeated bank name, bold healthy consent line, sticky ?error, lapsed consent wording, RBS search name.
