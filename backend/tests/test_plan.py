"""Phases 6 and 7: forecast and compare, take-home, childcare linking, the mortgage,
renewals, goals, and the planning reminders (PLAN.md §5.5-5.8, §9, §10, §14)."""

from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import editing, taxconfig
from app.domain import mortgage as mortgage_domain
from app.domain.models import (
    Account,
    AuditLog,
    BankTransaction,
    BudgetLine,
    BudgetVersion,
    InboxItem,
    IncomeSource,
    Person,
    User,
)
from app.domain.versions import live_version, resolve
from app.engine import calendar, tax
from app.engine.mortgage import add_months
from app.sync import alerts, plan_alerts

TODAY = date(2026, 10, 23)
STATE = Path(__file__).resolve().parents[2] / "reference" / "childcare-app" / "state.json"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in ("app.api.plan", "app.api.versions", "app.api.inbox"):
        monkeypatch.setattr(f"{module}.today", lambda: TODAY)


def source(session: Session, person: str) -> IncomeSource:
    return session.scalars(select(IncomeSource).join(Person).where(Person.key == person)).one()


def line(session: Session, name: str, account: str | None = None) -> BudgetLine:
    query = select(BudgetLine).where(BudgetLine.name == name, BudgetLine.status == "active")
    if account:
        query = query.join(Account, BudgetLine.account_id == Account.id).where(
            Account.key == account
        )
    return session.scalars(query).first()  # type: ignore[return-value]


def what_if(session: Session, user: User, name: str, person_2_net: int) -> BudgetVersion:
    version = editing.create_what_if(session, name, live_version(session), user)
    editing.set_net_income(session, version, source(session, "person_2"), person_2_net, user)
    session.commit()
    return version


def path(client: TestClient, name: str, *steps: tuple[int, str]) -> int:
    made = client.post(
        "/api/plan/paths",
        json={"name": name, "steps": [{"version_id": v, "from_month": m} for v, m in steps]},
    )
    assert made.status_code == 201, made.text
    return int(made.json()["id"])


def month(run: dict[str, Any], on: str) -> dict[str, Any]:
    return next(m for m in run["months"] if m["month"] == on)


# ---- the engines and config -----------------------------------------------------------


def test_take_home_for_2026_27_from_the_cited_config() -> None:
    """£30,000, S1257L, no pension: Scottish bands above an allowance of £12,579."""
    year = taxconfig.year("2026-27")
    pay = tax.take_home(Fraction(30000), year, tax_code="S1257L")
    # Starter £3,967 @ 19% + basic £12,989 @ 20% + intermediate £465 @ 21%.
    assert pay.tax == Fraction("753.73") + Fraction("2597.80") + Fraction("97.65")
    assert pay.ni == (Fraction(30000) - 12570) * Fraction(8, 100)
    assert pay.net == Fraction(30000) - pay.tax - pay.ni
    assert [s.label for s in pay.tax_slices] == ["Starter", "Basic", "Intermediate"]


def test_the_allowance_tapers_over_100k_and_nhs_tiers_are_on_actual_pay() -> None:
    year = taxconfig.year("2026-27")
    rich = tax.take_home(Fraction(110000), year, tax_code="S1257L")
    assert rich.tax > tax.take_home(Fraction(110000), year, tax_code="S2000L").tax - 10000
    part_time = tax.take_home(Fraction(30000), year, pension="nhs_scotland")
    assert part_time.pension_rate == Fraction("7.0")  # £28,988 to £34,302 (Table 2)


def test_scottish_bank_holidays_2026_and_2027_match_gov_uk() -> None:
    assert date(2026, 6, 15) in calendar.scottish_bank_holidays(2026)  # the World Cup one
    assert sorted(calendar.scottish_bank_holidays(2027)) == [
        date(2027, 1, 1),
        date(2027, 1, 4),
        date(2027, 3, 26),
        date(2027, 5, 3),
        date(2027, 5, 31),
        date(2027, 8, 2),
        date(2027, 11, 30),
        date(2027, 12, 27),
        date(2027, 12, 28),
    ]


