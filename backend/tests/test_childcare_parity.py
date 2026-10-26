"""The childcare port gives the old calculator's answers (PLAN.md §9, Phase 6 acceptance).

reference/childcare-app/parity.json is what the old calculator's own JavaScript computes
for its saved state and for variations that reach every branch
(`node scripts/childcare-golden.mjs`). Here the Python port runs on the same inputs.
The old code works in floating point, so figures agree to within half a penny.
"""

import dataclasses
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from app.domain import taxconfig
from app.engine import childcare, tax

PARITY = Path(__file__).resolve().parents[2] / "reference" / "childcare-app" / "parity.json"
CASES: list[dict[str, Any]] = json.loads(PARITY.read_text(encoding="utf-8"))["cases"]
HALF_PENNY = Fraction(1, 200)


def old_year() -> tax.TaxYear:
    """2025/26 as the old calculator had it: tiered on the previous year's pay."""
    year = taxconfig.year("2025-26")
    return dataclasses.replace(year, nhs_tiers=year.nhs_tiers_continuing)


def _f(value: Any) -> Fraction:
    return Fraction(str(value)) if value not in ("", None) else Fraction(0)


def profile(inputs: dict[str, Any]) -> childcare.Profile:
    f = inputs["fields"]
    return childcare.Profile(
        salary=_f(f["i-salary"]),
        ref_hours=_f(f["i-refhours"]),
        ref_days=_f(f["i-refdays"]),
        bank_holidays=_f(f["i-bank"]),
        leave_days=_f(f["i-leave"]),
        partner_income=_f(f["i-partner"]),
        children=tuple(inputs["children"]),
        tax_code=f["i-taxcode"] or None,
        net_pay_arrangement=f["i-npa"] is True,
        use_child_benefit=f["i-usecb"] is True,
        child_benefit_first_weekly=_f(f["i-cb1"]),
        child_benefit_other_weekly=_f(f["i-cb2"]),
        tfc_cap=_f(f["i-tfc"]),
        tfc_rate=_f(f["i-tfcrate"]),
        ni_primary_threshold=_f(f["i-nilo"]),
        ni_upper_limit=_f(f["i-nihi"]),
    )


def nursery(raw: dict[str, Any]) -> childcare.Nursery:
    return childcare.Nursery(
        name=raw["name"],
        basis=raw["basis"],
        rates=tuple(
            childcare.Rate(_f(r["rate"]), _f(r["disc"]), _f(r["min"])) for r in raw["rates"]
        ),
        takes_tfc=raw["tfc"] is not False,
        bills_leave=raw["billsLeave"] is True,
    )


def pattern(raw: dict[str, Any]) -> childcare.Pattern:
    return childcare.Pattern(_f(raw["days"]), _f(raw["hours"]))


def close(ours: Fraction, theirs: float) -> bool:
    return abs(ours - Fraction(theirs)) <= HALF_PENNY


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_every_scenario_matches_the_old_calculator(case: dict[str, Any]) -> None:
    year = old_year()
    p = profile(case["inputs"])
    nurseries = [nursery(n) for n in case["inputs"]["nurseries"]]
    patterns = [pattern(x) for x in case["inputs"]["patterns"]]
    assert (
        tax.allowance_from_code(p.tax_code, year.personal_allowance) == case["personal_allowance"]
    )
    for want in case["results"]:
        s = childcare.scenario(p, nurseries[want["nursery"]], patterns[want["pattern"]], year)
        where = f"{case['name']}: nursery {want['nursery']}, pattern {want['pattern']}"
        assert s.earnings.pension_rate == Fraction(str(want["pension_rate"])), where
        pairs = {
            "gross": s.earnings.gross,
            "pension": s.earnings.pension,
            "tax": s.earnings.tax,
            "ni": s.earnings.ni,
            "net": s.earnings.net,
            "days": s.childcare.days,
            "attended": s.childcare.counts.attended,
            "leave": s.childcare.counts.leave,
            "bank": s.childcare.counts.bank,
            "cost": s.childcare.cost,
            "tfc": s.childcare.tfc,
            "childcare_net": s.childcare.net,
            "cb_gross": s.child_benefit.gross,
            "cb_net": s.child_benefit.net,
            "clawback": s.child_benefit.clawback_pct,
            "annual": s.annual,
            "monthly": s.monthly,
        }
        for key, ours in pairs.items():
            assert close(ours, want[key]), f"{where}: {key} {float(ours)} != {want[key]}"
        for line, old in zip(s.childcare.lines, want["lines"], strict=True):
            assert line.discount_applied == old["applied"], where
            assert close(line.cost, old["cost"]), where
            assert close(line.tfc, old["tfc"]), where
        assert [sl.label for sl in s.earnings.tax_slices] == [t["label"] for t in want["tax_split"]]


def test_the_saved_state_still_matches_its_payslip() -> None:
    """The old app's own check: its calculated take-home matched Person 2's payslip."""
    case = CASES[0]
    p = profile(case["inputs"])
    net = childcare.earnings(p, p.ref_hours, old_year()).net_monthly
    assert close(net, case["payslip_monthly"])
    assert abs(net - _f(case["inputs"]["fields"]["i-actual"])) < Fraction(1, 100)


def test_the_matrix_left_over_is_the_detail_panels_monthly_figure() -> None:
    """Regression (PLAN.md §9): the matrix once divided an already-monthly figure by 12."""
    case = CASES[0]
    p = profile(case["inputs"])
    year = old_year()
    nurseries = tuple(nursery(n) for n in case["inputs"]["nurseries"])
    patterns = tuple(pattern(x) for x in case["inputs"]["patterns"])
    for row in childcare.matrix(p, nurseries, patterns, year):
        for cell in row:
            assert cell.monthly == cell.annual / 12
            assert cell.monthly * 12 == cell.earnings.net + cell.child_benefit.net - (
                cell.childcare.net
            )


def test_the_config_holds_the_old_calculators_tables() -> None:
    """2025/26 in config/tax is what the old app hard-coded (it had the Advanced top as
    £112,570 above the allowance; HMRC gives £125,140, which only differs above £112k)."""
    year = taxconfig.year("2025-26")
    old_bands = [(2827, 19), (14921, 20), (31092, 21), (62430, 42), (112570, 45), (None, 48)]
    for band, (up_to, rate) in zip(year.bands, old_bands, strict=True):
        assert band.rate == rate
        if band.label != "Advanced":
            assert band.up_to == (None if up_to is None else Fraction(up_to))
    old_tiers = [13330, 26762, 31669, 39734, 41669, 50650, 54811, 76652, None]
    assert [t.up_to for t in year.nhs_tiers_continuing] == [
        None if x is None else Fraction(x) for x in old_tiers
    ]


def test_funded_hours_come_off_before_tax_free_childcare() -> None:
    case = CASES[0]
    p = profile(case["inputs"])
    year = taxconfig.year("2026-27")
    n = nursery(case["inputs"]["nurseries"][0])
    four_days = pattern(case["inputs"]["patterns"][2])
    plain = childcare.scenario(p, n, four_days, year)
    funded = childcare.scenario(p, n, four_days, year, (year.funded_hours_per_year, Fraction(0)))
    first, second = funded.childcare.lines
    # £60 a day over 9 hours: 1,140 funded hours are worth £7,600 off the first child.
    assert first.funded == Fraction(1140) * 60 / 9
    assert second.funded == 0
    assert first.cost == plain.childcare.lines[0].cost - first.funded
    assert first.tfc == min(first.cost * Fraction(20, 100), Fraction(2000))
    assert funded.annual > plain.annual
