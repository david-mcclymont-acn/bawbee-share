"""Planning reminders for the inbox (Phases 6 and 7).

Each morning and after each sync, alongside the bank checks (alerts.py):

- **goal_adjust:** a savings goal needs a different monthly amount from its budget line
  (what's left ÷ months left). Approving moves the live line to that amount.
- **renewal_due:** a policy renews within its reminder window (30 days by default).
- **remortgage_window:** 7 months before the mortgage fix ends (decision 30), with a
  button to make a remortgage what-if.
- **agreement_ending:** 3 months before an agreement such as the car PCP ends (§5.5).
- **salary_mismatch:** the salary that landed is more than £5 from the take-home the
  budget uses.

Nothing here changes the budget; each one waits in the inbox for a yes.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import forecast, takehome
from app.domain import mortgage as mortgage_domain
from app.domain.models import InboxItem, Renewal, SavingsGoal
from app.domain.versions import live_version, resolve
from app.engine.forecast import needed_monthly
from app.engine.money import round_pence
from app.engine.mortgage import add_months, months_between

GOAL_TOLERANCE_PENCE = 100  # under £1 a month apart isn't worth asking about
AGREEMENT_LEAD_MONTHS = 3
SALARY_LOOKBACK_DAYS = 45


def _exists(session: Session, key: str) -> bool:
    return (
        session.scalars(select(InboxItem.id).where(InboxItem.dedupe_key == key)).first() is not None
    )


def _raise(
    session: Session, key: str, kind: str, payload: dict[str, Any], line_id: int | None = None
) -> bool:
    if _exists(session, key):
        return False
    session.add(InboxItem(type=kind, dedupe_key=key, line_id=line_id, payload_json=payload))
    session.flush()
    return True


def goal_needs(session: Session, goal: SavingsGoal, today: date) -> tuple[int, int] | None:
    """(what the live line puts aside a month, what the goal needs a month), or None."""
    if goal.line_id is None:
        return None
    live = resolve(session, live_version(session), today)
    line = live.by_id().get(goal.line_id)
    if line is None:
        return None
    allocation = round_pence(line.monthly())
    start = forecast.start_month(today)
    gap = max(0, months_between(forecast.first_of_month(goal.saved_on), start) - 1)
    saved = goal.saved_pence + allocation * gap
    target = forecast.first_of_month(goal.target_date)
    while goal.repeats_yearly and target < start:
        saved = max(0, saved - goal.target_pence)
        target = add_months(target, 12)
    return allocation, needed_monthly(goal.target_pence, saved, start, target)


def check_goals(session: Session, today: date) -> int:
    raised = 0
    for goal in forecast.goals(session):
        found = goal_needs(session, goal, today)
        if found is None:
            continue
        allocation, needed = found
        if abs(needed - allocation) < GOAL_TOLERANCE_PENCE:
            continue
        key = f"goal_adjust:{goal.id}:{needed}"
        if _exists(session, key):
            continue
        # A newer figure replaces any older one still waiting.
        for old in session.scalars(
            select(InboxItem).where(
                InboxItem.type == "goal_adjust",
                InboxItem.status.in_(("open", "snoozed")),
                InboxItem.dedupe_key.like(f"goal_adjust:{goal.id}:%"),
            )
        ):
            old.status = "rejected"
            old.payload_json = old.payload_json | {"superseded": True}
        line = goal.line
        raised += _raise(
            session,
            key,
            "goal_adjust",
            {
                "goal_id": goal.id,
                "name": goal.name,
                "label": f"{line.name} ({line.provider})"
                if line and line.provider
                else (line.name if line else goal.name),
                "current_pence": allocation,
                "needed_pence": needed,
                "target_pence": goal.target_pence,
                "target_date": goal.target_date.isoformat(),
                "account": goal.account.key,
            },
            line_id=goal.line_id,
        )
    return raised


def check_renewals(session: Session, today: date) -> int:
    raised = 0
    for renewal in session.scalars(select(Renewal).order_by(Renewal.renews_on)):
        days = (renewal.renews_on - today).days
        if not 0 <= days <= renewal.remind_days:
            continue
        raised += _raise(
            session,
            f"renewal_due:{renewal.id}:{renewal.renews_on.isoformat()}",
            "renewal_due",
            {
                "renewal_id": renewal.id,
                "name": renewal.name,
                "provider": renewal.provider,
                "renews_on": renewal.renews_on.isoformat(),
                "cost_pence": renewal.cost_pence,
                "account": renewal.line.account.key if renewal.line else None,
            },
            line_id=renewal.line_id,
        )
    return raised


def _line_account(session: Session, line_id: int | None) -> str | None:
    from app.domain.models import BudgetLine

    line = session.get(BudgetLine, line_id) if line_id else None
    return line.account.key if line else None


def check_remortgage(session: Session, today: date) -> int:
    m = mortgage_domain.current(session)
    if m is None or m.fix_end_on is None:
        return 0
    window = mortgage_domain.remortgage_window(m, forecast.REMORTGAGE_LEAD_MONTHS)
    if window is None or today < window or today >= m.fix_end_on:
        return 0
    try:
        owed, anchored = mortgage_domain.balance(
            m, add_months(forecast.first_of_month(m.fix_end_on), -1)
        )
    except mortgage_domain.MortgageError:
        owed, anchored = 0, False
    return int(
        _raise(
            session,
            f"remortgage_window:{m.id}:{m.fix_end_on.isoformat()}",
            "remortgage_window",
            {
                "mortgage_id": m.id,
                "lender": m.lender,
                "fix_end": m.fix_end_on.isoformat(),
                "fix_end_exact": m.fix_end_exact,
                "balance_at_fix_end_pence": owed,
                "balance_anchored": anchored,
                "payment_pence": m.payment_pence,
                "account": _line_account(session, m.line_id),
            },
            line_id=m.line_id,
        )
    )


def check_agreements(session: Session, today: date) -> int:
    raised = 0
    live = resolve(session, live_version(session), today)
    this_month = forecast.first_of_month(today)
    for line in live.lines:
        end = forecast.agreement_end(line)
        if end is None or end < this_month:
            continue
        if this_month < add_months(end, -AGREEMENT_LEAD_MONTHS):
            continue
        agreement = line.agreement or {}
        raised += _raise(
            session,
            f"agreement_ending:{line.line_id}:{end.isoformat()}",
            "agreement_ending",
            {
                "label": line.label,
                "type": agreement.get("type"),
                "end_month": end.isoformat(),
                "end_confirmed": bool(agreement.get("end_date_confirmed")),
                "amount_pence": line.amount_pence,
                # The optional final (balloon) payment, if it's been entered.
                "final_payment_pence": agreement.get("balloon_payment_pence"),
                "account": line.account,
            },
            line_id=line.line_id,
        )
    return raised


def check_salaries(session: Session, today: date) -> int:
    raised = 0
    for check in takehome.checks(session, today):
        for salary in check.mismatches():
            if (today - salary.on).days > SALARY_LOOKBACK_DAYS:
                continue
            raised += _raise(
                session,
                f"salary_mismatch:{check.person}:{salary.on.isoformat()}",
                "salary_mismatch",
                {
                    "person": check.person,
                    "name": check.name,
                    "on": salary.on.isoformat(),
                    "landed_pence": salary.pence,
                    "expected_pence": check.expected_pence,
                    "budget_is_override": check.budget_is_override,
                    "calculated_pence": check.calculated_monthly_pence,
                    "description": salary.description,
                },
            )
    return raised


@dataclass
class PlanAlerts:
    goals: int = 0
    renewals: int = 0
    remortgage: int = 0
    agreements: int = 0
    salaries: int = 0


def run(session: Session, today: date) -> PlanAlerts:
    return PlanAlerts(
        goals=check_goals(session, today),
        renewals=check_renewals(session, today),
        remortgage=check_remortgage(session, today),
        agreements=check_agreements(session, today),
        salaries=check_salaries(session, today),
    )
