"""The mortgage (PLAN.md §10): the product, statements, and the remortgage what-if.

The arithmetic is engine/mortgage.py. The mortgage's payment is the one source of truth
for the "Mortgage (Northfield)" budget line: setting it up, or changing the payment, moves
the live line to match (fixing the sheet's £1,192.46 vs £1,193.80).
"""

import re
from datetime import date
from fractions import Fraction
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit, editing
from app.domain.models import BudgetLine, BudgetVersion, Mortgage, MortgageStatement, User
from app.domain.versions import live_version, resolve
from app.engine import mortgage as engine
from app.engine.money import decimal_text
from app.engine.mortgage import add_months, months_between

# Past mortgages from the spreadsheet's Mortgage Info sheet (PLAN.md §10): lender and year.
HISTORY = (("Oak Bank", 2013), ("Elm Lending", 2016), ("Elm Lending", 2019), ("Ash BS", 2021))


class MortgageError(ValueError):
    pass


def current(session: Session) -> Mortgage | None:
    return session.scalars(
        select(Mortgage).where(Mortgage.status == "current").order_by(Mortgage.id.desc())
    ).first()


def history(session: Session) -> list[Mortgage]:
    return list(
        session.scalars(
            select(Mortgage).where(Mortgage.status == "past").order_by(Mortgage.started_on)
        )
    )


def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def to_engine(m: Mortgage) -> engine.Mortgage:
    if None in (m.principal_pence, m.rate_pct, m.term_months, m.first_payment_on, m.payment_pence):
        raise MortgageError("The mortgage needs its amount, rate, term, first payment and payment.")
    return engine.Mortgage(
        principal=m.principal_pence or 0,
        rate_pct=Fraction(m.rate_pct or "0"),
        term_months=m.term_months or 0,
        first_payment=first_of_month(m.first_payment_on or date.min),
        monthly_payment=m.payment_pence or 0,
        fix_end=first_of_month(m.fix_end_on) if m.fix_end_on else date.max,
    )


def latest_statement(m: Mortgage) -> MortgageStatement | None:
    return m.statements[-1] if m.statements else None


def balance(m: Mortgage, on: date) -> tuple[int, bool]:
    """After the payment in `on`'s month; True when anchored to a statement."""
    st = latest_statement(m)
    anchor = (st.on, st.balance_pence) if st else None
    return engine.balance_on(to_engine(m), first_of_month(on), anchor)


def months_left(m: Mortgage, on: date) -> int:
    first = first_of_month(m.first_payment_on or on)
    return max(0, (m.term_months or 0) - months_between(first, first_of_month(on)) - 1)


def allowance_year_starts(m: Mortgage, start: date, end: date) -> tuple[date, ...]:
    """The start of each overpayment-allowance year from start to end."""
    if m.allowance_basis == "calendar_year":
        firsts = [date(y, 1, 1) for y in range(start.year, end.year + 1)]
    else:
        anniversary = m.started_on or m.first_payment_on or start
        firsts = [date(y, anniversary.month, 1) for y in range(start.year - 1, end.year + 1)]
    # The allowance year the plan starts in begins at the plan's start.
    within = [d for d in firsts if start < d < end]
    return (start, *within)


def remortgage_window(m: Mortgage, lead_months: int) -> date | None:
    return add_months(first_of_month(m.fix_end_on), -lead_months) if m.fix_end_on else None


def link_line(session: Session, m: Mortgage, actor: User | None, on: date) -> None:
    """Make the linked budget line's amount the mortgage payment (one source of truth)."""
    if m.line_id is None or m.payment_pence is None:
        return
    line = session.get(BudgetLine, m.line_id)
    if line is None:
        return
    live = resolve(session, live_version(session), on)
    resolved = next((ln for ln in live.lines if ln.line_id == line.id), None)
    if resolved is not None and resolved.amount_pence != m.payment_pence:
        editing.update_live_line(session, line, {"amount_pence": m.payment_pence}, on, actor)


def find_mortgage_line(session: Session, lender: str) -> BudgetLine | None:
    for line in session.scalars(
        select(BudgetLine).where(
            BudgetLine.status == "active", BudgetLine.scope_version_id.is_(None)
        )
    ):
        if "mortgage" in line.name.lower() and (line.provider or "").lower() == lender.lower():
            return line
    return None


def _month(raw: str | None) -> date | None:
    """'2027-11 (exact day TBC ...)' or '2027-11-24' to a date (1st if no day)."""
    match = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", raw or "")
    if match is None:
        return None
    return date(int(match[1]), int(match[2]), int(match[3] or 1))


