"""Actual vs planned for a month (PLAN.md §8.4 step 5).

Bills compare against the payments matched to them. Allocations (Groceries, Petrol, ...)
compare against what was spent in that category from that account and not matched to a bill.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Account, BankTransaction, BudgetVersion, Category
from app.domain.versions import resolve
from app.engine.money import round_pence

Status = Literal["paid", "partly", "not_seen", "due", "over", "under", "no_data"]

# Exports rarely start or end exactly on a payment day; a little slack either side.
SLACK = timedelta(days=3)


@dataclass
class LineActual:
    line_id: int
    label: str
    account: str
    kind: str
    planned_pence: int
    actual_pence: int
    status: Status
    paid_on: list[date] = field(default_factory=list)
    due_on: date | None = None


def _unpaid(
    day: int | None, first: date, after: date, covered: tuple[date, date]
) -> tuple[Status, date | None]:
    """A bill with no payment yet: missing, still to come, or outside what was exported."""
    start, end = covered
    if day is None:
        # No set day: it can only be missing once the export spans the whole month.
        whole = start <= first + SLACK and end >= after - timedelta(days=1) - SLACK
        return ("not_seen" if whole else "no_data"), None
    last_day = (after - timedelta(days=1)).day
    due = first.replace(day=min(day, last_day))
    if due > end:
        return "due", due
    if due < start - SLACK:
        return "no_data", due
    return "not_seen", due


def _spans(txns: Sequence[BankTransaction]) -> dict[int, tuple[date, date]]:
    """The span each account's exports cover, from the rows themselves."""
    covered: dict[int, tuple[date, date]] = {}
    for t in txns:
        lo, hi = covered.get(t.account_id, (t.booked_date, t.booked_date))
        covered[t.account_id] = (min(lo, t.booked_date), max(hi, t.booked_date))
    return covered


def _month(session: Session, year: int, month: int) -> Sequence[BankTransaction]:
    first = date(year, month, 1)
    after = date(year + month // 12, month % 12 + 1, 1)
    return session.scalars(
        select(BankTransaction).where(
            BankTransaction.booked_date >= first, BankTransaction.booked_date < after
        )
    ).all()


def export_coverage(session: Session, year: int, month: int) -> dict[str, tuple[date, date]]:
    """Account key -> the first and last day its bank exports cover in that month."""
    keys = {a.id: a.key for a in session.scalars(select(Account))}
    return {keys[a]: span for a, span in _spans(_month(session, year, month)).items()}


def month_actuals(
    session: Session, version: BudgetVersion, year: int, month: int
) -> list[LineActual]:
    first = date(year, month, 1)
    after = date(year + month // 12, month % 12 + 1, 1)
    resolved = resolve(session, version, first)
    account_ids = {a.key: a.id for a in session.scalars(select(Account))}
    category_ids = {c.name: c.id for c in session.scalars(select(Category))}
    txns = _month(session, year, month)
    covered = _spans(txns)

    out: list[LineActual] = []
    for ln in resolved.lines:
        if ln.kind == "transfer":
            continue  # the payday checklist tracks transfers, ticked from the bank
        planned = round_pence(ln.monthly())
        if planned == 0:
            continue
        account_id = account_ids.get(ln.account)
        if account_id is None or account_id not in covered:
            out.append(LineActual(ln.line_id, ln.label, ln.account, ln.kind, planned, 0, "no_data"))
            continue
        if ln.kind == "allocation":
            spent = [
                t
                for t in txns
                if t.account_id == account_id
                and t.amount_pence < 0
                and t.matched_line_id is None
                and ln.category is not None
                and t.category_id == category_ids.get(ln.category)
            ]
            actual = -sum(t.amount_pence for t in spent)
            # Nothing in its category yet says nothing: spending may not be categorised.
            status: Status = "no_data" if not spent else "over" if actual > planned else "under"
            out.append(
                LineActual(ln.line_id, ln.label, ln.account, ln.kind, planned, actual, status)
            )
            continue
        paid = [t for t in txns if t.matched_line_id == ln.line_id and t.amount_pence < 0]
        actual = -sum(t.amount_pence for t in paid)
        due_on = None
        if not paid and ln.frequency != "monthly":
            status = "no_data"  # a yearly or quarterly bill isn't due every month
        elif not paid:
            status, due_on = _unpaid(ln.day_of_month, first, after, covered[account_id])
        elif actual < planned:
            status = "partly"
        elif actual > planned:
            status = "over"  # paid more than the budget says: a price change, or a one-off
        else:
            status = "paid"
        out.append(
            LineActual(
                ln.line_id,
                ln.label,
                ln.account,
                ln.kind,
                planned,
                actual,
                status,
                sorted({t.booked_date for t in paid}),
                due_on,
            )
        )
    return out
