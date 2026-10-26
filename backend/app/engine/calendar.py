"""Scottish bank holidays and pay-day rules (PLAN.md §5.7)."""

from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from typing import Literal

PayDayRuleName = Literal["last_working_day", "last_thursday", "fixed_day"]

# One-off changes announced by proclamation. Add new ones here as they're announced.
_EXTRA_HOLIDAYS = {
    date(2022, 6, 3): "Platinum Jubilee",
    date(2022, 9, 19): "State Funeral of Queen Elizabeth II",
    date(2023, 5, 8): "Coronation of King Charles III",
    # Scotland only, for the World Cup (GOV.UK bank holidays, checked 23 October 2026).
    date(2026, 6, 15): "World Cup bank holiday",
}
_MOVED_HOLIDAYS = {
    date(2020, 5, 4): date(2020, 5, 8),  # Early May moved for VE Day
    date(2022, 5, 30): date(2022, 6, 2),  # Spring moved for the Platinum Jubilee
}


def easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _first_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(weekday - d.weekday()) % 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = _month_end(year, month)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _month_end(year: int, month: int) -> date:
    return (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)


@cache
def scottish_bank_holidays(year: int) -> frozenset[date]:
    taken: set[date] = set()

    def add_with_substitutes(*days: date) -> None:
        # A holiday on a weekend (or on another holiday) moves to the next free weekday.
        for d in days:
            while d.weekday() >= 5 or d in taken:
                d += timedelta(days=1)
            taken.add(d)

    add_with_substitutes(date(year, 1, 1), date(year, 1, 2))
    taken.add(easter_sunday(year) - timedelta(days=2))  # Good Friday
    taken.add(_first_weekday(year, 5, 0))  # Early May
    taken.add(_last_weekday(year, 5, 0))  # Spring
    taken.add(_first_weekday(year, 8, 0))  # Summer (first Monday in August in Scotland)
    add_with_substitutes(date(year, 11, 30))  # St Andrew's Day
    add_with_substitutes(date(year, 12, 25), date(year, 12, 26))

    for original, moved in _MOVED_HOLIDAYS.items():
        if original.year == year:
            taken.discard(original)
            taken.add(moved)
    taken.update(d for d in _EXTRA_HOLIDAYS if d.year == year)
    return frozenset(taken)


def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in scottish_bank_holidays(d.year)


def previous_working_day(d: date) -> date:
    """`d` itself if it's a working day, otherwise the working day before it."""
    while not is_working_day(d):
        d -= timedelta(days=1)
    return d


def shift_working_days(d: date, days: int) -> date:
    """Move by whole working days (negative is earlier)."""
    step = 1 if days > 0 else -1
    for _ in range(abs(days)):
        d += timedelta(days=step)
        while not is_working_day(d):
            d += timedelta(days=step)
    return d


@dataclass(frozen=True)
class PayDayRule:
    rule: PayDayRuleName
    fixed_day: int | None = None
    # Working days between the official date and when the money usually lands (Person 2: -1).
    typical_offset_days: int = 0


@dataclass(frozen=True)
class PayDay:
    official: date
    expected: date


def pay_day(rule: PayDayRule, year: int, month: int) -> PayDay:
    if rule.rule == "last_working_day":
        official = previous_working_day(_month_end(year, month))
    elif rule.rule == "last_thursday":
        official = previous_working_day(_last_weekday(year, month, 3))
    else:
        if rule.fixed_day is None:
            raise ValueError("fixed_day rule needs a fixed_day")
        day = min(rule.fixed_day, _month_end(year, month).day)
        official = previous_working_day(date(year, month, day))
    return PayDay(official, shift_working_days(official, rule.typical_offset_days))
