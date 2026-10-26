"""Recurring payments, missed payments, still paying, and Home Assistant (Phase 5).

Phase 5's acceptance checks, against seven months of synthetic history:
- it proposes the existing bills with the right cadence
- it catches the §4.6 price changes where they appear in the data
- it flags Domestic and General if that's still being paid
"""

import json
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.models import (
    Account,
    BankTransaction,
    BudgetVersion,
    CategoryRule,
    FollowUp,
    InboxItem,
    RecurringCandidate,
    User,
)
from app.importers.seed import ImportResult
from app.sync import alerts, inbox, ingest, matching
from app.sync.recurring import rhythm
from tests import bank_fixtures as bf

TODAY = bf.HISTORY_TODAY


def months(*days: tuple[int, int, int]) -> list[date]:
    return [date(*d) for d in days]


# ---- the rhythm of one payee ------------------------------------------------------------


def test_a_monthly_payment_drifting_a_day_or_two_is_monthly() -> None:
    paid = [(date(2026, m, 1 + (m % 3)), 1500) for m in range(5, 11)]
    found = rhythm(paid)
    assert found is not None
    assert (found.cadence, found.typical_pence, found.occurrences) == ("monthly", 1500, 6)


def test_weekly_quarterly_and_annual() -> None:
    weekly = [(date(2026, 9, 5) + timedelta(days=7 * n), 399) for n in range(6)]
    quarterly = [(date(2025, 2, 10), 9000), (date(2025, 5, 12), 9000), (date(2025, 8, 11), 9000)]
    annual = [(date(2023, 3, 3), 4999), (date(2024, 3, 4), 4999), (date(2025, 3, 3), 5499)]
    assert [r.cadence for r in map(rhythm, (weekly, quarterly, annual)) if r] == [
        "weekly",
        "quarterly",
        "annual",
    ]


def test_a_price_change_keeps_the_rhythm_and_is_recorded() -> None:
    pet = [(date(2026, m, 24), 9655 if m < 8 else 13874) for m in range(5, 12)]
    found = rhythm(pet)
    assert found is not None
    assert (found.typical_pence, found.previous_pence, found.changed_on) == (
        13874,
        9655,
        date(2026, 8, 24),
    )


def test_not_a_rhythm() -> None:
    shop = [(date(2026, 9, 5) + timedelta(days=7 * n), 6000 + n * 2900) for n in range(6)]
    assert rhythm(shop) is None  # weekly, but the amount is all over the place
    assert rhythm([(date(2026, 9, 1), 100), (date(2026, 10, 1), 100)]) is None  # only two
    random = [(date(2026, 1, 3), 500), (date(2026, 1, 19), 500), (date(2026, 4, 2), 500)]
    assert rhythm(random) is None


def test_two_payments_on_one_day_are_one_bill_in_parts() -> None:
    parts = [(date(2026, m, 24), p) for m in range(5, 9) for p in (6512, 7362)]
    found = rhythm(parts)
    assert found is not None
    assert (found.cadence, found.typical_pence) == ("monthly", 13874)


# ---- against seven months of history ----------------------------------------------------


@pytest.fixture
def history(session: Session, imported: ImportResult) -> alerts.AlertSummary:
    for key, (fmt, text) in bf.history_files().items():
        account = session.scalars(select(Account).where(Account.key == key)).one()
        ingest.import_csv(session, account, text, f"{key}.csv", fmt, None)  # type: ignore[arg-type]
    found = alerts.run(session, Settings(), TODAY)
    session.commit()
    return found


def candidate_for(session: Session, label: str) -> RecurringCandidate:
    lines = {ln.label: ln.line_id for ln in matching.bill_lines(session, TODAY)}
    return session.scalars(
        select(RecurringCandidate).where(RecurringCandidate.line_id == lines[label])
    ).one()


def items(session: Session, kind: str) -> list[InboxItem]:
    return list(session.scalars(select(InboxItem).where(InboxItem.type == kind)))