def import_from_seed(
    session: Session, seed: dict[str, Any], actor: User | None, on: date
) -> Mortgage | None:
    """Set up the current mortgage and the past ones from the seed, once."""
    raw = seed.get("mortgage")
    if not raw or current(session) is not None:
        return current(session)
    fix_end_raw = raw.get("fixed_rate_end_date")
    basis_raw = str(raw.get("overpayment_allowance_basis") or "")
    m = Mortgage(
        lender=raw["lender"],
        status="current",
        principal_pence=raw["principal_pence"],
        rate_pct=decimal_text(Fraction(str(raw["rate"])) * 100),
        term_months=raw["term_months"],
        started_on=date.fromisoformat(raw["start_date"]),
        first_payment_on=date.fromisoformat(raw["first_payment_date"]),
        fix_end_on=_month(fix_end_raw),
        # Exact only when the seed gives a day and doesn't say it's still to be confirmed.
        fix_end_exact=bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(fix_end_raw or ""))),
        payment_pence=raw["monthly_payment_pence"],
        overpay_allowance_pct=str(raw.get("overpayment_allowance_pct_per_year") or 10),
        allowance_basis="calendar_year" if "calendar" in basis_raw.lower() else "anniversary",
        property_value_pence=raw.get("property_value_pence"),
        property_value_on=_month(raw.get("value_as_of")),
        notes=None
        if "TBC" not in basis_raw
        else "The overpayment allowance basis is to be confirmed.",
    )
    line = find_mortgage_line(session, m.lender)
    m.line_id = line.id if line else None
    session.add(m)
    for lender, year in HISTORY:
        session.add(Mortgage(lender=lender, status="past", started_on=date(year, 1, 1)))
    session.flush()
    statement = raw.get("latest_statement") or {}
    if statement.get("balance_pence") and statement.get("date"):
        session.add(
            MortgageStatement(
                mortgage_id=m.id,
                on=date.fromisoformat(statement["date"]),
                balance_pence=statement["balance_pence"],
            )
        )
    audit.record(session, actor, "mortgage", m.id, "import", None, {"lender": m.lender})
    link_line(session, m, actor, on)
    session.flush()
    return m


def add_statement(
    session: Session, m: Mortgage, on: date, balance_pence: int, actor: User | None
) -> MortgageStatement:
    if balance_pence <= 0:
        raise MortgageError("A statement balance is more than zero.")
    st = MortgageStatement(
        mortgage_id=m.id, on=on, balance_pence=balance_pence, created_by=actor.id if actor else None
    )
    session.add(st)
    session.flush()
    session.refresh(m)
    audit.record(
        session,
        actor,
        "mortgage",
        m.id,
        "statement",
        None,
        {"on": on.isoformat(), "balance_pence": balance_pence},
    )
    return st


EDITABLE = {
    "fix_end_on",
    "fix_end_exact",
    "payment_pence",
    "rate_pct",
    "overpay_allowance_pct",
    "allowance_basis",
    "property_value_pence",
    "property_value_on",
}


def update(
    session: Session, m: Mortgage, changes: dict[str, Any], actor: User | None, on: date
) -> None:
    unknown = set(changes) - EDITABLE
    if unknown:
        raise MortgageError(f"Can't change {', '.join(sorted(unknown))} here.")
    before = {k: getattr(m, k) for k in changes}
    for key, value in changes.items():
        setattr(m, key, value)
    session.flush()
    audit.record(
        session,
        actor,
        "mortgage",
        m.id,
        "update",
        {k: str(v) for k, v in before.items()},
        {k: str(v) for k, v in changes.items()},
    )
    if "payment_pence" in changes:
        link_line(session, m, actor, on)


def remortgage_whatif(
    session: Session,
    m: Mortgage,
    rate_pct: Fraction,
    actor: User | None,
    on: date,
    base: BudgetVersion | None = None,
) -> tuple[BudgetVersion, int]:
    """A what-if from the fix end: the same balance at the new rate over the remaining term.

    It builds on `base` (the live budget unless another what-if is chosen), so a path that
    goes "Return 3 days" then "Remortgage" can keep the return-to-work changes.
    """
    if m.fix_end_on is None:
        raise MortgageError("Enter when the fix ends first.")
    if m.line_id is None:
        raise MortgageError("Link the mortgage to its budget line first.")
    if not Fraction(0) < rate_pct < Fraction(20):
        raise MortgageError("Enter the new rate as a percentage, e.g. 4.19.")
    fix_end = first_of_month(m.fix_end_on)
    owed, _ = balance(m, add_months(fix_end, -1))
    remaining = months_left(m, add_months(fix_end, -1))
    new_payment = engine.payment(owed, rate_pct, remaining)
    rate_text = f"{rate_pct.numerator / rate_pct.denominator:g}"
    builds_on = base or live_version(session)
    if builds_on.type not in ("live", "what_if") or builds_on.status not in ("active", "draft"):
        raise MortgageError("A remortgage what-if builds on the live budget or a what-if.")
    after = "" if builds_on.type == "live" else f", after {builds_on.name}"
    version = editing.create_what_if(
        session,
        f"Remortgage at {rate_text}%{after}",
        builds_on,
        actor,
        description=(
            f"{m.lender} fix ends {fix_end:%B %Y}: £{owed / 100:,.2f} over {remaining} months "
            f"at {rate_text}% is £{new_payment / 100:,.2f} a month."
        ),
        planned_start=fix_end,
    )
    editing.override_line(session, version, m.line_id, {"amount_pence": new_payment}, on, actor)
    return version, new_payment
