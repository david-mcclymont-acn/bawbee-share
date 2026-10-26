"""Childcare: the setup, the matrix, and linking a scenario to a what-if (PLAN.md §9).

The arithmetic is engine/childcare.py (the old calculator, ported). This module turns the
stored setup into engine inputs, imports the old calculator's state.json, and applies a
linked scenario to a budget version: the earner's pay for those hours (gross and
take-home) and a calculated nursery line, with Tax-Free Childcare as a line of its own.

Funded hours follow each child's date of birth, so as the forecast moves on, a child's
bill drops in the term after their third birthday. That's one of the two deliberate
exceptions to "no dated steps" (PLAN.md §9), and the screens say so.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit, taxconfig
from app.domain.models import (
    Account,
    BudgetVersion,
    ChildcareChild,
    ChildcareNursery,
    ChildcarePattern,
    ChildcareProfile,
    ChildcareScenario,
    Person,
    User,
)
from app.engine import childcare
from app.engine.money import decimal_text, round_pence

if TYPE_CHECKING:
    from app.domain.versions import ResolvedBudget

# The old calculator's form fields, and what they're called here.
OLD_FIELDS = {
    "i-salary": "salary",
    "i-refhours": "ref_hours",
    "i-refdays": "ref_days",
    "i-bank": "bank_holidays",
    "i-leave": "leave_days",
    "i-actual": "payslip_monthly",
    "i-partner": "partner_income",
    "i-cb1": "cb_first_weekly",
    "i-cb2": "cb_other_weekly",
    "i-tfc": "tfc_cap",
    "i-tfcrate": "tfc_rate",
    "i-usecb": "use_child_benefit",
    "i-taxcode": "tax_code",
    "i-nilo": "ni_primary_threshold",
    "i-nihi": "ni_upper_limit",
    "i-npa": "net_pay_arrangement",
}
DEFAULTS: dict[str, Any] = {
    "salary": "0",
    "ref_hours": "37.5",
    "ref_days": "5",
    "bank_holidays": "9",
    "leave_days": "27.5",
    "partner_income": "0",
    "use_child_benefit": True,
    "net_pay_arrangement": True,
    "tax_code": None,
}
# Inputs that fall back to the tax year's figure when left empty.
FROM_TAX_YEAR = (
    "cb_first_weekly",
    "cb_other_weekly",
    "tfc_cap",
    "tfc_rate",
    "ni_primary_threshold",
    "ni_upper_limit",
)
COMPUTED_LINE_BASE = -1_000_000  # computed lines get ids that can't clash with real ones


class ChildcareError(ValueError):
    pass


def _f(value: Any) -> Fraction:
    return Fraction(str(value)) if value not in (None, "") else Fraction(0)


def _opt(value: Any) -> Fraction | None:
    return None if value in (None, "") else Fraction(str(value))


# ---- the stored setup ------------------------------------------------------------------


def profile_row(session: Session) -> ChildcareProfile:
    row = session.scalars(select(ChildcareProfile).order_by(ChildcareProfile.id)).first()
    if row is None:
        person_2 = session.scalars(select(Person).where(Person.key == "person_2")).first()
        row = ChildcareProfile(person_id=person_2.id if person_2 else None, fields_json={})
        session.add(row)
        session.flush()
    return row


def fields(session: Session) -> dict[str, Any]:
    return {**DEFAULTS, **(profile_row(session).fields_json or {})}


def children(session: Session) -> list[ChildcareChild]:
    return list(
        session.scalars(select(ChildcareChild).order_by(ChildcareChild.position, ChildcareChild.id))
    )


def nurseries(session: Session) -> list[ChildcareNursery]:
    return list(
        session.scalars(
            select(ChildcareNursery).order_by(ChildcareNursery.position, ChildcareNursery.id)
        )
    )


def patterns(session: Session) -> list[ChildcarePattern]:
    return list(
        session.scalars(
            select(ChildcarePattern).order_by(ChildcarePattern.position, ChildcarePattern.id)
        )
    )


def engine_profile(session: Session) -> childcare.Profile:
    f = fields(session)
    return childcare.Profile(
        salary=_f(f["salary"]),
        ref_hours=_f(f["ref_hours"]),
        ref_days=_f(f["ref_days"]),
        bank_holidays=_f(f["bank_holidays"]),
        leave_days=_f(f["leave_days"]),
        partner_income=_f(f["partner_income"]),
        children=tuple(c.name for c in children(session)),
        tax_code=f.get("tax_code") or None,
        net_pay_arrangement=bool(f["net_pay_arrangement"]),
        use_child_benefit=bool(f["use_child_benefit"]),
        child_benefit_first_weekly=_opt(f.get("cb_first_weekly")),
        child_benefit_other_weekly=_opt(f.get("cb_other_weekly")),
        tfc_cap=_opt(f.get("tfc_cap")),
        tfc_rate=_opt(f.get("tfc_rate")),
        ni_primary_threshold=_opt(f.get("ni_primary_threshold")),
        ni_upper_limit=_opt(f.get("ni_upper_limit")),
    )


def engine_nursery(row: ChildcareNursery) -> childcare.Nursery:
    return childcare.Nursery(
        name=row.name,
        basis=row.basis,  # type: ignore[arg-type]
        rates=tuple(
            childcare.Rate(_f(r.get("rate")), _f(r.get("discount_pct")), _f(r.get("min_days")))
            for r in row.rates_json or []
        ),
        takes_tfc=row.takes_tfc,
        bills_leave=row.bills_leave,
    )


def engine_pattern(row: ChildcarePattern) -> childcare.Pattern:
    return childcare.Pattern(_f(row.days), _f(row.hours))


def funded_from(child: ChildcareChild, label: str) -> date | None:
    if child.born_on is None:
        return None
    third = taxconfig.funded_from(child.born_on, label)
    if child.eligible_at_two:
        return taxconfig.funded_from(
            date(child.born_on.year - 1, child.born_on.month, min(child.born_on.day, 28)), label
        )
    return third


def funded_hours(session: Session, on: date) -> tuple[Fraction, ...]:
    """Each child's funded hours a year, for the month `on` falls in (0 before they start)."""
    year = taxconfig.year_for(on)
    out: list[Fraction] = []
    for child in children(session):
        start = funded_from(child, year.label)
        out.append(year.funded_hours_per_year if start is not None and start <= on else Fraction(0))
    return tuple(out)


