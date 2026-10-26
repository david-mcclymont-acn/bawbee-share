# CLAUDE.md — A Wee Bawbee

Household finance app for Person 1 and Person 2. `PLAN.md` is the spec. Read the relevant section before starting any work. §2 records decisions that are already made: don't re-ask them, and flag anything that contradicts them.

## Working rules

- Work **one phase at a time** (PLAN.md §14). Stop at the end of each phase and show its acceptance checks passing before starting the next.
- **Before any fix to something seen in the live app** (https://bawbee.example.com), run `python scripts/check-deployed.py`. It compares the commit the app reports at `/api/version` with `origin/main`. If it says anything but UP TO DATE (BEHIND, or UNKNOWN), change nothing: tell Person 1 to update (`cd /opt/bawbee && git pull && docker compose up -d --build`) and retest, then stop until they say it still happens.
- Names: display "A Wee Bawbee", short name "Bawbee", Python distribution and Docker services `bawbee` (`bawbee-app`, `bawbee-worker`).
- `seed/budget-seed.json` holds made-up example budget data and the expected outputs for it. Use it for seeding and golden tests. `reference/childcare-app/` is the old childcare calculator, kept for parity.

## Stack (§12)

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Pydantic v2 (+ pydantic-settings), APScheduler, `httpx`, `PyJWT[crypto]`. Managed with `uv` (`backend/pyproject.toml`, `backend/uv.lock`).
- **Database:** SQLite in WAL mode, on the `/data` Docker volume. Keep the ORM portable (no SQLite-only SQL) so moving to MariaDB/Postgres is only a config change.
- **Frontend:** React + TypeScript + Vite, TanStack Query, Radix headless primitives styled from `DESIGN.md` tokens via CSS variables (no themed component kit), Recharts styled to the tokens, PWA manifest. Mobile-first; test at 390px and 1440px.
- **Auth (decision 27):** local accounts are the default: username and password (Argon2id via `argon2-cffi`), server-side sessions, CSRF on writes, and rate-limited login. Users are created and reset with the `bawbee user` CLI; there's no sign-up page. authentik OIDC is optional and only on when `BAWBEE_OIDC_*` is set. Never make authentik a requirement. Two users, mapped to Person 1 and Person 2. Every write goes to `AuditLog` with the user.
- **Design:** Impeccable. `PRODUCT.md` holds product context and `DESIGN.md` is the source of truth for tokens. Run `/impeccable shape` before each new screen, then `critique` and `polish` at the end of each UI phase.

## Repo layout

```
backend/app/engine/     pure calculation engine; no DB or framework imports
backend/app/domain/     models, version resolution, promotion
backend/app/providers/  bank providers (enable_banking, monzo, starling_pat, csv_import)
backend/app/sync/       scheduler, ingestion, matching, recurring detection, inbox
backend/app/api/        FastAPI routers
backend/app/importers/  seed JSON, xlsx, childcare state.json
backend/config/tax/     TaxYearConfig data files, each citing its gov.uk / gov.scot source
backend/tests/
frontend/
docs/                   ADRs, provider notes, nginx sample
```

## Code conventions

- **Money is integer pence** everywhere: DB, API and engine. Never floats. Use `Fraction` for exact intermediate values, such as shares and annual ÷ 12. Format only at the UI edge (`£1,192.46`), with tabular figures.
- Use ISO dates. Timestamps are UTC.
- The engine is pure and side-effect free. Tax rates come only from `config/tax/` files, never from constants in code.
- Never hard-code derived values (PLAN.md §4.1). Household surplus always includes the joint surplus.
- Golden tests assert to the penny against `expected_spreadsheet_outputs`.

## Security and privacy (§13)

- **Read-only** bank access only. Never request payment scopes.
- Provider session tokens and the Starling PAT are Fernet-encrypted at rest, with the key from a secret. The Enable Banking `.pem` is a mounted Docker secret.
- **Never commit** secrets, `.env`, `.pem`, bank CSV exports, databases or backups. gitleaks runs in pre-commit and CI. `seed/` and `reference/` hold made-up example data only; never commit real figures.
- Nightly encrypted SQLite backup to the NAS, with a tested restore script.
- Audit every edit, approval, promotion and bank-connection event with the user.
- Bank connections belong to one person. Person 2 links and re-links their own accounts. Disconnecting deletes the session and optionally its transactions.
- In Codespaces, use the Enable Banking sandbox and seed or synthetic data only. Real connections and real data live only on the home VM.

## Commands

Backend (run from `backend/`):

```
uv sync                         # install deps
uv run pytest                   # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python -m app.cli migrate                       # DB to latest schema (./data/bawbee.db)
uv run python -m app.cli import-seed ../seed/budget-seed.json   # into an empty DB
uv run uvicorn app.main:app --reload   # API on :8000 (this laptop: use --port 8765)
uv run alembic revision --autogenerate -m "..."        # after changing app/domain/models.py
```

Plan (Phases 6-7): `uv run python -m app.cli import-plan ../seed/budget-seed.json` (the mortgage, once), `import-childcare ../reference/childcare-app/state.json`; `node scripts/childcare-golden.mjs` regenerates the childcare parity baseline from the old calculator's own code.

Banks: `uv run python -m app.cli sync` (`--full` re-fetches from the day each bank took over, then runs alerts), `consents`, `sync-report --days 7`, `providers` (which bank routes work and why not), `fernet-key`. Alerts: `alerts`. For local testing of the demo bank, set `BAWBEE_BANKS_DEMO=true` and `BAWBEE_FERNET_KEY=$(uv run python -m app.cli fernet-key)`.

Logins: `uv run python -m app.cli user create person_1 --person person_1` (prompts; `--password-stdin` for scripts), `user reset-password`, `user list`, `user disable|enable`. In the container the same CLI is `bawbee …`. Local dev over plain HTTP needs `BAWBEE_COOKIE_SECURE=false`.

On the VM, see `docs/deploy.md`. The seed is imported from stdin so real data never enters the image:
`docker compose exec -T bawbee-app bawbee import-seed - < seed/budget-seed.json`

Frontend e2e (Playwright, both widths) against a running seeded app:
`PW_CHANNEL=chrome E2E_BASE_URL=http://127.0.0.1:8765 npx playwright test` (from `frontend/`; CI runs it in the `e2e` job and uploads screenshots).

## Domain notes

- `app/domain/versions.py` resolves any version to a `ResolvedBudget`; `calculate()` runs the engine on it. Lines are keyed by `line_id`; overrides are sparse `fields_json` against the resolved base.
- Live changes go through `editing.update_live_line` (new dated `BudgetLineVersion`), never by editing a version in place, so what-if conflicts stay detectable.
- Tests use a migrated SQLite template copied per test (`tests/conftest.py`); `AS_OF` is 2026-10-01. The `raw_import` fixture is the seed exactly as the spreadsheet has it, and `imported` is the seed after the cleanups.
- Bank data (Phase 3): `providers/csv_import.py` parses Monzo/Starling/RBS or a user column map (pure). `sync/ingest.py` stores rows idempotently (bank id, else a stable hash), then `sync/matching.py` pairs transfers (and ticks payday), applies "always match" rules, and scores bills (payee, amount, day). Any amount difference raises a `price_change` `InboxItem`, de-duplicated on line and new price; nothing touches the budget until `sync/inbox.py` approves it, after which each overriding what-if keeps, takes or sets its own amount.
- Open banking (Phase 4): providers implement `providers/base.py` (`enable_banking.py` for RBS, `monzo.py` for Monzo via its Developer API (decision 29), `starling_pat.py`, and `demo.py` for CI and Codespaces only, behind `BAWBEE_BANKS_DEMO`). `sync/banks.py` handles link, re-link, mapping, sync, consents and disconnect. A link belongs to its owner; the joint account is mapped once (by identification hash). The bank takes over each account from the day after its last CSV row. Each sync re-fetches the last week, and `ingest.ingest_bank` keeps that idempotent and settles pending rows. Secrets are Fernet-encrypted (`app/crypto.py`). Link and disconnect need a recent password (HTTP 428, then `POST /api/auth/confirm`). A Monzo link is `pending` until access is approved in the Monzo app, then `POST /api/banks/{id}/continue` fetches its accounts; each token refresh replaces both tokens and is saved at once (`banks.monzo_for`). Pot moves are `bank_category="pot_transfer"` and never recurring. Provider tests use `httpx.MockTransport`, never the network. See `docs/providers.md`.
- Recurring and alerts (Phase 5): `sync/recurring.py` finds steady rhythms. It needs 3 or more payments with a weekly, monthly, quarterly or annual cadence and a steady amount: within 15%, or one price step, which it records as the previous price and the change date. Same-day payments to one payee are summed. Payees matched to a live bill are "known"; the rest become `new_recurring` inbox items, with a `likely_line_id` when a live bill on that account has the same price. `sync/alerts.py` adds missed payments (the bill's day, or the usual paid day, plus 5, and only for bills paid from the bank before), `still_paying` items (open "cancel" follow-ups whose payee is still taken), and Home Assistant: one batched notification for new items, one per ending consent (with `person`), promotion due, and a low projected joint balance. Each is logged in `Notification` so it goes once. History from before the budget's first `effective_from` matches the budget as it first stood, with no price changes raised. `/api/ha/summary` takes a hashed bearer token made in Settings. See `docs/home-assistant.md`.
- Forecast and plan (Phases 6-7): engines are pure (`engine/tax.py` take-home, `engine/childcare.py` the old calculator ported line for line, `engine/mortgage.py`, `engine/forecast.py`); every tax figure comes from `config/tax/<YYYY-YY>.json`, each group citing its gov.uk / gov.scot / SPPA page (`domain/taxconfig.py`; NHS tiers have the pay-change table and the continuing-member one). `domain/forecast.py` resolves the version for each month of a path (a dated chain of versions), drops agreements after their end month (the PCP), adds one-offs and goals, and starts next month. A `ChildcareScenario` linked to a what-if is applied in `versions.resolve` (Person 2's pay and a calculated Nursery line, origin `computed`, never editable); funded hours start from the dates of birth, so linked versions change with age. The mortgage payment is the source of truth for its budget line; `remortgage_whatif` builds on live or another what-if. `sync/plan_alerts.py` raises goal_adjust, renewal_due (30 days), remortgage_window (7 months, decision 30), agreement_ending (3 months) and salary_mismatch (over £5). Childcare parity is tested against `reference/childcare-app/parity.json`, which the old JavaScript itself produced.
- `tests/bank_fixtures.py` is a synthetic month in each bank's format, and `history_files()` gives seven months (May to November 2026) for Phase 5's checks (never real statements; real CSVs are gitignored). It must keep matching at least 80% of monthly bill lines.

Frontend (run from `frontend/`):

```
npm ci
npm run dev        # Vite on :5173, proxies /api and /health to :8000
npm run lint && npm run format:check && npm run typecheck
npm run build
```

Whole stack:

```
docker compose up --build       # app on 127.0.0.1:8000; worker alongside
pre-commit run --all-files      # or: uvx pre-commit run --all-files
```

This laptop has no Docker (Hyper-V/WSL2 is blocked). Docker runs in CI, in Codespaces or on the home VM.
