"""Command line: `bawbee <command>` in the container, or `python -m app.cli <command>`.

migrate                                  bring the database up to the latest schema
import-seed PATH|-                       load seed/budget-seed.json (or stdin) into an empty DB
user create USERNAME --person KEY        add a login (prompts for the password)
user reset-password USERNAME             set a new password and sign out their sessions
user list                                show logins
user disable|enable USERNAME             block or restore a login
fernet-key                               print a new key for BAWBEE_FERNET_KEY_FILE
providers                                which bank links work on this server, and why not
import-plan PATH|-                       add the mortgage from the seed (once; Phase 7)
import-childcare PATH|-                  load the old childcare calculator's state.json
sync [--connection ID]                   sync bank connections now (the worker does 06:00, 18:00)
consents                                 raise inbox items for consents ending within 14 days
sync-report [--days N]                   syncs per connection, and anything that looks doubled
alerts                                   recurring, missed and still-paying checks; tell HA
"""

import argparse
import getpass
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import crypto
from app.config import get_settings
from app.db import make_engine, utcnow
from app.domain import audit
from app.domain import auth as auth_domain
from app.domain import childcare as childcare_domain
from app.domain import mortgage as mortgage_domain
from app.domain.models import (
    AuthSession,
    BankAccountLink,
    BankConnection,
    BankTransaction,
    Person,
    SyncRun,
    User,
)
from app.importers.seed import SeedError, import_seed, load_seed
from app.providers.base import ProviderError
from app.sync import alerts, banks

BACKEND = Path(__file__).resolve().parents[1]


class CliError(Exception):
    pass


@contextmanager
def _session() -> Iterator[Session]:
    engine = make_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def migrate() -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(config, "head")


def import_seed_file(path: str, as_of: date, cleanups: bool) -> None:
    seed = json.load(sys.stdin) if path == "-" else load_seed(Path(path))
    with _session() as session:
        result = import_seed(session, seed, as_of, apply_cleanups=cleanups)
        session.commit()
        print(
            f"Imported {result.lines} live lines into {result.live.name!r}"
            f" (id {result.live.id}), {result.overrides} overrides into {result.what_if.name!r}"
            f" (id {result.what_if.id}) and {result.follow_ups} follow-ups."
            f" Cleanups applied: {', '.join(result.cleanups) or 'none'}."
        )


def _read_json(path: str) -> Any:
    if path == "-":
        return json.loads(sys.stdin.read().removeprefix("\ufeff"))
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def import_plan(path: str) -> None:
    """The mortgage from the seed, for a database that already has the budget."""
    seed = _read_json(path)
    with _session() as session:
        before = mortgage_domain.current(session)
        made = mortgage_domain.import_from_seed(session, seed, None, date.today())
        session.commit()
        if before is not None:
            print(f"The {before.lender} mortgage is already set up; nothing changed.")
        elif made is None:
            print("The seed has no mortgage.")
        else:
            linked = "linked to its budget line" if made.line_id else "not linked to a budget line"
            print(
                f"Added the {made.lender} mortgage ({linked}) and "
                f"{len(mortgage_domain.HISTORY)} past ones."
            )


def import_childcare(path: str) -> None:
    state = _read_json(path)
    with _session() as session:
        counts = childcare_domain.import_state(session, state, None)
        session.commit()
        print(
            f"Loaded {counts['children']} children, {counts['patterns']} work patterns and "
            f"{counts['nurseries']} nurseries."
        )


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        # Whatever the shell added: PowerShell pipes a byte-order mark and "\r\n".
        return sys.stdin.readline().removeprefix("\ufeff").rstrip("\r\n")
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Again: "):
        raise CliError("The passwords didn't match")
    return first


def _user(session: Session, username: str) -> User:
    user = session.scalars(
        select(User).where(User.username == auth_domain.normalise_username(username))
    ).first()
    if user is None:
        raise CliError(f"No user called {username!r}")
    return user