# ---- the matrix (the old calculator's main view) ----------------------------------------


def _pence(pounds: Fraction) -> int:
    return round_pence(pounds * 100)


@dataclass(frozen=True)
class Cell:
    nursery_id: int
    pattern_id: int
    scenario: childcare.Scenario


def cells(
    session: Session, on: date, *, with_funding: bool = True
) -> tuple[list[ChildcareNursery], list[ChildcarePattern], list[list[Cell]]]:
    year = taxconfig.year_for(on)
    profile = engine_profile(session)
    funded = funded_hours(session, on) if with_funding else ()
    n_rows, p_rows = nurseries(session), patterns(session)
    grid = [
        [
            Cell(
                n.id,
                p.id,
                childcare.scenario(profile, engine_nursery(n), engine_pattern(p), year, funded),
            )
            for p in p_rows
        ]
        for n in n_rows
    ]
    return n_rows, p_rows, grid


def scenario_out(s: childcare.Scenario) -> dict[str, Any]:
    """Every figure the screens show, in pence a year (÷ 12 happens once, for a month)."""
    e, n, cb = s.earnings, s.childcare, s.child_benefit

    def month(pounds_a_year: Fraction) -> int:
        """A month's figure, divided exactly then rounded once (never rounded twice)."""
        return _pence(pounds_a_year / 12)

    return {
        "month": {
            "pay": {
                "gross_pence": month(e.gross),
                "pension_pence": month(e.pension),
                "tax_slices": [
                    {"label": sl.label, "rate": decimal_text(sl.rate), "pence": month(sl.amount)}
                    for sl in e.tax_slices
                ],
                "ni_pence": month(e.ni),
                "net_pence": month(e.net),
            },
            "childcare": {
                "before_tfc_pence": month(n.cost),
                "tfc_pence": month(n.tfc),
                "net_pence": month(n.net),
                "children": [
                    {
                        "cost_pence": month(line.cost),
                        "tfc_pence": month(line.tfc),
                        "funded_pence": month(line.funded),
                    }
                    for line in n.lines
                ],
            },
            "child_benefit_pence": month(cb.net),
        },
        "monthly_left_pence": _pence(s.monthly),
        "annual_left_pence": _pence(s.annual),
        "share_of_take_home": str(round(float(s.share_of_take_home) * 100, 1)),
        "pay": {
            "gross_pence": _pence(e.gross),
            "pension_rate": decimal_text(e.pension_rate),
            "pension_pence": _pence(e.pension),
            "tax_pence": _pence(e.tax),
            "tax_slices": [
                {"label": sl.label, "rate": decimal_text(sl.rate), "pence": _pence(sl.amount)}
                for sl in e.tax_slices
            ],
            "ni_pence": _pence(e.ni),
            "net_pence": _pence(e.net),
        },
        "childcare": {
            "days_billed": str(round(float(n.days), 1)),
            "days_attended": str(round(float(n.counts.attended), 1)),
            "leave_days": str(round(float(n.counts.leave), 1)),
            "bank_holidays": str(round(float(n.counts.bank), 1)),
            "hours_per_day": str(round(float(n.hours_per_day), 1)),
            "bills_leave": n.bills_leave,
            "takes_tfc": n.takes_tfc,
            "before_tfc_pence": _pence(n.cost),
            "tfc_pence": _pence(n.tfc),
            "net_pence": _pence(n.net),
            "funded_pence": _pence(n.funded),
            "leave_waste_pence": _pence(n.leave_waste),
            "children": [
                {
                    "name": line.name,
                    "rate_pence": _pence(line.rate),
                    "effective_pence": _pence(line.effective),
                    "per_day_pence": _pence(line.per_day),
                    "discount_applied": line.discount_applied,
                    "discount_pct": decimal_text(line.discount_pct),
                    "funded_pence": _pence(line.funded),
                    "cost_pence": _pence(line.cost),
                    "tfc_pence": _pence(line.tfc),
                }
                for line in n.lines
            ],
        },
        "child_benefit": {
            "gross_pence": _pence(cb.gross),
            "net_pence": _pence(cb.net),
            "clawback_pct": str(round(float(cb.clawback_pct), 1)),
        },
    }