BUDGETED = [
    "TV (Sky)",
    "TV (TV Licence)",
    "Council Tax (Exampletown)",
    "Mortgage (Northfield)",
    "Doorstep Milk",
    "Gig Club",
    "Car Insurance (1stCentral)",
    "Spotify (PayPal)",
    "Pink Ribbon",
    "Rover Physio",
    "Broadband (Vodafone)",
    "Factor Fees (Hill & Ward)",
    "Phones (EE)",
    "Buildings and Contents Insurance (Ageas)",
    "Life and Income Protection Insurance (Aviva)",
    "Windows Cleaner (Alex Reid)",
    "Glow Clinic",
    "Pet Insurance (Ultimate Pet Partners)",
    "Gas/Electricity (Octopus)",
    "Patreon",
    "Email (Google)",
    "Alarm (SimpliSafe)",
    "Premier Sports",
    "Childrens Hospice",
    "Claude AI",
    "Home Assistant Cloud (Nabu Casa)",
    "SOE (Society of Engineers)",
    "Gap insurance",
]


def test_it_finds_the_bills_we_budget_for_at_the_right_cadence(
    session: Session, history: alerts.AlertSummary
) -> None:
    for label in BUDGETED:
        found = candidate_for(session, label)
        assert (found.status, found.cadence) == ("known", "monthly"), label
    assert history.recurring.known >= len(BUDGETED)
    sky = candidate_for(session, "TV (Sky)")
    assert (sky.day_of_month, sky.typical_amount_pence) == (1, 10475)


def test_it_catches_the_section_4_6_price_changes(
    session: Session, history: alerts.AlertSummary
) -> None:
    # Before the budget existed: seen in the history, recorded on the rhythm.
    seen = {
        label: (c.previous_amount_pence, c.typical_amount_pence)
        for label in (
            "TV (Sky)",
            "Council Tax (Exampletown)",
            "Spotify (PayPal)",
            "Car Insurance (1stCentral)",
            "Broadband (Vodafone)",
            "Pet Insurance (Ultimate Pet Partners)",
        )
        for c in [candidate_for(session, label)]
    }
    assert seen == {
        "TV (Sky)": (9800, 10475),
        "Council Tax (Exampletown)": (24350, 25980),
        "Spotify (PayPal)": (1699, 1799),
        "Car Insurance (1stCentral)": (3912, 4127),
        "Broadband (Vodafone)": (4900, 2612),
        "Pet Insurance (Ultimate Pet Partners)": (9655, 13874),
    }
    # The first payment at the new price (it drifted to the 3rd that month).
    assert candidate_for(session, "TV (Sky)").price_changed_on == date(2026, 8, 3)
    # Since the budget started: proposed in the inbox, as in Phase 3.
    proposed = {
        i.payload_json["label"]: i.payload_json["new_pence"] for i in items(session, "price_change")
    }
    assert proposed["Phones (EE)"] == 4175
    # Nothing proposed for history from before the budget existed.
    assert "TV (Sky)" not in proposed


def test_it_flags_domestic_and_general_still_being_paid(
    session: Session, history: alerts.AlertSummary
) -> None:
    (flag,) = items(session, "still_paying")
    assert flag.payload_json["label"] == "Domestic and General"
    assert flag.payload_json["amount_pence"] == 1299
    # It's not also proposed as a new regular payment.
    merchants = {i.payload_json["merchant"] for i in items(session, "new_recurring")}
    assert not any("Domestic" in m for m in merchants)


def test_new_regular_payments_are_proposed_and_the_car_is_recognised(
    session: Session, history: alerts.AlertSummary
) -> None:
    proposed = {i.payload_json["merchant"]: i.payload_json for i in items(session, "new_recurring")}
    assert proposed["Netflix"]["amount_pence"] == 1099
    assert proposed["Netflix"]["cadence"] == "monthly"
    assert proposed["Netflix"]["likely_line_id"] is None
    # The car is paid to the lender: the same price as the Car line, so it's suggested.
    assert proposed["Black Horse"]["likely_label"] == "Car"
    # The weekly shop varies too much to be a bill.
    assert not any("Tesco" in m for m in proposed)


def test_a_bill_that_stopped_is_a_missed_payment(
    session: Session, history: alerts.AlertSummary
) -> None:
    (missed,) = items(session, "missed_payment")
    assert missed.payload_json["label"] == "Doorstep Milk"
    assert missed.payload_json["month"] == "2026-11-01"


