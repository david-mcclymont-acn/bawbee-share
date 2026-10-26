"""Day-by-day balance of one account across a month (PLAN.md §4.7, §5.5)."""

from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from app.engine.budget import Budget, Result


@dataclass(frozen=True)
class DayBalance:
    day: date
    closing: Fraction
    movements: tuple[tuple[str, Fraction], ...]


@dataclass(frozen=True)
class Projection:
    account: str
    days: tuple[DayBalance, ...]
    minimum: Fraction
    minimum_day: date
    # Lines with no day of month are assumed to leave on the 1st, the worst case.
    undated_lines: tuple[str, ...]

    @property
    def goes_negative(self) -> bool:
        return self.minimum < 0


def project_month(
    budget: Budget,
    result: Result,
    account: str,
    year: int,
    month: int,
    opening_balance: Fraction | int,
    inflow_date: date,
) -> Projection:
    """Run `account` through the month: outgoings leave on their day of month, and
    transfers into it land on `inflow_date` (the payday they're made on)."""
    first = date(year, month, 1)
    last = (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)
    effective = {t.line_key: Fraction(t.effective_pence) for t in result.transfers}

    events: dict[date, list[tuple[str, Fraction]]] = {}
    undated: list[str] = []
    for line in budget.lines:
        value = effective.get(line.key, line.monthly)
        if line.account == account and value:
            if line.day_of_month is None:
                undated.append(line.name)
            day = min(line.day_of_month or 1, last.day)
            events.setdefault(date(year, month, day), []).append((line.name, -value))
        if line.to_account == account and value:
            events.setdefault(inflow_date, []).append((line.name, value))

    balance = Fraction(opening_balance)
    days: list[DayBalance] = []
    minimum, minimum_day = balance, first
    d = first
    while d <= last:
        moves = tuple(events.get(d, ()))
        # Money out first: a same-day bill can't rely on a transfer that lands later that day.
        for _, amount in sorted(moves, key=lambda m: m[1]):
            balance += amount
            if balance < minimum:
                minimum, minimum_day = balance, d
        days.append(DayBalance(d, balance, moves))
        d += timedelta(days=1)
    return Projection(account, tuple(days), minimum, minimum_day, tuple(undated))
