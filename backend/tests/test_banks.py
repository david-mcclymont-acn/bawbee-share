"""Bank connections end to end against the database, with fake banks (no network).

Phase 4's acceptance checks, as far as they can run here: syncs add nothing twice, payday
ticks itself from synced transfers, and consents ending within 14 days raise inbox items.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import crypto
from app.config import Settings
from app.db import utcnow
from app.domain import payday
from app.domain.models import (
    Account,
    BankConnection,
    BankTransaction,
    InboxItem,
    User,
)
from app.importers.seed import ImportResult
from app.providers.base import (
    ConsentExpired,
    ProviderAccount,
    ProviderBalance,
    ProviderTransaction,
)
from app.providers.demo import DemoProvider
from app.sync import banks, ingest
from tests import bank_fixtures as bf


@pytest.fixture
def settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        static_dir=tmp_path,
        cookie_secure=False,
        public_url="http://testserver",
        fernet_key=crypto.new_key(),
        banks_demo=True,
    )


DEMO = banks.Bank("Demo Bank", "GB", "demo", 90)


def account(session: Session, key: str) -> Account:
    return session.scalars(select(Account).where(Account.key == key)).one()


def link_demo(session: Session, settings: Settings, user: User) -> BankConnection:
    url = banks.start_link(session, settings, user, DEMO)
    state = url.split("state=")[1].split("&")[0]
    return banks.complete_link(session, settings, user, state, "demo").connection


def count(session: Session, **where: object) -> int:
    query = select(func.count()).select_from(BankTransaction)
    for key, value in where.items():
        query = query.where(getattr(BankTransaction, key) == value)
    return session.scalar(query) or 0


@dataclass
class FakeBank:
    """Hands back whatever rows the test sets for each bank account uid."""

    rows: dict[str, list[ProviderTransaction]] = field(default_factory=dict)
    fail: Exception | None = None

    def list_accounts(self) -> list[ProviderAccount]:
        return []

    def fetch_transactions(
        self, account: ProviderAccount, since: date | None, until: date
    ) -> list[ProviderTransaction]:
        if self.fail:
            raise self.fail
        return [
            r
            for r in self.rows.get(account.uid, [])
            if (since is None or r.booked_date >= since) and r.booked_date <= until
        ]

    def fetch_balance(self, account: ProviderAccount) -> ProviderBalance | None:
        return ProviderBalance(100, utcnow())


def row(ext: str, day: date, pence: int, text: str, status: str = "booked") -> ProviderTransaction:
    return ProviderTransaction(ext, day, pence, text, status=status)  # type: ignore[arg-type]


# ---- linking and mapping ---------------------------------------------------------------


def test_linking_the_demo_bank_and_choosing_its_accounts(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    person_1 = users["person_1"]
    connection = link_demo(session, settings, person_1)
    assert connection.owner_user_id == person_1.id
    assert [a.name for a in connection.accounts] == ["Demo Current", "Demo Joint"]
    # The secret is stored encrypted, never as written.
    assert connection.secret_encrypted != "demo"
    assert crypto.decrypt(settings, connection.secret_encrypted).startswith("demo:")
    assert connection.consent_expires_at is not None

    banks.set_mapping(session, connection, person_1, {"demo-joint": "joint_billing"})
    joint = next(a for a in connection.accounts if a.external_uid == "demo-joint")
    assert joint.account is not None
    assert joint.account.key == "joint_billing"
    # No CSV history for Joint Billing: the bank brings in as much as it has.
    assert joint.synced_from is None


def test_the_bank_takes_over_the_day_after_the_last_csv_row(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    joint = account(session, "joint_billing")
    fmt, text = bf.month_files()["joint_billing"]
    ingest.import_csv(session, joint, text, None, fmt, None)  # type: ignore[arg-type]
    connection = link_demo(session, settings, users["person_1"])
    banks.set_mapping(session, connection, users["person_1"], {"demo-joint": "joint_billing"})
    link = next(a for a in connection.accounts if a.external_uid == "demo-joint")
    assert link.synced_from == date(2026, 11, 26)  # the CSV's last day was 25 Nov

    banks.sync_connection(
        session,
        settings,
        connection,
        "link",
        provider=DemoProvider(today=date(2026, 12, 20)),
        today=date(2026, 12, 20),
    )
    # Nothing from the bank before the CSV ended, so nothing doubled.
    first_bank_day = session.scalar(
        select(func.min(BankTransaction.booked_date)).where(
            BankTransaction.connection_id == connection.id
        )
    )
    assert first_bank_day is not None
    assert first_bank_day >= date(2026, 11, 26)
    # A CSV for days the bank has covered skips those days.
    late = bf.starling_csv([(date(2026, 12, 1), "SKY DIGITAL|SKY0123456", -10475)])
    summary = ingest.import_csv(session, joint, late, None, "starling", None)
    assert (summary.new, summary.bank_covered) == (0, 1)


def test_resyncing_adds_nothing_twice_and_pending_rows_settle(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    connection = link_demo(session, settings, users["person_1"])
    banks.set_mapping(session, connection, users["person_1"], {"demo-current": "person_1_monzo"})
    monzo = account(session, "person_1_monzo")

    # Twice a day for a week, the way the worker runs it.
    for day in range(7):
        today = date(2026, 11, 24) + timedelta(days=day)
        for _ in range(2):
            run = banks.sync_connection(
                session,
                settings,
                connection,
                "schedule",
                provider=DemoProvider(today=today),
                today=today,
            )
            assert run.status == "ok"
    rows = session.scalars(
        select(BankTransaction).where(BankTransaction.account_id == monzo.id)
    ).all()
    keys = [(r.booked_date, r.amount_pence, r.description) for r in rows]
    assert len(keys) == len(set(keys)), "a payment was stored twice"
    # The salary on the 28th was pending for two days, then booked under its final id.
    salary = next(
        r for r in rows if r.amount_pence == 300000 and r.booked_date == date(2026, 11, 28)
    )
    assert (salary.status, salary.external_id) == ("booked", "demo:demo-current:2026-11-28:0")
    # Only the last two days can still be pending.
    last = date(2026, 11, 30)
    assert all(r.status == "booked" for r in rows if (last - r.booked_date).days >= 2)


def test_a_pending_payment_that_books_at_a_different_amount_is_replaced(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    connection = link_demo(session, settings, users["person_1"])
    banks.set_mapping(session, connection, users["person_1"], {"demo-current": "person_1_monzo"})
    fake = FakeBank({"demo-current": [row("p1", date(2026, 11, 5), -4500, "BP FUEL", "pending")]})
    banks.sync_connection(
        session, settings, connection, "schedule", provider=fake, today=date(2026, 11, 5)
    )
    fake.rows["demo-current"] = [row("b1", date(2026, 11, 6), -5230, "BP FUEL")]
    run = banks.sync_connection(
        session, settings, connection, "schedule", provider=fake, today=date(2026, 11, 6)
    )
    assert (run.rows_new, run.rows_removed) == (1, 1)
    fuel = session.scalars(
        select(BankTransaction).where(BankTransaction.description == "BP FUEL")
    ).all()
    assert [(f.status, f.amount_pence) for f in fuel] == [("booked", -5230)]


def test_a_full_sync_fetches_what_earlier_syncs_couldnt_see(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    """Bills a Starling Space paid weren't fetched before: a full sync goes back for them,
    and relabels the moves into the Space already stored."""
    connection = link_demo(session, settings, users["person_1"])
    banks.set_mapping(session, connection, users["person_1"], {"demo-joint": "joint_billing"})
    move = row("m1", date(2026, 10, 1), -50000, "Top up")
    fake = FakeBank({"demo-joint": [move]})
    banks.sync_connection(
        session, settings, connection, "schedule", provider=fake, today=date(2026, 10, 23)
    )
    # Now the provider sees the Space: an EE bill on the 5th, and the move is ours.
    fake.rows["demo-joint"] = [
        ProviderTransaction(
            "m1",
            date(2026, 10, 1),
            -50000,
            "Top up",
            counterparty="Space: Bills Manager",
            bank_category="pot_transfer",
        ),
        row("bills:dd", date(2026, 10, 5), -4500, "EE LIMITED"),
    ]
    normal = banks.sync_connection(
        session, settings, connection, "schedule", provider=fake, today=date(2026, 10, 24)
    )
    assert normal.rows_new == 0  # only the last week is re-fetched
    full = banks.sync_connection(
        session, settings, connection, "manual", provider=fake, today=date(2026, 10, 24), full=True
    )
    assert full.rows_new == 1
    stored = session.scalars(
        select(BankTransaction).where(BankTransaction.external_id == "m1")
    ).one()
    assert (stored.bank_category, stored.counterparty) == ("pot_transfer", "Space: Bills Manager")


def test_payday_ticks_itself_from_synced_transfers(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    connection = link_demo(session, settings, users["person_1"])
    banks.set_mapping(
        session,
        connection,
        users["person_1"],
        {"demo-current": "person_1_monzo", "demo-joint": "joint_billing"},
    )
    fake = FakeBank(
        {
            "demo-current": [row("m1", date(2026, 10, 30), -267000, "Joint Billing")],
            "demo-joint": [row("j1", date(2026, 10, 30), 267000, "P ONE")],
        }
    )
    banks.sync_connection(
        session, settings, connection, "schedule", provider=fake, today=date(2026, 10, 30)
    )
    run = payday.get_run(session, payday.month_key(2026, 10))
    assert run is not None
    ticked = [t for t in run.transfers if t.done_at is not None]
    assert [(t.name, t.done_via) for t in ticked] == [("To Joint Billing", "bank")]


# ---- the joint account, owners --------------------------------------------------------


def test_the_joint_account_is_linked_once(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    person_1_link = link_demo(session, settings, users["person_1"])
    banks.set_mapping(session, person_1_link, users["person_1"], {"demo-joint": "joint_billing"})
    person_2_link = link_demo(session, settings, users["person_2"])
    joint_again = next(a for a in person_2_link.accounts if a.external_uid == "demo-joint")
    assert banks.claimed_elsewhere(session, joint_again) is not None
    with pytest.raises(banks.BankError, match="already linked by Person 1"):
        banks.set_mapping(session, person_2_link, users["person_2"], {"demo-joint": "person_2_rbs"})
    # And one of our accounts can't be fed by two links either.
    with pytest.raises(banks.BankError, match="already comes from"):
        banks.set_mapping(
            session, person_2_link, users["person_2"], {"demo-current": "joint_billing"}
        )


def test_only_the_owner_relinks_or_disconnects(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    connection = link_demo(session, settings, users["person_2"])
    with pytest.raises(banks.NotOwner, match="Only Person 2"):
        banks.start_link(session, settings, users["person_1"], DEMO, relink=connection)
    with pytest.raises(banks.NotOwner):
        banks.disconnect(
            session, settings, connection, users["person_1"], delete_transactions=False
        )
    with pytest.raises(banks.NotOwner):
        banks.set_mapping(session, connection, users["person_1"], {"demo-current": "person_2_rbs"})


# ---- consent ----------------------------------------------------------------------------


def test_consents_ending_within_14_days_raise_one_inbox_item(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    connection = link_demo(session, settings, users["person_2"])
    banks.set_mapping(session, connection, users["person_2"], {"demo-current": "person_2_rbs"})
    now = utcnow()
    connection.consent_expires_at = now + timedelta(days=20)
    assert banks.check_consents(session, now) == 0
    connection.consent_expires_at = now + timedelta(days=13)
    assert banks.check_consents(session, now) == 1
    assert banks.check_consents(session, now) == 0  # once only
    item = session.scalars(select(InboxItem).where(InboxItem.type == "consent_expiring")).one()
    assert item.payload_json["owner"] == "Person 2"
    assert item.payload_json["accounts"] == ["Person 2 RBS"]
    # Person 2 re-links: the reminder is dealt with, and the new consent is further out.
    url = banks.start_link(session, settings, users["person_2"], DEMO, relink=connection)
    state = url.split("state=")[1].split("&")[0]
    result = banks.complete_link(session, settings, users["person_2"], state, "demo")
    again = result.connection
    assert again.id == connection.id
    # Same accounts as before: no need to ask which they feed again.
    assert (result.relinked, result.new_accounts) == (True, False)
    assert item.status == "approved"
    # The mapping survives the re-link.
    assert next(a for a in again.accounts if a.external_uid == "demo-current").account is not None


def test_a_refused_consent_marks_the_link_expired_and_asks_the_owner(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    connection = link_demo(session, settings, users["person_2"])
    banks.set_mapping(session, connection, users["person_2"], {"demo-current": "person_2_rbs"})
    run = banks.sync_connection(
        session,
        settings,
        connection,
        "schedule",
        provider=FakeBank(fail=ConsentExpired("ended")),
        today=date(2026, 11, 5),
    )
    assert (run.status, connection.status) == ("error", "expired")
    item = session.scalars(select(InboxItem).where(InboxItem.type == "consent_expiring")).one()
    assert item.payload_json["expired"] is True
    # The scheduler leaves expired links alone until they're re-linked.
    assert banks.sync_all(session, settings) == []


def test_lapsed_consents_are_marked_expired(
    session: Session, settings: Settings, users: dict[str, User], imported: ImportResult
) -> None:
    connection = link_demo(session, settings, users["person_1"])
    connection.consent_expires_at = utcnow() - timedelta(hours=1)
    banks.check_consents(session)
    assert connection.status == "expired"


# ---- disconnecting ---------------------------------------------------------------------


@pytest.mark.parametrize("delete", [True, False])
def test_disconnecting_optionally_takes_its_transactions(
    session: Session,
    settings: Settings,
    users: dict[str, User],
    imported: ImportResult,
    delete: bool,
) -> None:
    connection = link_demo(session, settings, users["person_1"])
    banks.set_mapping(session, connection, users["person_1"], {"demo-current": "person_1_monzo"})
    banks.sync_connection(
        session,
        settings,
        connection,
        "link",
        provider=DemoProvider(today=date(2026, 11, 20)),
        today=date(2026, 11, 20),
    )
    synced = count(session, connection_id=connection.id)
    assert synced > 0
    removed = banks.disconnect(
        session, settings, connection, users["person_1"], delete_transactions=delete
    )
    assert session.get(BankConnection, connection.id) is None
    monzo = account(session, "person_1_monzo")
    if delete:
        assert removed == synced
        assert count(session, account_id=monzo.id) == 0
    else:
        assert removed == 0
        assert count(session, account_id=monzo.id) == synced


def test_reauth_window(session: Session, users: dict[str, User]) -> None:
    from app.domain.auth import recently_confirmed, start_session

    auth = start_session(session, users["person_1"], "local", None, None).session
    assert recently_confirmed(auth, 12)
    auth.created_at = datetime.now(auth.created_at.tzinfo) - timedelta(hours=13)
    assert not recently_confirmed(auth, 12)
    auth.confirmed_at = utcnow()
    assert recently_confirmed(auth, 12)