def test_running_again_raises_nothing_twice(session: Session, history: alerts.AlertSummary) -> None:
    before = len(session.scalars(select(InboxItem)).all())
    again = alerts.run(session, Settings(), TODAY)
    assert (again.recurring.proposed, again.missed, again.still_paying) == (0, 0, 0)
    assert len(session.scalars(select(InboxItem)).all()) == before


# ---- answering them --------------------------------------------------------------------


def test_adding_a_new_regular_payment_to_the_budget(
    session: Session, history: alerts.AlertSummary
) -> None:
    netflix = next(
        i for i in items(session, "new_recurring") if i.payload_json["merchant"] == "Netflix"
    )
    line = inbox.accept_recurring(
        session, netflix, None, {"name": "Netflix", "category": "Subscriptions"}
    )
    added = next(ln for ln in matching.bill_lines(session, TODAY) if ln.line_id == line.id)
    assert (added.amount_pence, added.frequency, added.day_of_month, added.account) == (
        1099,
        "monthly",
        20,
        "joint_billing",
    )
    paid = session.scalars(
        select(BankTransaction).where(BankTransaction.matched_line_id == line.id)
    ).all()
    assert len(paid) == 7
    alerts.run(session, Settings(), TODAY)
    assert (
        session.scalars(
            select(RecurringCandidate).where(RecurringCandidate.merchant_normalised == "Netflix")
        )
        .one()
        .status
        == "accepted"
    )


def test_linking_the_lender_to_the_car_line(session: Session, history: alerts.AlertSummary) -> None:
    horse = next(
        i for i in items(session, "new_recurring") if i.payload_json["merchant"] == "Black Horse"
    )
    inbox.link_recurring(session, horse, None, horse.payload_json["likely_line_id"])
    assert matching.bill_coverage(session, 2026, 11)[0] >= len(BUDGETED) - 1
    car = candidate_for(session, "Car")
    assert car.status == "accepted"


def test_ignoring_stops_it_being_proposed(session: Session, history: alerts.AlertSummary) -> None:
    netflix = next(
        i for i in items(session, "new_recurring") if i.payload_json["merchant"] == "Netflix"
    )
    inbox.ignore_recurring(session, netflix, None)
    alerts.run(session, Settings(), TODAY + timedelta(days=30))
    assert (
        len([i for i in items(session, "new_recurring") if i.payload_json["merchant"] == "Netflix"])
        == 1
    )


def test_a_missed_bill_that_stopped_comes_out_of_the_budget(
    session: Session, history: alerts.AlertSummary
) -> None:
    (missed,) = items(session, "missed_payment")
    inbox.missed_stopped(session, missed, None)
    assert "Doorstep Milk" not in {ln.name for ln in matching.bill_lines(session, TODAY)}
    assert "Doorstep Milk" in {ln.name for ln in matching.bill_lines(session, date(2026, 10, 15))}


def paid_under_another_name(session: Session, missed: InboxItem) -> BankTransaction:
    """The bill went out, but under bank text the matcher doesn't know."""
    account = session.scalars(
        select(Account).where(Account.key == missed.payload_json["account"])
    ).one()
    txn = BankTransaction(
        account_id=account.id,
        external_id="milkman-nov",
        booked_date=date.fromisoformat(missed.payload_json["due_on"]) + timedelta(days=1),
        amount_pence=-missed.payload_json["last_paid_pence"],
        description="DOORSTEP DAIRY LTD",
        merchant_normalised="Doorstep Dairy",
    )
    session.add(txn)
    session.flush()
    return txn


