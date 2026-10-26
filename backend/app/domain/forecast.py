"""Forecast paths and the 36-month forecast (PLAN.md §5.5, §5.6).

A path is a dated chain of budget versions: the live budget now, then "Return 3 days"
from April, then "Remortgage" from November, and so on. For each month this resolves the
version that applies (as it stands in that month, so dated live changes count), stops any
agreement that has ended (the car PCP), runs the budget engine, and hands the months to
engine/forecast.py with the opening balances, one-offs and savings goals.

The forecast starts next month: this month is the day-by-day projection on the Dashboard.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import childcare as childcare_domain
from app.domain import taxconfig
from app.domain.models import (
    Account,
    BankAccountLink,
    BudgetVersion,
    ForecastPath,
    Mortgage,
    OneOff,
    SavingsGoal,
)
from app.domain.versions import ResolvedBudget, ResolvedLine, calculate, live_version, resolve
from app.engine import forecast as engine
from app.engine.money import round_pence
from app.engine.mortgage import add_months, months_between

HORIZON = 36
REMORTGAGE_LEAD_MONTHS = 7  # decision 30: the window opens 7 months before the fix ends


def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def start_month(today: date) -> date:
    return add_months(first_of_month(today), 1)


@dataclass(frozen=True)
class Step:
    from_month: date
    version: BudgetVersion


@dataclass(frozen=True)
class Plan:
    id: int | None  # None: the live budget on its own
    name: str
    steps: tuple[Step, ...]

    def version_for(self, month: date) -> BudgetVersion:
        chosen = self.steps[0].version
        for step in self.steps:
            if step.from_month <= month:
                chosen = step.version
        return chosen


def plan_for(path: ForecastPath) -> Plan:
    steps = tuple(
        Step(s.from_month, s.version) for s in sorted(path.steps, key=lambda s: s.from_month)
    )
    if not steps:
        raise ValueError(f"The path {path.name!r} has no versions on it.")
    return Plan(path.id, path.name, steps)


def live_plan(session: Session) -> Plan:
    live = live_version(session)
    return Plan(None, f"{live.name} as it is", (Step(date.min, live),))


def default_plan(session: Session) -> Plan:
    path = session.scalars(
        select(ForecastPath).where(ForecastPath.is_default.is_(True)).order_by(ForecastPath.id)
    ).first()
    return plan_for(path) if path is not None and path.steps else live_plan(session)


def agreement_end(line: ResolvedLine) -> date | None:
    """The month an agreement's last payment falls in (the car PCP's end date)."""
    raw = (line.agreement or {}).get("end_date")
    if not raw:
        return None
    try:
        return first_of_month(date.fromisoformat(raw if len(raw) > 7 else f"{raw}-01"))
    except ValueError:
        return None


@dataclass(frozen=True)
class Opening:
    pence: int
    source: str  # "bank" | "entered" | "none"
    as_of: str | None


def opening_balances(session: Session) -> dict[str, Opening]:
    out: dict[str, Opening] = {}
    linked = {
        link.account_id: link
        for link in session.scalars(
            select(BankAccountLink).where(
                BankAccountLink.account_id.is_not(None), BankAccountLink.balance_pence.is_not(None)
            )
        )
    }
    for account in session.scalars(select(Account).where(Account.role != "dormant")):
        link = linked.get(account.id)
        if link is not None and link.balance_pence is not None:
            out[account.key] = Opening(
                link.balance_pence, "bank", link.balance_at.isoformat() if link.balance_at else None
            )
        elif account.opening_balance_pence is not None:
            out[account.key] = Opening(account.opening_balance_pence, "entered", None)
        else:
            out[account.key] = Opening(0, "none", None)
    return out


def mortgage_events(session: Session) -> dict[date, list[str]]:
    events: dict[date, list[str]] = {}
    current = session.scalars(select(Mortgage).where(Mortgage.status == "current")).first()
    if current is None or current.fix_end_on is None:
        return events
    fix_end = first_of_month(current.fix_end_on)
    approx = "" if current.fix_end_exact else " (approx.)"
    events.setdefault(fix_end, []).append(f"{current.lender} fix ends{approx}")
    window = add_months(fix_end, -REMORTGAGE_LEAD_MONTHS)
    events.setdefault(window, []).append("Remortgage window opens")
    return events


def childcare_events(session: Session, today: date) -> dict[date, list[str]]:
    events: dict[date, list[str]] = {}
    try:
        label = taxconfig.year_for(today).label
    except taxconfig.TaxConfigMissing:
        return events
    for child in childcare_domain.children(session):
        start = childcare_domain.funded_from(child, label)
        if start is not None:
            events.setdefault(start, []).append(f"{child.name} starts funded hours")
    return events


def _change(resolved: ResolvedBudget, result: Any) -> dict[str, Any]:
    roles = {a.key: (a.role, a.owner) for a in resolved.accounts}
    change = {}
    for key, flow in result.flows.items():
        role, owner = roles.get(key, ("", None))
        if role == "salary_hub" and owner in result.personal_surplus:
            change[key] = result.personal_surplus[owner]
        elif key == result.joint_account:
            change[key] = result.joint_surplus
        else:
            change[key] = flow.inflow - flow.outflow
    return change


def goals(session: Session) -> list[SavingsGoal]:
    return list(
        session.scalars(
            select(SavingsGoal).where(SavingsGoal.status == "active").order_by(SavingsGoal.id)
        )
    )


def _goal_key(goal: SavingsGoal) -> str:
    return f"goal:{goal.id}"


@dataclass(frozen=True)
class Run:
    plan: Plan
    forecast: engine.Forecast
    opening: dict[str, Opening]
    accounts: dict[str, str]  # key -> name
    goal_ids: dict[str, int]


def run(session: Session, plan: Plan, today: date, months: int = HORIZON) -> Run:
    start = start_month(today)
    accounts = {
        a.key: a.name for a in session.scalars(select(Account).where(Account.role != "dormant"))
    }
    opening = opening_balances(session)
    one_offs = [
        o
        for o in session.scalars(select(OneOff).order_by(OneOff.on))
        if o.path_id is None or o.path_id == plan.id
    ]
    goal_rows = goals(session)
    extra_events: dict[date, list[str]] = {}
    for source in (mortgage_events(session), childcare_events(session, today)):
        for month, labels in source.items():
            extra_events.setdefault(month, []).extend(labels)

    out: list[engine.Month] = []
    previous: int | None = None
    for i in range(months):
        month = add_months(start, i)
        version = plan.version_for(month)
        resolved = resolve(session, version, month)
        ended = [ln for ln in resolved.lines if (end := agreement_end(ln)) and end < month]
        ending = [ln for ln in resolved.lines if agreement_end(ln) == month]
        if ended:
            resolved = dataclasses.replace(
                resolved, lines=[ln for ln in resolved.lines if ln not in ended]
            )
        result = calculate(resolved)
        events = (
            [f"{version.name} from here"] if previous is not None and previous != version.id else []
        )
        events += [f"{ln.label} ends" for ln in ending]
        events += extra_events.get(month, [])
        previous = version.id
        allocations = {}
        by_id = resolved.by_id()
        for goal in goal_rows:
            line = by_id.get(goal.line_id) if goal.line_id else None
            if line is not None:
                allocations[_goal_key(goal)] = round_pence(line.monthly())
        out.append(
            engine.Month(
                month=month,
                version_id=version.id,
                version_name=version.name,
                change=_change(resolved, result),
                personal_surplus=dict(result.personal_surplus),
                joint_surplus=result.joint_surplus,
                household_surplus=result.household_surplus,
                one_offs=tuple(
                    engine.OneOff(o.account.key, o.amount_pence, o.label)
                    for o in one_offs
                    if first_of_month(o.on) == month and o.account.key in accounts
                ),
                events=tuple(events),
                allocations=allocations,
            )
        )

    engine_goals = []
    for goal in goal_rows:
        saved = goal.saved_pence
        # Allocations put aside since the saved figure was entered, up to the forecast.
        first_alloc = out[0].allocations.get(_goal_key(goal), 0) if out else 0
        gap = max(0, months_between(first_of_month(goal.saved_on), start) - 1)
        saved += first_alloc * gap
        engine_goals.append(
            engine.Goal(
                key=_goal_key(goal),
                name=goal.name,
                account=goal.account.key,
                target=goal.target_pence,
                target_month=first_of_month(goal.target_date),
                saved=saved,
                repeats_yearly=goal.repeats_yearly,
            )
        )
    projected = engine.run(
        out,
        {k: v.pence for k, v in opening.items()},
        tuple(engine_goals),
        names=accounts,
    )
    return Run(plan, projected, opening, accounts, {_goal_key(g): g.id for g in goal_rows})
