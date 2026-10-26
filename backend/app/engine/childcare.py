"""The childcare calculator (PLAN.md §9), ported from reference/childcare-app.

Pure, and line for line the old calculator's arithmetic, so the same inputs give the
same answers (tests/test_childcare_parity.py runs both). Two rules it keeps on purpose:

- Pay follows **hours** and nursery cost follows **days**. A 5-day week of 36 hours pays
  the same as a 4-day week of 36 hours; only the nursery bill differs.
- Everything here is annual. A monthly figure is annual ÷ 12, done once, at the end
  (an older version divided an already-monthly figure by 12 again).

New since the old app: funded hours (Scottish early learning and childcare), which take
the funded hours' worth off a child's bill before Tax-Free Childcare is worked out. With
no funded hours the result is exactly the old calculator's.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from app.engine import tax

Basis = Literal["day", "hour"]
WEEKS = 52


@dataclass(frozen=True)
class Rate:
    rate: Fraction  # £ a day or an hour (whole pounds and pence, as entered)
    discount_pct: Fraction = Fraction(0)
    min_days: Fraction = Fraction(0)  # the discount applies from this many days a week


@dataclass(frozen=True)
class Nursery:
    name: str
    basis: Basis
    rates: tuple[Rate, ...]  # one per child, in order
    takes_tfc: bool = True
    bills_leave: bool = False


@dataclass(frozen=True)
class Pattern:
    days: Fraction
    hours: Fraction


@dataclass(frozen=True)
class Profile:
    """The earner and the household inputs the old calculator held as form fields."""

    salary: Fraction  # whole-time salary for the reference hours
    ref_hours: Fraction
    ref_days: Fraction
    bank_holidays: Fraction  # a year, for a 5-day week
    leave_days: Fraction  # a year, for the reference pattern
    partner_income: Fraction  # for the High Income Child Benefit Charge
    children: tuple[str, ...]
    tax_code: str | None = None
    net_pay_arrangement: bool = True
    use_child_benefit: bool = True
    child_benefit_first_weekly: Fraction | None = None  # None: the tax year's rate
    child_benefit_other_weekly: Fraction | None = None
    tfc_cap: Fraction | None = None  # a year per child; None: the tax year's
    tfc_rate: Fraction | None = None  # percent; None: the tax year's
    ni_primary_threshold: Fraction | None = None  # None: the tax year's
    ni_upper_limit: Fraction | None = None


def earnings(profile: Profile, hours: Fraction, year: tax.TaxYear) -> tax.TakeHome:
    gross = profile.salary * hours / (profile.ref_hours or 1)
    return tax.take_home(
        gross,
        year,
        tax_code=profile.tax_code,
        pension="nhs_scotland",
        net_pay_arrangement=profile.net_pay_arrangement,
        ni_primary_threshold=profile.ni_primary_threshold,
        ni_upper_limit=profile.ni_upper_limit,
    )


def leave_days_for(profile: Profile, days: Fraction) -> Fraction:
    """Leave is entered for the reference pattern; the nursery days it covers scale by days."""
    return profile.leave_days * days / (profile.ref_days or 1)


@dataclass(frozen=True)
class DayCounts:
    booked: Fraction
    bank: Fraction
    leave: Fraction
    attended: Fraction
    open_booked: Fraction  # booked days the nursery is open (bank holidays off)


def day_counts(profile: Profile, days: Fraction) -> DayCounts:
    booked = days * WEEKS
    bank = profile.bank_holidays * days / 5
    leave = leave_days_for(profile, days)
    return DayCounts(booked, bank, leave, booked - bank - leave, booked - bank)


@dataclass(frozen=True)
class ChildBenefit:
    gross: Fraction
    net: Fraction
    clawback_pct: Fraction


def child_benefit(profile: Profile, year: tax.TaxYear) -> ChildBenefit:
    if not profile.use_child_benefit or not profile.children:
        return ChildBenefit(Fraction(0), Fraction(0), Fraction(0))
    first = profile.child_benefit_first_weekly
    other = profile.child_benefit_other_weekly
    first = year.child_benefit_first_weekly if first is None else first
    other = year.child_benefit_other_weekly if other is None else other
    gross = (first + other * (len(profile.children) - 1)) * WEEKS
    pct = min(
        Fraction(100),
        max(Fraction(0), (profile.partner_income - year.hicbc_threshold) / year.hicbc_step),
    )
    return ChildBenefit(gross, gross * (1 - pct / 100), pct)


@dataclass(frozen=True)
class ChildLine:
    name: str
    rate: Fraction
    effective: Fraction  # after any discount
    per_day: Fraction
    discount_applied: bool
    discount_pct: Fraction
    funded: Fraction  # funded hours' worth taken off (a year)
    cost: Fraction  # the bill, after funded hours (a year)
    tfc: Fraction  # the government's top-up (a year)


@dataclass(frozen=True)
class NurseryCost:
    days: Fraction  # days billed
    counts: DayCounts
    bills_leave: bool
    hours_per_day: Fraction
    takes_tfc: bool
    lines: tuple[ChildLine, ...]
    cost: Fraction
    tfc: Fraction
    net: Fraction
    leave_waste: Fraction  # paid for on leave days, not used

    @property
    def funded(self) -> Fraction:
        return sum((line.funded for line in self.lines), Fraction(0))


def nursery_cost(
    profile: Profile,
    nursery: Nursery,
    pattern: Pattern,
    year: tax.TaxYear,
    funded_hours: tuple[Fraction, ...] = (),
) -> NurseryCost:
    counts = day_counts(profile, pattern.days)
    cap = year.tfc_cap_per_child if profile.tfc_cap is None else profile.tfc_cap
    rate = (year.tfc_rate if profile.tfc_rate is None else profile.tfc_rate) / 100
    billed = counts.open_booked if nursery.bills_leave else counts.attended
    hours_per_day = pattern.hours / pattern.days if pattern.days else Fraction(0)
    lines: list[ChildLine] = []
    for i, r in enumerate(nursery.rates):
        applied = r.discount_pct > 0 and pattern.days >= r.min_days
        effective = r.rate * (1 - r.discount_pct / 100 if applied else 1)
        per_day = effective * hours_per_day if nursery.basis == "hour" else effective
        full = billed * per_day
        hours = funded_hours[i] if i < len(funded_hours) else Fraction(0)
        per_hour = (
            effective
            if nursery.basis == "hour"
            else (per_day / hours_per_day if hours_per_day else Fraction(0))
        )
        funded = min(full, hours * per_hour)
        cost = full - funded
        tfc = min(cost * rate, cap) if nursery.takes_tfc else Fraction(0)
        name = profile.children[i] if i < len(profile.children) else f"Child {i + 1}"
        lines.append(
            ChildLine(name, r.rate, effective, per_day, applied, r.discount_pct, funded, cost, tfc)
        )
    cost = sum((line.cost for line in lines), Fraction(0))
    top_up = sum((line.tfc for line in lines), Fraction(0))
    waste = (
        sum((line.per_day * counts.leave for line in lines), Fraction(0))
        if nursery.bills_leave
        else Fraction(0)
    )
    return NurseryCost(
        days=billed,
        counts=counts,
        bills_leave=nursery.bills_leave,
        hours_per_day=hours_per_day,
        takes_tfc=nursery.takes_tfc,
        lines=tuple(lines),
        cost=cost,
        tfc=top_up,
        net=cost - top_up,
        leave_waste=waste,
    )


@dataclass(frozen=True)
class Scenario:
    nursery: Nursery
    pattern: Pattern
    earnings: tax.TakeHome
    childcare: NurseryCost
    child_benefit: ChildBenefit
    annual: Fraction  # left over a year: take-home + Child Benefit - childcare after TFC

    @property
    def monthly(self) -> Fraction:
        return self.annual / 12

    @property
    def share_of_take_home(self) -> Fraction:
        return self.childcare.net / self.earnings.net if self.earnings.net > 0 else Fraction(0)


def scenario(
    profile: Profile,
    nursery: Nursery,
    pattern: Pattern,
    year: tax.TaxYear,
    funded_hours: tuple[Fraction, ...] = (),
) -> Scenario:
    pay = earnings(profile, pattern.hours, year)
    care = nursery_cost(profile, nursery, pattern, year, funded_hours)
    benefit = child_benefit(profile, year)
    return Scenario(nursery, pattern, pay, care, benefit, pay.net + benefit.net - care.net)


def matrix(
    profile: Profile,
    nurseries: tuple[Nursery, ...],
    patterns: tuple[Pattern, ...],
    year: tax.TaxYear,
    funded_hours: tuple[Fraction, ...] = (),
) -> list[list[Scenario]]:
    """One row per nursery, one cell per work pattern (the old calculator's matrix)."""
    return [[scenario(profile, n, p, year, funded_hours) for p in patterns] for n in nurseries]
