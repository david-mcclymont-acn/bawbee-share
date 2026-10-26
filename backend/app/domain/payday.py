"""Payday checklist (PLAN.md §5.7, §11.3).

Runs are keyed by the payday month: transfers made when the October salaries land fund
November's bills. A run is created the first time someone opens or ticks that payday, and
it snapshots the live budget's transfers so later edits don't move the checklist.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import audit
from app.domain.models import Account, PayDayRule, PaydayRun, PaydayTransfer, Person, User
from app.domain.versions import calculate, live_version, resolve
from app.engine.calendar import PayDay, pay_day
from app.engine.calendar import PayDayRule as Rule


class PaydayError(ValueError):
    pass


def month_key(year: int, month: int) -> date:
    return date(year, month, 1)


def pay_dates(session: Session, month: date) -> dict[str, PayDay]:
    """Each person's official and expected pay date in that month."""
    out: dict[str, PayDay] = {}
    for rule, person in session.execute(
        select(PayDayRule, Person).join(Person, Person.id == PayDayRule.person_id)
    ):
        out[person.key] = pay_day(
            Rule(rule.rule, rule.fixed_day, rule.typical_offset_days),  # type: ignore[arg-type]
            month.year,
            month.month,
        )
    return out


def transfer_date(session: Session, month: date) -> date:
    """Transfers go once both salaries have landed: the later expected pay date."""
    dates = pay_dates(session, month)
    if not dates:
        raise PaydayError("No pay-day rules are set up")
    return max(d.expected for d in dates.values())


@dataclass(frozen=True)
class PlannedTransfer:
    line_id: int | None
    name: str
    from_account: str
    to_account: str
    suggested_pence: int
    actual_pence: int | None
    rule: str = "fixed"

    @property
    def budget_pence(self) -> int:
        """What the budget says to send: its typed-in amount, or else the calculated one."""
        return self.suggested_pence if self.actual_pence is None else self.actual_pence


def planned_transfers(session: Session, month: date) -> list[PlannedTransfer]:
    """What the live budget says to move on that payday (zero amounts left out)."""
    live = live_version(session)
    result = calculate(resolve(session, live, transfer_date(session, month)))
    return [
        PlannedTransfer(
            line_id=int(t.line_key.removeprefix("line:")),
            name=t.name,
            from_account=t.from_account,
            to_account=t.to_account,
            suggested_pence=t.suggested_pence,
            actual_pence=t.actual_pence,
            rule=t.rule,
        )
        for t in result.transfers
        if t.effective_pence != 0
    ]


def get_run(session: Session, month: date) -> PaydayRun | None:
    return session.scalars(select(PaydayRun).where(PaydayRun.month == month)).first()


def start_run(session: Session, month: date, actor: User | None) -> PaydayRun:
    existing = get_run(session, month)
    if existing is not None:
        return existing
    accounts = {a.key: a.id for a in session.scalars(select(Account))}
    run = PaydayRun(
        month=month, version_id=live_version(session).id, created_by=actor.id if actor else None
    )
    for t in planned_transfers(session, month):
        run.transfers.append(
            PaydayTransfer(
                line_id=t.line_id,
                name=t.name,
                from_account_id=accounts[t.from_account],
                to_account_id=accounts[t.to_account],
                suggested_pence=t.suggested_pence,
                actual_pence=t.actual_pence,
            )
        )
    session.add(run)
    session.flush()
    audit.record(session, actor, "payday_run", run.id, "start", None, {"month": month.isoformat()})
    return run


class Unset:
    """Marks an argument that wasn't given (as opposed to an explicit None)."""


UNSET: Final = Unset()


def update_transfer(
    session: Session,
    transfer: PaydayTransfer,
    actor: User | None,
    *,
    done: bool | None = None,
    actual_pence: int | Unset | None = UNSET,
) -> PaydayTransfer:
    """Tick a transfer done (or not), and/or record what was actually sent (None clears)."""
    before = {
        "done": transfer.done_at is not None,
        "actual_pence": transfer.actual_pence,
    }
    if not isinstance(actual_pence, Unset):
        if actual_pence is not None and actual_pence < 0:
            raise PaydayError("An actual amount must be zero or more pence")
        transfer.actual_pence = actual_pence
    if done is True and transfer.done_at is None:
        transfer.done_at = utcnow()
        transfer.done_by = actor.id if actor else None
        transfer.done_via = "manual"
    elif done is False:
        transfer.done_at = None
        transfer.done_by = None
        transfer.done_via = None
        transfer.matched_transaction_id = None
    session.flush()
    audit.record(
        session,
        actor,
        "payday_transfer",
        transfer.id,
        "update",
        before,
        {"done": transfer.done_at is not None, "actual_pence": transfer.actual_pence},
    )
    return transfer