def test_a_missed_bill_that_was_paid_is_matched_to_its_payment(
    session: Session, history: alerts.AlertSummary
) -> None:
    (missed,) = items(session, "missed_payment")
    txn = paid_under_another_name(session, missed)
    options = inbox.payment_options(session, missed)
    assert options[0].id == txn.id  # the usual amount, the day after it was due
    inbox.missed_paid(session, missed, None, txn, always=True)
    assert (missed.status, txn.matched_line_id, txn.match_method) == (
        "rejected",
        missed.line_id,
        "manual",
    )
    # Next month's comes in under the same name: the rule matches it.
    december = BankTransaction(
        account_id=txn.account_id,
        external_id="milkman-dec",
        booked_date=txn.booked_date + timedelta(days=30),
        amount_pence=txn.amount_pence,
        description="DOORSTEP DAIRY LTD",
        merchant_normalised="Doorstep Dairy",
    )
    session.add(december)
    session.flush()
    matching.run(session, [december])
    assert december.matched_line_id == missed.line_id

    # Undo: the payment is unmatched again, and the rule is gone.
    inbox.undo(session, missed, None)
    assert (missed.status, txn.matched_line_id, txn.match_method) == ("open", None, None)
    assert "paid_with" not in missed.payload_json
    rule_id = session.scalars(
        select(CategoryRule.id).where(CategoryRule.set_line_id == missed.line_id)
    ).first()
    assert rule_id is None


def test_any_inbox_item_can_be_dismissed_and_brought_back(
    session: Session, history: alerts.AlertSummary
) -> None:
    (flag,) = items(session, "still_paying")
    (missed,) = items(session, "missed_payment")
    for item in (flag, missed):
        inbox.reject(session, item, None)
        assert item.status == "rejected"
    alerts.run(session, Settings(), TODAY)
    assert {i.id for i in inbox.open_items(session, TODAY)}.isdisjoint({flag.id, missed.id})
    inbox.undo(session, flag, None)
    assert flag.status == "open"


