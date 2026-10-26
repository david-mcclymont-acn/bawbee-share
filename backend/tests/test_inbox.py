"""Price changes in the inbox, and what each what-if does about them.

Phase 3's acceptance check: a price change can be approved and offered to the what-ifs
that override it.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import editing
from app.domain.models import BudgetVersion, InboxItem, User
from app.domain.versions import conflicts, live_version, resolve
from app.importers.seed import ImportResult
from app.sync import inbox, ingest
from tests import bank_fixtures as bf
from tests.test_matching import NOV, account, import_month, line_id


def sky_item(session: Session) -> InboxItem:
    return session.scalars(select(InboxItem).where(InboxItem.type == "price_change")).one()


def sky_in(session: Session, version: BudgetVersion, when: date = NOV) -> int | None:
    sky = line_id(session, "TV", "joint_billing", "Sky")
    return resolve(session, version, when).by_id()[sky].amount_pence


def what_if_with_sky_at(session: Session, name: str, pence: int) -> BudgetVersion:
    version = editing.create_what_if(session, name, live_version(session), None)
    sky = line_id(session, "TV", "joint_billing", "Sky")
    editing.override_line(session, version, sky, {"amount_pence": pence}, NOV, None)
    return version


def test_approving_moves_live_from_the_day_it_was_seen_and_offers_each_what_if(
    session: Session, imported: ImportResult
) -> None:
    import_month(session)
    cheaper = what_if_with_sky_at(session, "Haggle with Sky", 9800)
    item = sky_item(session)

    offered = inbox.approve_price_change(session, item, None)
    live = live_version(session)
    assert sky_in(session, live) == 10850
    assert sky_in(session, live, date(2026, 10, 31)) == 10475
    assert item.status == "approved"
    assert [(o.version_id, o.override_pence) for o in offered if o.name == cheaper.name] == [
        (cheaper.id, 9800)
    ]
    # The live line moved under the what-if: it's a conflict until someone decides.
    assert any(c.line_id == item.line_id for c in conflicts(session, cheaper, NOV))

    with pytest.raises(inbox.InboxError, match="already"):
        inbox.approve_price_change(session, item, None)


def test_keep_take_or_set_for_each_overriding_what_if(
    session: Session, imported: ImportResult
) -> None:
    import_month(session)
    keep = what_if_with_sky_at(session, "Keep £98", 9800)
    take = what_if_with_sky_at(session, "Take the new price", 9800)
    other = what_if_with_sky_at(session, "Something else", 9800)
    item = sky_item(session)
    with pytest.raises(inbox.InboxError, match="Approve"):
        inbox.decide_what_if(session, item, keep, "keep", None)
    inbox.approve_price_change(session, item, None)

    inbox.decide_what_if(session, item, keep, "keep", None)
    assert sky_in(session, keep) == 9800
    assert not [c for c in conflicts(session, keep, NOV) if c.line_id == item.line_id]

    inbox.decide_what_if(session, item, take, "take", None)
    assert sky_in(session, take) == 10850
    assert not take.overrides

    with pytest.raises(inbox.InboxError, match="amount"):
        inbox.decide_what_if(session, item, other, "set", None)
    inbox.decide_what_if(session, item, other, "set", None, 12500)
    assert sky_in(session, other) == 12500
    assert not [c for c in conflicts(session, other, NOV) if c.line_id == item.line_id]
    assert inbox.awaiting_what_ifs(session) == []


def test_what_if_choices_wait_on_the_item_until_each_is_made(
    session: Session, imported: ImportResult
) -> None:
    import_month(session)
    first = what_if_with_sky_at(session, "First", 9800)
    second = what_if_with_sky_at(session, "Second", 9800)
    item = sky_item(session)
    inbox.approve_price_change(session, item, None)
    # A refresh, or another approval, finds them still waiting.
    assert inbox.awaiting_what_ifs(session) == [item]
    assert {w.version_id for w in inbox.pending_what_ifs(session, item)} == {first.id, second.id}
    inbox.decide_what_if(session, item, first, "keep", None)
    assert [w.version_id for w in inbox.pending_what_ifs(session, item)] == [second.id]
    # One choice made: the approval stands now.
    with pytest.raises(inbox.InboxError, match="decided"):
        inbox.undo(session, item, None)
    inbox.decide_what_if(session, item, second, "take", None)
    assert inbox.awaiting_what_ifs(session) == []


def test_undo_puts_an_approval_back_in_the_inbox_and_live_back_as_it_was(
    session: Session, imported: ImportResult
) -> None:
    import_month(session)
    item = sky_item(session)
    live = live_version(session)
    inbox.approve_price_change(session, item, None)
    assert sky_in(session, live) == 10850
    inbox.undo(session, item, None)
    assert item.status == "open"
    assert sky_in(session, live) == 10475
    assert sky_in(session, live, date(2027, 6, 1)) == 10475
    # And it can be approved again, the same as before.
    inbox.approve_price_change(session, item, None)
    assert sky_in(session, live) == 10850

    other = sky_item(session)
    inbox.undo(session, other, None)
    inbox.reject(session, other, None)
    inbox.undo(session, other, None)
    assert other.status == "open"
    inbox.snooze(session, other, None, None, date(2026, 11, 2))
    inbox.undo(session, other, None)
    assert (other.status, other.snoozed_until) == ("open", None)
    with pytest.raises(inbox.InboxError, match="still open"):
        inbox.undo(session, other, None)


def test_not_a_change_and_snooze(session: Session, imported: ImportResult) -> None:
    import_month(session)
    item = sky_item(session)
    today = date(2026, 11, 2)
    inbox.snooze(session, item, None, None, today)
    assert item.snoozed_until == date(2026, 11, 9)
    assert inbox.open_items(session, today) == []
    assert inbox.open_items(session, date(2026, 11, 9)) == [item]

    inbox.reject(session, item, None)
    assert item.status == "rejected"
    assert sky_in(session, live_version(session)) == 10475
    # Seeing the same price next month doesn't ask again: it's been answered.
    dec = bf.starling_csv([(date(2026, 12, 1), "SKY DIGITAL|SKY0123456", -10850)])
    summary = ingest.import_csv(session, account(session, "joint_billing"), dec, None, None, None)
    assert (summary.bills_matched, summary.price_changes) == (1, 0)
    assert sky_item(session).id == item.id


# ---- over HTTP ----------------------------------------------------------------------------


def test_import_then_approve_over_the_api(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    _fmt, text = bf.month_files()["joint_billing"]
    preview = client.post("/api/imports/preview", json={"text": text, "account": "joint_billing"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["format"], body["new"], body["duplicates"], body["last_format"]) == (
        "starling",
        len(bf.JOINT_BILLING),
        0,
        None,
    )

    made = client.post(
        "/api/imports",
        json={"text": text, "account": "joint_billing", "filename": "joint.csv"},
    )
    assert made.status_code == 201, made.text
    summary = made.json()
    assert summary["price_changes"] == 1
    assert summary["bills_matched"] >= 18

    what_if = client.post(
        "/api/versions",
        json={"name": "Haggle with Sky", "base_version_id": live_version(session).id},
    )
    assert what_if.status_code == 201, what_if.text
    sky = line_id(session, "TV", "joint_billing", "Sky")
    changed = client.patch(
        f"/api/versions/{what_if.json()['id']}/lines/{sky}",
        json={"changes": {"amount_pence": 9800}},
    )
    assert changed.status_code == 204, changed.text

    listing = client.get("/api/inbox").json()
    price = next(i for i in listing["items"] if i["kind"] == "price_change")
    assert (price["label"], price["old_pence"], price["new_pence"]) == ("TV (Sky)", 10475, 10850)
    assert [o["name"] for o in price["overriding"]] == ["Haggle with Sky"]
    # The count is what the bank proposes; the spreadsheet's follow-ups are chores, uncounted.
    assert listing["count"] == 1
    assert client.get("/api/inbox/count").json()["count"] == 1
    assert listing["follow_ups"]
    assert listing["chores_count"] >= len(listing["follow_ups"])

    approved = client.post(f"/api/inbox/{price['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["effective_from"] == "2026-11-01"
    assert [w["name"] for w in approved.json()["pending"]] == ["Haggle with Sky"]
    # Still waiting on the what-if's choice, so still counted, and still listed after a refresh.
    waiting = client.get("/api/inbox").json()
    assert waiting["count"] == 1
    assert waiting["items"][0]["status"] == "approved"
    decided = client.post(
        f"/api/inbox/{price['id']}/what-ifs",
        json={"version_id": what_if.json()["id"], "decision": "take"},
    )
    assert decided.status_code == 204, decided.text
    again = client.post(f"/api/inbox/{price['id']}/approve", json={})
    assert again.status_code == 422
    assert client.get("/api/inbox/count").json()["count"] == 0
    # Decided on, so the approval stands.
    assert client.post(f"/api/inbox/{price['id']}/undo").status_code == 422

    audit = session.execute(
        select(InboxItem.resolved_by).where(InboxItem.id == price["id"])
    ).scalar_one()
    assert audit == users["person_1"].id


def test_transactions_filter_and_match_over_the_api(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    for key, (_fmt, text) in bf.month_files().items():
        assert client.post("/api/imports", json={"text": text, "account": key}).status_code == 201

    # The bank's transfers ticked October's payday, and say so.
    october = client.get("/api/payday/2026/10").json()
    assert october["complete"] is True
    assert {t["done_via"] for t in october["transfers"]} == {"bank"}

    nov = client.get("/api/transactions", params={"month": "2026-11", "account": "person_2_rbs"})
    assert {t["booked_date"][:7] for t in nov.json()} == {"2026-11"}
    unmatched = client.get(
        "/api/transactions", params={"status": "needs", "q": "black horse"}
    ).json()
    assert len(unmatched) == 1
    # Groceries are categorised, not something to look at; transfers count as matched.
    nov = {"month": "2026-11"}
    needs = client.get("/api/transactions", params=nov | {"status": "needs"}).json()
    categorised = client.get("/api/transactions", params=nov | {"status": "categorised"}).json()
    assert {t["category"] for t in categorised} == {"Groceries"}
    assert not {t["id"] for t in needs} & {t["id"] for t in categorised}
    assert all(t["amount_pence"] < 0 and t["category"] is None for t in needs)
    october = client.get("/api/transactions", params={"month": "2026-10", "status": "matched"})
    assert {t["match_method"] for t in october.json()} == {"transfer"}
    car = line_id(session, "Car", "person_2_rbs")

    r = client.patch(
        f"/api/transactions/{unmatched[0]['id']}", json={"line_id": car, "always": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["transaction"]["line_name"] == "Car"
    assert r.json()["transaction"]["match_method"] == "manual"
    assert len(client.get("/api/rules").json()) == 1

    alias = client.post("/api/aliases", json={"pattern": "NARWHAL", "merchant": "Narwahl"})
    assert alias.json() == {"newly_matched": 1}

    actuals = client.get("/api/actuals", params={"month": "2026-11"}).json()
    assert actuals["covered"]["person_2_rbs"] == {"from": "2026-11-02", "to": "2026-11-12"}
    assert "person_1_savings" not in actuals["covered"]
    car_actual = next(a for a in actuals["lines"] if a["line_id"] == car)
    assert car_actual["status"] == "paid"

    assert (
        client.post(
            "/api/imports", json={"text": "Date,Memo\n", "account": "joint_billing"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/imports", json={"text": bf.rbs_csv(bf.PERSON_2_RBS)}).status_code == 422
    )


def test_bank_data_needs_a_session(app: object, users: dict[str, User]) -> None:
    anonymous = TestClient(app)  # type: ignore[arg-type]
    assert anonymous.get("/api/transactions").status_code == 401
    assert anonymous.get("/api/inbox").status_code == 401