def test_funded_hours_start_the_term_after_the_third_birthday() -> None:
    assert taxconfig.funded_from(date(2024, 5, 10), "2026-27") == date(2027, 8, 1)
    assert taxconfig.funded_from(date(2024, 10, 2), "2026-27") == date(2028, 1, 1)
    assert taxconfig.funded_from(date(2024, 1, 15), "2026-27") == date(2027, 4, 1)


# ---- Phase 6: two paths compared (acceptance) --------------------------------------------


def test_return_3_days_and_return_4_days_compare_on_chart_and_table(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    live = live_version(session)
    three = what_if(session, users["person_1"], "Return 3 days", 250000)
    four = what_if(session, users["person_1"], "Return 4 days", 320000)
    groceries = line(session, "Groceries")
    groceries_now = resolve(session, live, TODAY).by_id()[groceries.id].amount_pence or 0
    editing.override_line(
        session,
        four,
        groceries.id,
        {"amount_pence": groceries_now + 5000},
        TODAY,
        users["person_1"],
    )
    session.commit()
    a = path(client, "Back 3 days from April", (live.id, "2026-11-01"), (three.id, "2027-04-01"))
    b = path(client, "Back 4 days from April", (live.id, "2026-11-01"), (four.id, "2027-04-01"))

    got = client.get("/api/plan/forecast", params={"path": [a, b]})
    assert got.status_code == 200, got.text
    runs = got.json()["runs"]
    assert [r["name"] for r in runs] == ["Back 3 days from April", "Back 4 days from April"]
    assert len(runs[0]["months"]) == 36
    assert runs[0]["months"][0]["month"] == "2026-11-01"
    # Identical until April, then apart by the take-home and groceries difference.
    assert (
        month(runs[0], "2027-03-01")["household_pence"]
        == month(runs[1], "2027-03-01")["household_pence"]
    )
    gap = (
        month(runs[1], "2027-04-01")["household_pence"]
        - month(runs[0], "2027-04-01")["household_pence"]
    )
    assert gap == (320000 - 250000) - 5000
    assert "Return 4 days from here" in month(runs[1], "2027-04-01")["events"]

    table = client.get("/api/plan/compare", params={"version": [three.id, four.id]})
    assert table.status_code == 200, table.text
    body = table.json()
    assert [v["version"]["name"] for v in body["versions"]] == ["Return 3 days", "Return 4 days"]
    assert {"gross", "take_home"} <= set(body["versions"][0]["split_options"])
    differing = [ln["line_id"] for ln in body["lines"] if ln["differs"]]
    assert differing == [groceries.id]


def test_one_offs_opening_balances_and_paths_are_audited(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    base = client.get("/api/plan/forecast").json()
    assert base["runs"][0]["path_id"] == 0
    assert (
        client.put("/api/plan/opening/person_1_savings", json={"pence": 1_000_000}).status_code
        == 204
    )
    made = client.post(
        "/api/plan/one-offs",
        json={
            "label": "Holiday",
            "account": "joint_billing",
            "on": "2027-02-14",
            "amount_pence": -150000,
        },
    )
    assert made.status_code == 201, made.text
    after = client.get("/api/plan/forecast").json()
    savings = next(a for a in after["accounts"] if a["key"] == "person_1_savings")
    assert (savings["opening_pence"], savings["opening_source"]) == (1_000_000, "entered")
    before_feb = month(base["runs"][0], "2027-02-01")["balances"]["joint_billing"]
    assert month(after["runs"][0], "2027-02-01")["balances"]["joint_billing"] == before_feb - 150000
    actions = set(
        session.scalars(select(AuditLog.entity).where(AuditLog.user_id == users["person_1"].id))
    )
    assert {"one_off", "account"} <= actions


# ---- Phase 6: take-home within £5, checked against the salary that lands ----------------


def salary(session: Session, account: str, on: date, pence: int) -> None:
    acct = session.scalars(select(Account).where(Account.key == account)).one()
    session.add(
        BankTransaction(
            account_id=acct.id,
            external_id=f"salary-{on}",
            booked_date=on,
            amount_pence=pence,
            description="EXAMPLE LTD SALARY",
            merchant_normalised="EXAMPLE LTD",
            status="booked",
        )
    )
    session.flush()


def test_pay_within_five_pounds_is_quiet_and_a_bigger_gap_is_asked_about(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    expected = resolve(session, live_version(session), TODAY).incomes
    person_1_net = next(i.net_monthly_pence for i in expected if i.person == "person_1")
    assert person_1_net is not None
    salary(session, "person_1_monzo", date(2026, 9, 30), person_1_net + 400)  # £4 over: fine
    assert plan_alerts.check_salaries(session, TODAY) == 0
    salary(
        session, "person_1_monzo", date(2026, 10, 1) - timedelta(days=0), 0
    )  # ignored: not a salary
    session.query(BankTransaction).filter(
        BankTransaction.external_id == "salary-2026-09-30"
    ).delete()
    salary(session, "person_1_monzo", date(2026, 9, 30), person_1_net + 2500)
    assert plan_alerts.check_salaries(session, TODAY) == 1
    session.commit()

    item = session.scalars(select(InboxItem).where(InboxItem.type == "salary_mismatch")).one()
    assert item.payload_json["landed_pence"] == person_1_net + 2500
    view = client.get("/api/plan/take-home").json()
    person_1 = next(p for p in view["people"] if p["person"] == "person_1")
    assert person_1["salaries"][-1]["difference_pence"] == 2500
    assert client.post(f"/api/inbox/{item.id}/take").status_code == 204
    now = resolve(session, live_version(session), TODAY).incomes
    assert next(i.net_monthly_pence for i in now if i.person == "person_1") == person_1_net + 2500
    assert client.post(f"/api/inbox/{item.id}/undo").status_code == 204
    session.expire_all()
    back = resolve(session, live_version(session), TODAY).incomes
    assert next(i.net_monthly_pence for i in back if i.person == "person_1") == person_1_net


def test_income_details_feed_the_calculation_and_can_set_the_budget(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    person_2 = source(session, "person_2")
    body = {
        "gross_annual_pence": 6200000,
        "benefits_allowance_pence": None,
        "salary_sacrifice_pence": None,
        "pension_scheme": "nhs_scotland",
        "pension_pct": None,
        "tax_code": "s1257l",
        "payslip_net_monthly_pence": 330412,
        "payslip_on": "2026-03-31",
    }
    assert client.put(f"/api/plan/take-home/{person_2.id}", json=body).status_code == 200
    view = client.get("/api/plan/take-home").json()
    person = next(p for p in view["people"] if p["person"] == "person_2")
    assert person["income"]["tax_code"] == "S1257L"
    assert person["calculated"]["pension_rate"] == "11.6"
    assert view["tax_year"] == "2026-27"
    assert view["exact_year"] is True
    assert client.post(f"/api/plan/take-home/{person_2.id}/use").status_code == 204
    incomes = resolve(session, live_version(session), TODAY).incomes
    assert (
        next(i.net_monthly_pence for i in incomes if i.person == "person_2")
        == person["calculated"]["net_monthly_pence"]
    )


# ---- Phase 6: childcare, linked to a what-if --------------------------------------------


def test_childcare_imports_links_to_a_what_if_and_follows_the_children_s_ages(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    import json

    state = json.loads(STATE.read_text(encoding="utf-8"))
    assert client.post("/api/plan/childcare/import", json=state).json() == {
        "children": 2,
        "patterns": 4,
        "nurseries": 3,
    }
    view = client.get("/api/plan/childcare").json()
    assert len(view["cells"]) == 12
    for cell in view["cells"]:
        assert abs(cell["monthly_left_pence"] * 12 - cell["annual_left_pence"]) <= 12
    assert view["payslip_check"]["payslip_pence"] == 297679

    child_1 = view["children"][0]
    client.put(
        f"/api/plan/childcare/children/{child_1['id']}",
        json={"name": "Child 1", "born_on": "2024-02-20", "eligible_at_two": False},
    )
    acorns = next(n for n in view["nurseries"] if n["name"] == "Little Acorns")
    three_days = next(p for p in view["patterns"] if p["days"] == "3")
    made = client.post(
        "/api/plan/childcare/scenarios",
        json={
            "name": "3 days",
            "nursery_id": acorns["id"],
            "pattern_id": three_days["id"],
            "account": "joint_billing",
        },
    ).json()
    live = live_version(session)
    assert (
        client.put(
            f"/api/plan/childcare/scenarios/{made['id']}/link", json={"version_id": live.id}
        ).status_code
        == 422
    )
    back = what_if(session, users["person_1"], "Back at work", 0)
    assert (
        client.put(
            f"/api/plan/childcare/scenarios/{made['id']}/link", json={"version_id": back.id}
        ).status_code
        == 204
    )

    session.expire_all()
    resolved = resolve(session, back, date(2026, 11, 1))
    nursery = next(ln for ln in resolved.lines if ln.name == "Nursery")
    assert nursery.origin == "computed"
    assert nursery.account == "joint_billing"
    assert any(
        ln.name == "Tax-Free Childcare top-up" and (ln.amount_pence or 0) < 0
        for ln in resolved.lines
    )
    person_2 = next(i for i in resolved.incomes if i.person == "person_2")
    cell = next(
        c
        for c in view["cells"]
        if c["nursery_id"] == acorns["id"] and c["pattern_id"] == three_days["id"]
    )
    assert abs((person_2.net_monthly_pence or 0) * 12 - cell["pay"]["net_pence"]) <= 12
    # Child 1 turns 3 in February 2027: funded hours from the summer term (April).
    later = resolve(session, back, date(2027, 4, 1))
    funded_bill = next(ln for ln in later.lines if ln.name == "Nursery").amount_pence or 0
    assert funded_bill < (nursery.amount_pence or 0)
    run = client.get(
        "/api/plan/forecast",
        params={"path": [path(client, "Back now", (back.id, "2026-11-01"))]},
    ).json()["runs"][0]
    assert "Child 1 starts funded hours" in month(run, "2027-04-01")["events"]
    assert month(run, "2027-04-01")["household_pence"] > month(run, "2027-03-01")["household_pence"]


# ---- Phase 7: the mortgage, the remortgage what-if on a path, the autumn 2027 cluster ----


@pytest.fixture
def mortgage(session: Session, seed: dict[str, Any], users: dict[str, User]) -> None:
    mortgage_domain.import_from_seed(session, seed, users["person_1"], TODAY)
    session.commit()


def test_the_mortgage_from_the_seed_holds_the_payment_in_one_place(
    client: TestClient, session: Session, mortgage: None
) -> None:
    view = client.get("/api/plan/mortgage").json()
    m = view["mortgage"]
    assert (m["lender"], m["rate_pct"], m["payment_pence"]) == ("Northfield", "2.79", 119380)
    assert m["fix_end_on"] == "2027-11-01"
    assert m["fix_end_exact"] is False
    # The sheet had £1,192.46; the line now follows the mortgage's £1,193.80.
    assert m["line_amount_pence"] == 119380
    assert view["months_to_fix_end"] == 13
    assert view["remortgage_window"] == "2027-04-01"
    assert 26_400_000 < view["balance_pence"] < 26_550_000  # about £265k (PLAN.md §10)
    assert view["ltv"]["band"] == 85
    assert view["ltv"]["next_band"] == 80
    assert [h["lender"] for h in view["history"]] == [
        "Oak Bank",
        "Elm Lending",
        "Elm Lending",
        "Ash BS",
    ]
    assert mortgage_domain.import_from_seed(session, {"mortgage": {}}, None, TODAY) is not None


def test_a_statement_anchors_the_schedule(
    client: TestClient, session: Session, mortgage: None
) -> None:
    estimate = client.get("/api/plan/mortgage").json()["balance_pence"]
    statement = estimate - 15000  # the lender's figure, £150 under the estimate
    made = client.post(
        "/api/plan/mortgage/statements", json={"on": "2026-09-30", "balance_pence": statement}
    )
    assert made.status_code == 201
    view = client.get("/api/plan/mortgage").json()
    assert view["balance_anchored"] is True
    assert view["balance_pence"] == statement
    # From the anchor, the schedule runs on from the statement balance.
    first = view["schedule"][0]
    assert first["opening_pence"] == statement
    assert first["closing_pence"] == statement + first["interest_pence"] - first["payment_pence"]
    future = client.post(
        "/api/plan/mortgage/statements", json={"on": "2027-01-31", "balance_pence": 1}
    )
    assert future.status_code == 422


def test_overpaying_saves_interest_and_warns_past_the_allowance(
    client: TestClient, mortgage: None
) -> None:
    small = client.get("/api/plan/mortgage/overpay", params={"monthly_pence": 20000}).json()
    assert small["interest_saved_pence"] > 0
    assert small["months_saved"] > 0
    assert small["over_allowance"] == []
    big = client.get(
        "/api/plan/mortgage/overpay", params={"lump_pence": 4_000_000, "lump_on": "2026-11-01"}
    ).json()
    assert big["over_allowance"]
    assert big["over_allowance"][0]["overpaid_pence"] == 4_000_000


def test_the_remortgage_what_if_lands_on_a_path_with_the_autumn_2027_cluster(
    client: TestClient, session: Session, mortgage: None, users: dict[str, User]
) -> None:
    made = client.post("/api/plan/mortgage/remortgage", json={"rate_pct": "4.19"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["name"] == "Remortgage at 4.19%"
    version = session.get(BudgetVersion, body["version_id"])
    assert version is not None
    assert version.planned_start == date(2027, 11, 1)

    live = live_version(session)
    run = client.get(
        "/api/plan/forecast",
        params={
            "path": [
                path(client, "Remortgage", (live.id, "2026-11-01"), (version.id, "2027-11-01"))
            ]
        },
    ).json()["runs"][0]
    october, november = month(run, "2027-10-01"), month(run, "2027-11-01")
    car = resolve(session, live, TODAY).find("Car").amount_pence or 0
    # November: the car PCP has ended and the new mortgage payment has started.
    assert november["household_pence"] - october["household_pence"] == car - (
        body["payment_pence"] - 119380
    )
    assert "Car ends" in october["events"]
    assert {"Remortgage at 4.19% from here", "Northfield fix ends (approx.)"} <= set(
        november["events"]
    )
    assert "Remortgage window opens" in month(run, "2027-04-01")["events"]


def test_the_inbox_raises_the_cluster_on_time(session: Session, mortgage: None) -> None:
    assert plan_alerts.check_remortgage(session, date(2027, 3, 31)) == 0
    assert plan_alerts.check_remortgage(session, date(2027, 4, 1)) == 1  # 7 months ahead
    assert plan_alerts.check_remortgage(session, date(2027, 4, 2)) == 0  # once
    assert plan_alerts.check_agreements(session, date(2027, 6, 30)) == 0
    assert plan_alerts.check_agreements(session, date(2027, 7, 1)) == 1  # 3 months before Oct
    items = {i.type: i for i in session.scalars(select(InboxItem))}
    assert items["agreement_ending"].payload_json["end_month"] == "2027-10-01"
    assert alerts._line(items["remortgage_window"]) == (
        "the Northfield fix ends 2027-11: time to look at remortgaging"
    )


# ---- renewals and goals ----------------------------------------------------------------


def test_renewals_remind_30_days_ahead_and_move_on_a_year(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    options = client.get("/api/plan/renewals").json()["candidates"]
    car_insurance = next(c for c in options if c["name"] == "Car Insurance")
    made = client.post(
        "/api/plan/renewals",
        json={
            "name": "Car insurance",
            "provider": "1stCentral",
            "line_id": car_insurance["line_id"],
            "renews_on": (TODAY + timedelta(days=40)).isoformat(),
        },
    ).json()
    assert plan_alerts.check_renewals(session, TODAY) == 0
    assert plan_alerts.check_renewals(session, TODAY + timedelta(days=10)) == 1
    session.commit()
    item = session.scalars(select(InboxItem).where(InboxItem.type == "renewal_due")).one()
    assert client.post(f"/api/inbox/{item.id}/done").status_code == 204
    renewal = client.get("/api/plan/renewals").json()["renewals"][0]
    assert renewal["renews_on"] == (TODAY + timedelta(days=40)).replace(year=2027).isoformat()
    assert client.post(f"/api/inbox/{item.id}/undo").status_code == 204
    assert client.get("/api/plan/renewals").json()["renewals"][0]["renews_on"] == made["renews_on"]


def test_a_goal_proposes_its_monthly_amount_and_nothing_changes_without_a_yes(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    goals = client.get("/api/plan/goals").json()
    xmas = next(
        c
        for c in goals["candidates"]
        if c["label"] == "Xmas Savings" and c["account"] == "person_1_monzo"
    )
    made = client.post(
        "/api/plan/goals",
        json={
            "name": "Xmas",
            "account": "person_1_monzo",
            "line_id": xmas["line_id"],
            "target_pence": 180000,
            "target_date": "2026-12-01",
            "saved_pence": 150000,
            "saved_on": "2026-10-23",
            "repeats_yearly": True,
        },
    )
    assert made.status_code == 201, made.text
    # £300 left for December, one more month to save in (November): £300 a month, not £150.
    assert plan_alerts.check_goals(session, TODAY) == 1
    session.commit()
    item = session.scalars(select(InboxItem).where(InboxItem.type == "goal_adjust")).one()
    assert (item.payload_json["current_pence"], item.payload_json["needed_pence"]) == (15000, 30000)
    live = live_version(session)
    assert resolve(session, live, TODAY).by_id()[xmas["line_id"]].amount_pence == 15000
    assert client.post(f"/api/inbox/{item.id}/take").status_code == 204
    session.expire_all()
    assert resolve(session, live, TODAY).by_id()[xmas["line_id"]].amount_pence == 30000
    run = client.get("/api/plan/forecast").json()["runs"][0]
    goal = run["goals"][0]
    assert goal["name"] == "Xmas"
    assert goal["allocation_pence"] == 30000
    assert goal["on_track"]
    assert client.post(f"/api/inbox/{item.id}/undo").status_code == 204
    session.expire_all()
    assert resolve(session, live, TODAY).by_id()[xmas["line_id"]].amount_pence == 15000


def test_the_plan_screens_answer_and_need_a_login(client: TestClient, app: Any) -> None:
    for url in (
        "/api/plan/paths",
        "/api/plan/forecast",
        "/api/plan/goals",
        "/api/plan/one-offs",
        "/api/plan/take-home",
        "/api/plan/childcare",
        "/api/plan/mortgage",
        "/api/plan/renewals",
        "/api/plan/versions",
    ):
        assert client.get(url).status_code == 200, url
        assert TestClient(app).get(url).status_code == 401, url
    assert client.get("/api/plan/mortgage").json() == {"mortgage": None, "history": []}
    assert add_months(date(2027, 11, 1), -7) == date(2027, 4, 1)


def test_a_remortgage_can_build_on_a_what_if_so_a_path_keeps_its_changes(
    client: TestClient, session: Session, mortgage: None, users: dict[str, User]
) -> None:
    back = what_if(session, users["person_1"], "Return 3 days", 250000)
    made = client.post(
        "/api/plan/mortgage/remortgage", json={"rate_pct": "4.19", "base_version_id": back.id}
    ).json()
    assert made["name"] == "Remortgage at 4.19%, after Return 3 days"
    version = session.get(BudgetVersion, made["version_id"])
    assert version is not None
    assert version.base_version_id == back.id
    incomes = resolve(session, version, date(2027, 11, 1)).incomes
    assert next(i.net_monthly_pence for i in incomes if i.person == "person_2") == 250000


def test_reminders_carry_the_account_of_the_line_they_are_about(
    client: TestClient, session: Session, mortgage: None, users: dict[str, User]
) -> None:
    car = next(
        c
        for c in client.get("/api/plan/renewals").json()["candidates"]
        if c["name"] == "Car Insurance"
    )
    client.post(
        "/api/plan/renewals",
        json={"name": "Car insurance", "line_id": car["line_id"], "renews_on": "2026-11-10"},
    )
    plan_alerts.check_renewals(session, TODAY)
    plan_alerts.check_remortgage(session, date(2027, 4, 1))
    items = {i.type: i.payload_json for i in session.scalars(select(InboxItem))}
    assert items["renewal_due"]["account"] == "joint_billing"
    assert items["remortgage_window"]["account"] == "joint_billing"