def payslip_check(session: Session, on: date) -> dict[str, Any] | None:
    """The old calculator's own check: take-home at the reference hours vs the payslip."""
    f = fields(session)
    payslip = _opt(f.get("payslip_monthly"))
    if not payslip:
        return None
    profile = engine_profile(session)
    calc = childcare.earnings(profile, profile.ref_hours, taxconfig.year_for(on)).net_monthly
    diff = calc - payslip
    return {
        "calculated_pence": _pence(calc),
        "payslip_pence": _pence(payslip),
        "difference_pence": _pence(diff),
        "matches": abs(diff) <= 5,
    }


# ---- importing the old calculator's state.json ---------------------------------------------


def import_state(session: Session, state: dict[str, Any], actor: User | None) -> dict[str, int]:
    """Replace the setup with the old calculator's saved state (children, patterns, nurseries)."""
    if not isinstance(state.get("nurseries"), list) or not isinstance(state.get("patterns"), list):
        raise ChildcareError("That isn't the old childcare calculator's state.json.")
    for scenario in session.scalars(select(ChildcareScenario)):
        if scenario.version_id is not None:
            raise ChildcareError(
                "Unlink the childcare scenarios from their what-ifs before importing over them."
            )
        session.delete(scenario)
    for model in (ChildcareChild, ChildcareNursery, ChildcarePattern):
        for row in session.scalars(select(model)):
            session.delete(row)
    session.flush()
    for i, name in enumerate(state.get("children") or []):
        session.add(ChildcareChild(name=str(name), position=i))
    for i, p in enumerate(state["patterns"]):
        session.add(ChildcarePattern(days=str(p["days"]), hours=str(p["hours"]), position=i))
    for i, n in enumerate(state["nurseries"]):
        session.add(
            ChildcareNursery(
                name=str(n.get("name") or f"Nursery {i + 1}"),
                basis="hour" if n.get("basis") == "hour" else "day",
                takes_tfc=n.get("tfc") is not False,
                bills_leave=n.get("billsLeave") is True,
                rates_json=[
                    {
                        "rate": str(r.get("rate", 0)),
                        "discount_pct": str(r.get("disc", 0)),
                        "min_days": str(r.get("min", 0)),
                    }
                    for r in n.get("rates") or []
                ],
                position=i,
            )
        )
    row = profile_row(session)
    old = state.get("fields") or {}
    row.fields_json = {
        OLD_FIELDS[k]: (v if isinstance(v, bool) else str(v))
        for k, v in old.items()
        if k in OLD_FIELDS
    }
    session.flush()
    audit.record(
        session,
        actor,
        "childcare",
        row.id,
        "import_state",
        None,
        {
            "children": len(state.get("children") or []),
            "patterns": len(state["patterns"]),
            "nurseries": len(state["nurseries"]),
        },
    )
    return {
        "children": len(state.get("children") or []),
        "patterns": len(state["patterns"]),
        "nurseries": len(state["nurseries"]),
    }