def user_command(args: argparse.Namespace) -> None:
    with _session() as session:
        if args.action == "list":
            people = {p.id: p.key for p in session.scalars(select(Person))}
            for u in session.scalars(select(User).order_by(User.id)):
                state = "disabled" if u.disabled_at else "active"
                ways = ", ".join(
                    w for w, on in (("password", u.password_hash), ("oidc", u.oidc_subject)) if on
                )
                person = people.get(u.person_id or -1, "-")
                print(
                    f"{u.username:16} {u.display_name:20} person={person:8}"
                    f" {state:9} {ways or 'no sign-in method'}"
                )
            return
        if args.action == "create":
            password = _read_password(args.password_stdin)
            user = auth_domain.create_user(
                session,
                args.username,
                args.name or args.username.replace("_", " ").title(),
                args.person,
                password,
                None,
                email=args.email,
            )
            print(f"Created {user.username!r}.")
        elif args.action == "reset-password":
            user = _user(session, args.username)
            auth_domain.set_password(session, user, _read_password(args.password_stdin), None)
            for s in session.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
                )
            ):
                auth_domain.revoke(session, s, None)
            print(f"Password reset for {user.username!r}; their sessions were signed out.")
        else:
            user = _user(session, args.username)
            user.disabled_at = utcnow() if args.action == "disable" else None
            audit.record(session, None, "user", user.id, args.action)
            print(f"{args.action.title()}d {user.username!r}.")
        session.commit()


def sync_command(connection_id: int | None, full: bool = False) -> None:
    settings = get_settings()
    with _session() as session:
        if connection_id is None:
            runs = banks.sync_all(session, settings, "manual", full=full)
        else:
            connection = session.get(BankConnection, connection_id)
            if connection is None:
                raise CliError(f"No bank connection {connection_id}")
            runs = [banks.sync_connection(session, settings, connection, "manual", full=full)]
        session.commit()
        for run in runs:
            print(
                f"connection {run.connection_id}: {run.status}, {run.rows_new} new,"
                f" {run.rows_updated} updated, {run.rows_removed} removed"
                + (f" ({run.error})" if run.error else "")
            )
            if full:
                # Where each account's bank rows start: earlier days come from CSV.
                connection = session.get(BankConnection, run.connection_id)
                for link in connection.accounts if connection else []:
                    if link.account is not None:
                        start = link.synced_from.isoformat() if link.synced_from else "a year ago"
                        print(f"    {link.account.name}: bank rows from {start}")
        if full:
            # What the new rows answer: late payments clear their missed items.
            alerts_command()


def alerts_command() -> None:
    with _session() as session:
        found = alerts.run(session, get_settings(), banks.uk_today())
        session.commit()
        print(
            f"recurring: {found.recurring.candidates} found, {found.recurring.known} budgeted,"
            f" {found.recurring.proposed} proposed; missed: {found.missed};"
            f" still paying: {found.still_paying}; consents: {found.consents};"
            f" notified: {', '.join(found.notified) or 'nothing'}"
        )


def consents_command() -> None:
    with _session() as session:
        raised = banks.check_consents(session)
        session.commit()
        print(f"{raised} consent reminders raised.")


def sync_report(days: int) -> None:
    """For the Phase 4 check: twice-daily syncs for a week, and nothing doubled."""
    since = utcnow() - timedelta(days=days)
    with _session() as session:
        for connection in session.scalars(select(BankConnection).order_by(BankConnection.id)):
            runs = session.scalars(
                select(SyncRun).where(
                    SyncRun.connection_id == connection.id, SyncRun.started_at >= since
                )
            ).all()
            ok = sum(1 for r in runs if r.status == "ok")
            print(
                f"{connection.id:3} {connection.bank_name:20} {connection.owner.display_name:8}"
                f" {connection.status:8} {ok}/{len(runs)} syncs ok in {days} days"
            )
        # Bank rows that look like the same payment twice: same account, day, amount and
        # text. Two identical coffees are real, so these are for a person to look at.
        doubled = session.execute(
            select(
                BankTransaction.account_id,
                BankTransaction.booked_date,
                BankTransaction.amount_pence,
                BankTransaction.description,
                func.count(),
            )
            .where(BankTransaction.connection_id.is_not(None))
            .group_by(
                BankTransaction.account_id,
                BankTransaction.booked_date,
                BankTransaction.amount_pence,
                BankTransaction.description,
            )
            .having(func.count() > 1)
        ).all()
        # CSV rows on days a bank link covers would be double counting.
        overlap = 0
        for link in session.scalars(
            select(BankAccountLink).where(BankAccountLink.account_id.is_not(None))
        ):
            if link.synced_through is None:
                continue
            query = (
                select(func.count())
                .select_from(BankTransaction)
                .where(
                    BankTransaction.account_id == link.account_id,
                    BankTransaction.connection_id.is_(None),
                    BankTransaction.booked_date <= link.synced_through,
                )
            )
            if link.synced_from is not None:
                query = query.where(BankTransaction.booked_date >= link.synced_from)
            overlap += session.scalar(query) or 0
        print(f"look-alike bank rows: {len(doubled)} groups (identical payments can be real)")
        for account_id, day, pence, text, n in doubled[:20]:
            print(f"    account {account_id} {day} {pence:>9}p x{n}  {text[:40]}")
        print(f"CSV rows on bank-covered days: {overlap}")


