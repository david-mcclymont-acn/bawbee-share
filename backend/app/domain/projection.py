"""The joint account through the month a payday funds (PLAN.md §4.7), from the database.

Shared by the Dashboard's chart (api/versions.py) and the low-balance alert (sync/alerts.py).
It opens with exactly what that payday transfers in, so the lowest point is the month's
real buffer.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.domain.models import BudgetVersion
from app.domain.payday import month_key, transfer_date
from app.domain.versions import calculate, resolve, to_engine
from app.engine.projection import Projection as MonthProjection
from app.engine.projection import project_month


class NoSuchAccount(LookupError):
    pass


@dataclass(frozen=True)
class Projection:
    account: str
    payday_month: date
    bill_month: date
    opening_pence: int
    month: MonthProjection


def month_projection(
    session: Session, version: BudgetVersion, year: int, month: int, account: str | None = None
) -> Projection:
    payday = month_key(year, month)
    bill_month = month_key(year + month // 12, month % 12 + 1)
    resolved = resolve(session, version, bill_month)
    budget, result = to_engine(resolved), calculate(resolved)
    target = account or result.joint_account
    if target not in result.flows:
        raise NoSuchAccount(target)
    opening = sum(t.effective_pence for t in result.transfers if t.to_account == target)
    next_payday = transfer_date(session, bill_month)
    p = project_month(
        budget, result, target, bill_month.year, bill_month.month, opening, next_payday
    )
    return Projection(target, payday, bill_month, opening, p)