def test_a_regular_payee_can_be_linked_from_the_regular_payments_page(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    for key, (fmt, text) in bf.history_files().items():
        client.post("/api/imports", json={"text": text, "account": key, "format": fmt})
    alerts.run(session, Settings(), TODAY)
    session.commit()
    listed = client.get("/api/recurring").json()
    lines = {ln.label: ln.line_id for ln in matching.bill_lines(session, TODAY)}

    # A new one with its inbox item still open: linking answers the item too.
    netflix = next(r for r in listed if r["payee"] == "Netflix")
    linked = client.post(
        f"/api/recurring/{netflix['id']}/link", json={"line_id": lines["TV (Sky)"]}
    )
    assert linked.status_code == 200, linked.text
    item = session.scalars(
        select(InboxItem).where(InboxItem.dedupe_key == f"new_recurring:{netflix['id']}")
    ).one()
    session.refresh(item)
    assert item.status == "approved"

    # One matched to the wrong line moves over, payments and all, and stays moved.
    sky = next(r for r in listed if r["line_label"] == "TV (Sky)")
    target = lines["Spotify (PayPal)"]
    moved = client.post(f"/api/recurring/{sky['id']}/link", json={"line_id": target})
    assert moved.status_code == 200, moved.text
    session.expire_all()
    payments = session.scalars(
        select(BankTransaction).where(
            BankTransaction.merchant_normalised == sky["payee"], BankTransaction.amount_pence < 0
        )
    ).all()
    assert payments
    assert all(t.matched_line_id == target for t in payments)
    alerts.run(session, Settings(), TODAY)
    again = next(r for r in client.get("/api/recurring").json() if r["id"] == sky["id"])
    assert again["line_label"] == "Spotify (PayPal)"
    assert (
        client.post(f"/api/recurring/{sky['id']}/link", json={"line_id": 99999}).status_code == 422
    )


def test_keeping_a_bill_marked_to_cancel(session: Session, history: alerts.AlertSummary) -> None:
    (flag,) = items(session, "still_paying")
    inbox.still_paying_keep(session, flag, None)
    follow_up = session.get(FollowUp, flag.payload_json["follow_up_id"])
    assert follow_up is not None
    assert follow_up.status == "dismissed"


# ---- Home Assistant ------------------------------------------------------------------------


class Captured:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(json.loads(request.content))
            return httpx.Response(200)

        return httpx.Client(transport=httpx.MockTransport(handler))


def ha(**extra: Any) -> Settings:
    return Settings(ha_webhook_url="http://ha.test/api/webhook/bawbee", **extra)


def test_new_inbox_items_go_to_home_assistant_in_one_batch_once(
    session: Session, history: alerts.AlertSummary
) -> None:
    captured = Captured()
    alerts.notify_ha(session, ha(), TODAY, http=captured.client())
    inbox_batches = [n for n in captured.sent if n["kind"] == "inbox"]
    assert len(inbox_batches) == 1
    message = inbox_batches[0]["message"]
    assert "Domestic and General is still being paid" in message
    assert inbox_batches[0]["data"]["count"] >= 4
    captured.sent.clear()
    alerts.notify_ha(session, ha(), TODAY, http=captured.client())
    assert [n for n in captured.sent if n["kind"] == "inbox"] == []


def test_low_balance_and_promotion_due_are_told_once(
    session: Session, history: alerts.AlertSummary
) -> None:
    what_if = session.scalars(select(BudgetVersion).where(BudgetVersion.type == "what_if")).first()
    assert what_if is not None
    what_if.planned_start = TODAY - timedelta(days=1)
    captured = Captured()
    settings = ha(ha_low_balance_pence=100000)  # a £1,000 cushion: Joint Billing falls short
    alerts.notify_ha(session, settings, TODAY, http=captured.client())
    kinds = [n["kind"] for n in captured.sent]
    assert "low_balance" in kinds
    assert "promotion_due" in kinds
    low = next(n for n in captured.sent if n["kind"] == "low_balance")
    assert "£" in low["message"]
    assert low["data"]["minimum_pence"] < 100000
    captured.sent.clear()
    alerts.notify_ha(session, settings, TODAY, http=captured.client())
    assert [n["kind"] for n in captured.sent if n["kind"] != "inbox"] == []


def test_a_failed_notification_is_recorded_not_raised(
    session: Session, history: alerts.AlertSummary
) -> None:
    broken = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
    alerts.notify_ha(session, ha(), TODAY, http=broken)  # no exception
    from app.domain.models import Notification

    assert (
        session.scalars(select(Notification).where(Notification.ok.is_(False))).first() is not None
    )


def test_the_home_assistant_sensor_needs_its_own_token(
    client: TestClient, app: Any, users: dict[str, User]
) -> None:
    anonymous = TestClient(app)
    assert anonymous.get("/api/ha/summary").status_code == 401
    made = client.post("/api/ha/tokens", json={"label": "Living room"})
    assert made.status_code == 201
    token = made.json()["token"]
    reading = anonymous.get("/api/ha/summary", headers={"Authorization": f"Bearer {token}"})
    assert reading.status_code == 200, reading.text
    body = reading.json()
    assert {"inbox", "payday", "surplus_pence", "joint_low_point", "banks"} <= set(body)
    assert body["surplus_pence"]["household"] == 45324
    # The token reads the summary only; it isn't a session.
    assert (
        anonymous.get("/api/inbox", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )
    listed = client.get("/api/ha").json()
    assert [t["label"] for t in listed["tokens"]] == ["Living room"]
    assert listed["tokens"][0]["last_used_at"] is not None
    assert client.delete(f"/api/ha/tokens/{made.json()['id']}").status_code == 204
    assert (
        anonymous.get("/api/ha/summary", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )
    # No webhook set: the test notification says so.
    assert client.post("/api/ha/test").status_code == 502


def test_recurring_payments_are_listed(
    client: TestClient, session: Session, users: dict[str, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, (fmt, text) in bf.history_files().items():
        client.post("/api/imports", json={"text": text, "account": key, "format": fmt})
    alerts.run(session, Settings(), TODAY)
    session.commit()
    listed = client.get("/api/recurring").json()
    sky = next(r for r in listed if r["line_label"] == "TV (Sky)")
    assert (sky["cadence"], sky["previous_amount_pence"], sky["amount_pence"]) == (
        "monthly",
        9800,
        10475,
    )
    assert any(r["payee"] == "Netflix" and r["status"] == "new" for r in listed)


# ---- every answer that changes the budget can be taken back -------------------------------


def test_undoing_an_added_regular_payment_takes_the_line_back_out(
    session: Session, history: alerts.AlertSummary
) -> None:
    netflix = next(
        i for i in items(session, "new_recurring") if i.payload_json["merchant"] == "Netflix"
    )
    line = inbox.accept_recurring(session, netflix, None, {"name": "Netflix"})
    line_id = line.id
    inbox.undo(session, netflix, None)
    assert netflix.status == "open"
    assert line_id not in {ln.line_id for ln in matching.bill_lines(session, TODAY)}
    matched = session.scalars(
        select(BankTransaction).where(BankTransaction.matched_line_id == line_id)
    ).all()
    assert matched == []
    candidate = session.scalars(
        select(RecurringCandidate).where(RecurringCandidate.merchant_normalised == "Netflix")
    ).one()
    assert (candidate.status, candidate.line_id) == ("new", None)
    # And it can be added again.
    inbox.accept_recurring(session, netflix, None, {"name": "Netflix"})


def test_undoing_a_link_unmatches_its_payments(
    session: Session, history: alerts.AlertSummary
) -> None:
    horse = next(
        i for i in items(session, "new_recurring") if i.payload_json["merchant"] == "Black Horse"
    )
    car = horse.payload_json["likely_line_id"]
    inbox.link_recurring(session, horse, None, car)
    inbox.undo(session, horse, None)
    assert (
        session.scalars(
            select(BankTransaction).where(
                BankTransaction.matched_line_id == car,
                BankTransaction.merchant_normalised == "Black Horse",
            )
        ).all()
        == []
    )
    assert "Car" in {ln.name for ln in matching.bill_lines(session, TODAY)}


def test_undoing_not_a_bill_and_suggesting_it_again(
    session: Session, history: alerts.AlertSummary, client: TestClient
) -> None:
    netflix = next(
        i for i in items(session, "new_recurring") if i.payload_json["merchant"] == "Netflix"
    )
    inbox.ignore_recurring(session, netflix, None)
    inbox.undo(session, netflix, None)
    candidate_id = netflix.payload_json["candidate_id"]
    assert session.get(RecurringCandidate, candidate_id).status == "new"  # type: ignore[union-attr]
    inbox.ignore_recurring(session, netflix, None)
    session.commit()
    assert client.post(f"/api/recurring/{candidate_id}/suggest").status_code == 204
    session.refresh(netflix)
    assert netflix.status == "open"


def test_undoing_its_stopped_puts_the_bill_back(
    session: Session, history: alerts.AlertSummary
) -> None:
    (missed,) = items(session, "missed_payment")
    inbox.missed_stopped(session, missed, None)
    assert "Doorstep Milk" not in {ln.name for ln in matching.bill_lines(session, TODAY)}
    inbox.undo(session, missed, None)
    assert "Doorstep Milk" in {ln.name for ln in matching.bill_lines(session, TODAY)}
    assert "Doorstep Milk" in {ln.name for ln in matching.bill_lines(session, date(2027, 3, 1))}


def test_undoing_keep_paying_reopens_the_cancel_follow_up(
    session: Session, history: alerts.AlertSummary
) -> None:
    (flag,) = items(session, "still_paying")
    # Everything taken in the last six weeks, added up.
    assert flag.payload_json["taken_pence"] == flag.payload_json["payments"] * 1299
    inbox.still_paying_keep(session, flag, None)
    inbox.undo(session, flag, None)
    follow_up = session.get(FollowUp, flag.payload_json["follow_up_id"])
    assert follow_up is not None
    assert follow_up.status == "open"


def test_the_cancel_chore_waits_behind_its_still_paying_item(
    client: TestClient, session: Session, history: alerts.AlertSummary
) -> None:
    def chores() -> list[dict[str, Any]]:
        follow_ups: list[dict[str, Any]] = client.get("/api/inbox").json()["follow_ups"]
        return follow_ups

    (flag,) = items(session, "still_paying")
    follow_up_id = flag.payload_json["follow_up_id"]
    assert follow_up_id not in {f["id"] for f in chores()}
    inbox.still_paying_cancelled(session, flag, None)
    session.commit()
    watched = next(f for f in chores() if f["id"] == follow_up_id)
    assert watched["watching_since"] is not None
