"""The payday checklist (PLAN.md §5.7; Phase 2: recreate this month's payday from the app)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models import User
from app.importers.seed import ImportResult
from tests.conftest import login


def _by_name(view: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {f"{t['from_account']}->{t['to_account']}": t for t in view["transfers"]}


def test_october_payday_shows_what_to_move(client: TestClient, imported: ImportResult) -> None:
    view = client.get("/api/payday/2026/10").json()
    assert (view["month"], view["funds_month"]) == ("2026-10-01", "2026-11-01")
    assert view["pay_dates"]["person_1"] == {"official": "2026-10-30", "expected": "2026-10-30"}
    assert view["pay_dates"]["person_2"] == {"official": "2026-10-29", "expected": "2026-10-28"}
    assert view["transfer_date"] == "2026-10-30"
    assert view["run_id"] is None

    transfers = _by_name(view)
    # The amounts the Maternity sheet has you move (Monzo Investment is £0, so it's left off).
    assert transfers["person_1_monzo->joint_billing"]["effective_pence"] == 267000
    assert transfers["person_2_rbs->joint_billing"]["effective_pence"] == 70000
    assert transfers["person_1_monzo->person_1_starling"]["effective_pence"] == 13500
    assert transfers["person_1_monzo->person_1_savings"]["effective_pence"] == 40000
    assert "person_1_monzo->monzo_investment" not in transfers
    assert transfers["person_2_rbs->joint_billing"]["from_owner"] == "person_2"
    # Suggested and typed-in amounts are both kept.
    joint = transfers["person_1_monzo->joint_billing"]
    assert joint["actual_pence"] == 267000
    assert joint["suggested_pence"] < joint["actual_pence"]


def test_ticking_off_payday(app: FastAPI, users: dict[str, User]) -> None:
    person_1, person_2 = login(app, "person_1"), login(app, "person_2")
    started = person_1.post("/api/payday/2026/10/start").json()
    assert started["run_id"] is not None
    assert person_1.post("/api/payday/2026/10/start").json()["run_id"] == started["run_id"]

    transfers = _by_name(started)
    person_2_joint = transfers["person_2_rbs->joint_billing"]["id"]
    view = person_2.patch(f"/api/payday/transfers/{person_2_joint}", json={"done": True}).json()
    ticked = _by_name(view)["person_2_rbs->joint_billing"]
    assert ticked["done_by"] == "Person 2"
    assert ticked["done_at"] is not None
    assert view["complete"] is False

    savings = transfers["person_1_monzo->person_1_savings"]["id"]
    view = person_1.patch(f"/api/payday/transfers/{savings}", json={"actual_pence": 30000}).json()
    assert _by_name(view)["person_1_monzo->person_1_savings"]["effective_pence"] == 30000
    view = person_1.patch(f"/api/payday/transfers/{savings}", json={"actual_pence": None}).json()
    assert _by_name(view)["person_1_monzo->person_1_savings"]["effective_pence"] == 40000

    for t in view["transfers"]:
        view = person_1.patch(f"/api/payday/transfers/{t['id']}", json={"done": True}).json()
    assert view["complete"] is True
    undone = person_1.patch(f"/api/payday/transfers/{person_2_joint}", json={"done": False}).json()
    assert undone["complete"] is False
    assert _by_name(undone)["person_2_rbs->joint_billing"]["done_by"] is None


def test_a_started_run_keeps_its_amounts(client: TestClient, imported: ImportResult) -> None:
    run = client.post("/api/payday/2026/10/start").json()
    savings_line = _by_name(run)["person_1_monzo->person_1_savings"]["line_id"]
    client.patch(
        f"/api/versions/{imported.live.id}/lines/{savings_line}",
        json={"changes": {"amount_pence": 99900}, "effective_from": "2026-10-01"},
    )
    again = _by_name(client.get("/api/payday/2026/10").json())
    assert again["person_1_monzo->person_1_savings"]["effective_pence"] == 40000
    # A payday not yet started follows the budget.
    november = _by_name(client.get("/api/payday/2026/11").json())
    assert november["person_1_monzo->person_1_savings"]["effective_pence"] == 99900


def test_bad_month_is_rejected(client: TestClient) -> None:
    assert client.get("/api/payday/2026/13").status_code == 422


def test_each_transfer_says_why_its_amount_is_what_it_is(
    client: TestClient, imported: ImportResult
) -> None:
    view = _by_name(client.get("/api/payday/2026/10").json())
    joint = view["person_1_monzo->joint_billing"]
    # The Maternity budget types in £2,670 rather than the calculated share.
    assert (joint["basis"], joint["budget_pence"], joint["rule"]) == (
        "fixed",
        267000,
        "joint_share",
    )
    starling = view["person_1_monzo->person_1_starling"]
    assert (starling["basis"], starling["rule"]) == ("calculated", "cover_account_lines")

    run = client.post("/api/payday/2026/10/start").json()
    savings = _by_name(run)["person_1_monzo->person_1_savings"]
    changed = _by_name(
        client.patch(f"/api/payday/transfers/{savings['id']}", json={"actual_pence": 30000}).json()
    )["person_1_monzo->person_1_savings"]
    assert (changed["basis"], changed["budget_pence"], changed["effective_pence"]) == (
        "changed",
        40000,
        30000,
    )