# ---- linking a scenario to a what-if -----------------------------------------------------


def linked_scenario(session: Session, chain: list[BudgetVersion]) -> ChildcareScenario | None:
    """The scenario linked to this version or the nearest version it's based on."""
    for version in chain:
        found = session.scalars(
            select(ChildcareScenario).where(ChildcareScenario.version_id == version.id)
        ).first()
        if found is not None:
            return found
    return None


def computed_line_ids(scenario: ChildcareScenario) -> tuple[int, int]:
    base = COMPUTED_LINE_BASE - scenario.id * 10
    return base, base - 1


def apply_link(session: Session, chain: list[BudgetVersion], resolved: "ResolvedBudget") -> None:
    """Set the earner's pay from the linked scenario and add the nursery as calculated lines."""
    from app.domain.versions import ResolvedLine

    scenario = linked_scenario(session, chain)
    if scenario is None:
        return
    on = resolved.as_of
    try:
        year = taxconfig.year_for(on)
    except taxconfig.TaxConfigMissing:
        return
    profile = engine_profile(session)
    s = childcare.scenario(
        profile,
        engine_nursery(scenario.nursery),
        engine_pattern(scenario.pattern),
        year,
        funded_hours(session, on),
    )
    row = profile_row(session)
    person = session.get(Person, row.person_id) if row.person_id else None
    if person is not None:
        resolved.incomes = [
            dataclasses.replace(
                income,
                net_monthly_pence=_pence(s.earnings.net_monthly),
                gross_annual_pence=_pence(s.earnings.gross),
            )
            if income.person == person.key
            else income
            for income in resolved.incomes
        ]
    account = session.get(Account, scenario.account_id)
    if account is None:
        return
    nursery_id, tfc_id = computed_line_ids(scenario)
    funded = s.childcare.funded
    note = f"Calculated from Childcare: {scenario.nursery.name}, {scenario.name}." + (
        f" Funded hours take £{_pence(funded) / 100:,.2f} a year off." if funded else ""
    )
    resolved.lines.append(
        ResolvedLine(
            line_id=nursery_id,
            name="Nursery",
            kind="bill",
            account=account.key,
            provider=scenario.nursery.name,
            category="Childcare",
            amount_pence=_pence(s.childcare.cost / 12),
            notes=note,
            origin="computed",
        )
    )
    if s.childcare.tfc:
        resolved.lines.append(
            ResolvedLine(
                line_id=tfc_id,
                name="Tax-Free Childcare top-up",
                kind="bill",
                account=account.key,
                provider="HMRC",
                category="Childcare",
                amount_pence=-_pence(s.childcare.tfc / 12),
                notes="The government's 20% towards the nursery bill.",
                origin="computed",
            )
        )


def link(
    session: Session,
    scenario: ChildcareScenario,
    version: BudgetVersion | None,
    actor: User | None,
) -> None:
    if version is not None and version.type != "what_if":
        raise ChildcareError("Childcare can be linked to a what-if, not the live budget.")
    before = scenario.version_id
    if version is not None:
        other = session.scalars(
            select(ChildcareScenario).where(
                ChildcareScenario.version_id == version.id, ChildcareScenario.id != scenario.id
            )
        ).first()
        if other is not None:
            other.version_id = None
    scenario.version_id = version.id if version else None
    session.flush()
    audit.record(
        session,
        actor,
        "childcare_scenario",
        scenario.id,
        "link" if version else "unlink",
        {"version_id": before},
        {"version_id": scenario.version_id},
    )
