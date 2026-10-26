"""Checks that make the spreadsheet's quiet problems visible (PLAN.md §4)."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import IncomeSource
from app.domain.versions import ResolvedBudget

STALE_AFTER_MONTHS = 12


@dataclass(frozen=True)
class StaleInput:
    entity: str
    entity_id: int
    label: str
    as_of: date


def _months_between(earlier: date, later: date) -> int:
    return (
        (later.year - earlier.year) * 12 + later.month - earlier.month - (later.day < earlier.day)
    )


def stale_inputs(session: Session, today: date) -> list[StaleInput]:
    """Key inputs whose "as of" date is more than 12 months old (§4.4)."""
    out: list[StaleInput] = []
    for source in session.scalars(select(IncomeSource).order_by(IncomeSource.id)):
        if _months_between(source.as_of_date, today) > STALE_AFTER_MONTHS:
            out.append(
                StaleInput(
                    "income_source",
                    source.id,
                    f"{source.person.name}'s salary ({source.employer or 'employer unknown'})",
                    source.as_of_date,
                )
            )
    return out


@dataclass(frozen=True)
class DuplicateLine:
    name: str
    accounts: tuple[str, ...]


def duplicate_lines(resolved: ResolvedBudget) -> list[DuplicateLine]:
    """Bills with the same name on more than one account, like Apple Fitness+ (§4.5)."""
    by_name: dict[str, set[str]] = {}
    for ln in resolved.lines:
        if ln.kind == "bill":
            by_name.setdefault(ln.name.casefold(), set()).add(ln.account)
    names = {ln.name.casefold(): ln.name for ln in resolved.lines}
    return [
        DuplicateLine(names[key], tuple(sorted(accounts)))
        for key, accounts in sorted(by_name.items())
        if len(accounts) > 1
    ]
