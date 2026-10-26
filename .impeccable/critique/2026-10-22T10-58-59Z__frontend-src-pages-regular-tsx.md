---
target: Phase 5 inbox, regular payments, HA settings
total_score: 26
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 2
target_identity: "file:C:\\dev\\a-wee-bawbee\\frontend\\src\\pages\\Regular.tsx"
target_fingerprint: "sha256:8113a930448e1c435be8bcaed239cea8fe706b4c8a7dd94c602eab069090cb11"
target_path: "C:\\dev\\a-wee-bawbee\\frontend\\src\\pages\\Regular.tsx"
timestamp: 2026-10-22T10-58-59Z
slug: frontend-src-pages-regular-tsx
---
# Critique: Phase 5 UI (Inbox additions, Regular payments, Settings → Home Assistant)

Method: dual-agent (A: design review · B: detector + Playwright overlay)

## Design Health Score: 26/40 (Acceptable)
1 status 3 · 2 real world 3 · 3 control 2 · 4 consistency 2 · 5 prevention 2 · 6 recognition 3 · 7 flexibility 2 · 8 minimalist 3 · 9 recovery 3 · 10 help 3

## Specificity
Strips authored (price-change anatomy, household answers). Regular payments generic. Detector clean; overlay only the colophon false positive. Disney+ import matched the existing line and raised a price change (correct).

## Priority issues
- [P1] Budget-changing answers (stopped, add, link, Not a bill) have no consequence preview and no undo. harden, clarify.
- [P1] Focus lost on confirm/form swaps and Back. harden.
- [P2] Cancel decision duplicated between still-paying strip and Chores. distill.
- [P2] Regular payments generic and unreachable from More; duplicate name; "1 regular"; no un-ignore. layout, clarify.
- [P2] HA section speaks server-admin; token only in YAML; one-click revoke; test result not announced. clarify.

## Personas
Alex bulk/read-only; Sam focus; Casey token copy, permanent quiet button; Person 2 stop without effect/undo, jargon.

## Minor
Inbox-clear copy, confirm indent, delta class for due day, no provider field, sum taken since cancel.
