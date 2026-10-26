"""The mortgage (PLAN.md §10): amortisation, statement anchoring, LTV and overpayments.

Pure. Money is integer pence. Interest is charged monthly at the annual rate ÷ 12 and
rounded to the penny each month, as a lender's statement does; a statement balance
re-anchors the schedule, so any small difference from the lender's daily method never
builds up past the latest statement.
"""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from app.engine.money import round_pence

LTV_BANDS = (60, 75, 80, 85, 90)  # percent: lenders price by these


def add_months(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def months_between(start: date, end: date) -> int:
    """Whole months from start's month to end's month."""
    return (end.year - start.year) * 12 + end.month - start.month


def payment(principal: int, rate_pct: Fraction, months: int) -> int:
    """The level monthly repayment that clears principal over months, to the penny (rounded up)."""
    if months <= 0:
        return principal
    r = rate_pct / 100 / 12
    exact = Fraction(principal, months) if r == 0 else principal * r / (1 - (1 + r) ** -months)
    whole = int(exact)
    return whole if exact == whole else whole + 1


@dataclass(frozen=True)
class Row:
    on: date  # the payment date's month (1st)
    opening: int
    interest: int
    payment: int
    overpayment: int
    closing: int


def schedule(
    balance: int,
    rate_pct: Fraction,
    monthly_payment: int,
    first: date,
    months: int,
    *,
    monthly_overpayment: int = 0,
    lumps: dict[date, int] | None = None,
) -> list[Row]:
    """Month by month from `first` (the next payment) until paid off or `months` run out."""
    rows: list[Row] = []
    r = rate_pct / 100 / 12
    for i in range(months):
        if balance <= 0:
            break
        on = add_months(first, i)
        interest = round_pence(balance * r)
        pay = min(monthly_payment, balance + interest)
        extra = min(monthly_overpayment + (lumps or {}).get(on, 0), balance + interest - pay)
        closing = balance + interest - pay - extra
        rows.append(Row(on, balance, interest, pay, extra, closing))
        balance = closing
    return rows


@dataclass(frozen=True)
class Mortgage:
    principal: int
    rate_pct: Fraction
    term_months: int
    first_payment: date  # month of the first payment
    monthly_payment: int
    fix_end: date


def balance_on(m: Mortgage, on: date, anchor: tuple[date, int] | None = None) -> tuple[int, bool]:
    """The balance after the payment in `on`'s month. True when anchored to a statement.

    A statement is taken as the balance after its month's payment (payments are on the
    1st), so a statement dated 30 September anchors September itself.
    """
    if anchor is not None and date(anchor[0].year, anchor[0].month, 1) <= on:
        start, opening = add_months(date(anchor[0].year, anchor[0].month, 1), 1), anchor[1]
        anchored = True
    else:
        start, opening = date(m.first_payment.year, m.first_payment.month, 1), m.principal
        anchored = False
    months = months_between(start, on) + 1
    if months <= 0:
        return opening, anchored
    rows = schedule(opening, m.rate_pct, m.monthly_payment, start, months)
    return (rows[-1].closing if rows else 0), anchored


@dataclass(frozen=True)
class Ltv:
    pct: Fraction  # balance ÷ value x 100
    band: int | None  # the lowest band it is within (e.g. 80 for 76.4%); None above 90
    next_band: int | None  # the next band down
    to_next_band: int  # pence to repay to reach it (0 if none)


def ltv(balance: int, value: int) -> Ltv:
    pct = Fraction(balance * 100, value) if value else Fraction(0)
    band = next((b for b in LTV_BANDS if pct <= b), None)
    lower = [b for b in LTV_BANDS if b < pct]
    nxt = lower[-1] if lower else None
    needed = balance - (value * nxt // 100) if nxt is not None else 0
    return Ltv(pct, band, nxt, max(0, needed))


@dataclass(frozen=True)
class OverpaymentPlan:
    months_saved: int
    interest_saved: int
    interest_without: int
    interest_with: int
    paid_off_without: date | None
    paid_off_with: date | None
    # Fix years where the plan's overpayments pass the penalty-free allowance.
    over_allowance: tuple[tuple[date, int, int], ...]  # (year start, overpaid, allowance)


def overpayment_plan(
    balance: int,
    rate_pct: Fraction,
    monthly_payment: int,
    first: date,
    months_left: int,
    *,
    monthly_overpayment: int = 0,
    lumps: dict[date, int] | None = None,
    allowance_pct: Fraction = Fraction(10),
    year_starts: tuple[date, ...] = (),
    fix_end: date | None = None,
) -> OverpaymentPlan:
    """Interest saved and term cut by overpaying; warns past the yearly allowance in the fix.

    The allowance is `allowance_pct` of the balance at the start of each allowance year
    (`year_starts`: the anniversaries or 1 Januaries, by the mortgage's setting).
    """
    base = schedule(balance, rate_pct, monthly_payment, first, months_left)
    plan = schedule(
        balance,
        rate_pct,
        monthly_payment,
        first,
        months_left,
        monthly_overpayment=monthly_overpayment,
        lumps=lumps,
    )
    over: list[tuple[date, int, int]] = []
    starts = [s for s in year_starts if fix_end is None or s < fix_end]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else (fix_end or add_months(start, 12))
        in_year = [row for row in plan if start <= row.on < end]
        if not in_year:
            continue
        overpaid = sum(row.overpayment for row in in_year)
        allowed = int(in_year[0].opening * allowance_pct / 100)
        if overpaid > allowed:
            over.append((start, overpaid, allowed))
    without = sum(row.interest for row in base)
    with_ = sum(row.interest for row in plan)
    return OverpaymentPlan(
        months_saved=len(base) - len(plan),
        interest_saved=without - with_,
        interest_without=without,
        interest_with=with_,
        paid_off_without=base[-1].on if base and base[-1].closing <= 0 else None,
        paid_off_with=plan[-1].on if plan and plan[-1].closing <= 0 else None,
        over_allowance=tuple(over),
    )