def providers_command() -> None:
    """Which ways of linking a bank work on this server, and why not when they don't."""
    settings = get_settings()
    key = "ok" if crypto.can_encrypt(settings) else "not set up (BAWBEE_FERNET_KEY_FILE)"
    print(f"encryption key: {key}")
    print(f"monzo:          {settings.monzo_problem or 'ok'}")
    problem = settings.enable_banking_problem
    if problem:
        print(f"enable banking: {problem}")
    else:
        try:
            names = [
                b.name for b in banks.available_banks(settings) if b.provider == "enable_banking"
            ]
        except (banks.BankError, ProviderError) as exc:
            print(f"enable banking: set up, but listing its banks failed: {exc}")
        else:
            rbs = (
                "listed"
                if any("royal bank of scotland" in n.lower() for n in names)
                else "NOT listed"
            )
            print(f"enable banking: ok, {len(names)} GB banks (RBS {rbs})")
    print("starling:       ok (a token is added on the Banks screen)")
    print(f"demo bank:      {'on' if settings.banks_demo else 'off'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bawbee")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="upgrade the database to the latest schema")

    seed = sub.add_parser("import-seed", help="import the budget seed into an empty database")
    seed.add_argument("path", help="path to budget-seed.json, or - for stdin")
    seed.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="date the imported live budget starts (default: today)",
    )
    seed.add_argument(
        "--no-cleanups",
        action="store_true",
        help="leave the spreadsheet exactly as it was (skip post_import_cleanups)",
    )

    user = sub.add_parser("user", help="manage logins")
    user_sub = user.add_subparsers(dest="action", required=True)
    create = user_sub.add_parser("create", help="add a login")
    create.add_argument("username")
    create.add_argument("--person", choices=["person_1", "person_2"], help="who this login is")
    create.add_argument("--name", help="display name (default: the username)")
    create.add_argument("--email", help="lets a verified OIDC email link automatically")
    reset = user_sub.add_parser("reset-password", help="set a new password")
    reset.add_argument("username")
    for p in (create, reset):
        p.add_argument(
            "--password-stdin",
            action="store_true",
            help="read the password from stdin instead of prompting",
        )
    user_sub.add_parser("list", help="show logins")
    for action in ("disable", "enable"):
        user_sub.add_parser(action, help=f"{action} a login").add_argument("username")

    sub.add_parser("fernet-key", help="print a new encryption key for bank links")
    sync = sub.add_parser("sync", help="sync bank connections now")
    sync.add_argument("--connection", type=int, help="just this connection id")
    sync.add_argument(
        "--full",
        action="store_true",
        help="re-fetch from the day each bank took over, not just the last week, then run alerts",
    )
    sub.add_parser("consents", help="raise reminders for consents ending within 14 days")
    sub.add_parser("alerts", help="recurring, missed and still-paying checks; tell HA")
    sub.add_parser("providers", help="which bank links work here, and why not")
    plan = sub.add_parser("import-plan", help="add the mortgage from the seed (once)")
    plan.add_argument("path", help="path to budget-seed.json, or - for stdin")
    care = sub.add_parser("import-childcare", help="load the old childcare calculator's state")
    care.add_argument("path", help="path to state.json, or - for stdin")
    report = sub.add_parser("sync-report", help="recent syncs and anything that looks doubled")
    report.add_argument("--days", type=int, default=7)

    args = parser.parse_args(argv)
    try:
        if args.command == "migrate":
            migrate()
        elif args.command == "import-seed":
            import_seed_file(args.path, args.as_of, cleanups=not args.no_cleanups)
        elif args.command == "fernet-key":
            print(crypto.new_key())
        elif args.command == "sync":
            sync_command(args.connection, args.full)
        elif args.command == "consents":
            consents_command()
        elif args.command == "alerts":
            alerts_command()
        elif args.command == "import-plan":
            import_plan(args.path)
        elif args.command == "import-childcare":
            import_childcare(args.path)
        elif args.command == "providers":
            providers_command()
        elif args.command == "sync-report":
            sync_report(args.days)
        else:
            user_command(args)
    except (
        SeedError,
        CliError,
        auth_domain.AuthError,
        childcare_domain.ChildcareError,
        mortgage_domain.MortgageError,
    ) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
