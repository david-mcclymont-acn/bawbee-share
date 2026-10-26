"""Golden tests: the app reproduces the spreadsheet to the penny (PLAN.md §7)."""

from fractions import Fraction
from math import ceil
from typing import Any

from sqlalchemy.orm import Session

from app.domain.versions import calculate, resolve
from app.engine.budget import Result
from app.engine.money import round_pence
from app.importers.seed import CURRENT_PRICE_LINES, ImportResult, apply_post_import_cleanups
from tests.conftest import AS_OF


def _outputs(result: Result) -> dict[str, int]:
    person_1_joint = next(
        t
        for t in result.transfers
        if t.rule == "joint_share" and t.from_account == "person_1_monzo"
    )
    person_2_joint = next(
        t for t in result.transfers if t.rule == "joint_share" and t.from_account == "person_2_rbs"
    )
    starling = next(t for t in result.transfers if t.rule == "cover_account_lines")
    return {
        "person_1_to_joint_suggested_pence": person_1_joint.suggested_pence,
        "person_2_to_joint_suggested_pence": person_2_joint.suggested_pence,
        "person_1_to_joint_actual_pence": person_1_joint.effective_pence,
        "person_2_to_joint_actual_pence": person_2_joint.effective_pence,
        "person_1_to_starling_pence": starling.effective_pence,
        "joint_total_out_pence": round_pence(result.joint_total),
        "joint_surplus_pence": round_pence(result.joint_surplus),
        "person_1_surplus_pence": round_pence(result.personal_surplus["person_1"]),
        "person_2_surplus_pence": round_pence(result.personal_surplus["person_2"]),
        "household_surplus_pence": round_pence(result.household_surplus),
    }


def test_live_maternity_matches_spreadsheet(
    session: Session, raw_import: ImportResult, seed: dict[str, Any]
) -> None:
    expected = seed["expected_spreadsheet_outputs"]["maternity"]
    got = _outputs(calculate(resolve(session, raw_import.live, AS_OF)))
    for key, value in expected.items():
        assert got[key] == value, key


def test_back_to_normal_what_if_matches_spreadsheet(
    session: Session, raw_import: ImportResult, seed: dict[str, Any]
) -> None:
    expected = seed["expected_spreadsheet_outputs"]["standard"]
    got = _outputs(calculate(resolve(session, raw_import.what_if, AS_OF)))
    renamed = {
        "person_1_to_joint_pence": "person_1_to_joint_actual_pence",
        "person_2_to_joint_pence": "person_2_to_joint_actual_pence",
    }
    for key, value in expected.items():
        assert got[renamed.get(key, key)] == value, key
    # Back to normal has no typed-in transfers, so suggested and actual agree.
    assert got["person_1_to_joint_suggested_pence"] == expected["person_1_to_joint_pence"]


def test_joint_total_keeps_fractional_pence(session: Session, raw_import: ImportResult) -> None:
    # Ring is £49.99 a year: 4999/12 pence a month, rounded only for display.
    result = calculate(resolve(session, raw_import.what_if, AS_OF))
    assert result.joint_total.denominator != 1
    assert round_pence(result.joint_total) == 322165


def _independent_joint_transfers(
    seed: dict[str, Any], scenario_id: str, drop: str | None
) -> tuple[Fraction, int, int]:
    """The spreadsheet formula, worked by hand from the seed: joint total and both joint
    transfers, optionally without one joint line. Bills whose price changed between the
    sheets take Maternity's (today's) price, as Back to normal does after import (decision 28)."""
    scenario = next(s for s in seed["scenarios"] if s["id"] == scenario_id)
    maternity = next(s for s in seed["scenarios"] if s["id"] == "maternity")
    todays = {
        (ln["name"], ln.get("provider")): ln["amount_pence"]
        for ln in maternity["lines"]
        if (ln["name"], ln.get("provider")) in CURRENT_PRICE_LINES
    }
    joint = Fraction(0)
    for ln in scenario["lines"]:
        if ln["account"] == "joint_billing" and ln["name"] != drop:
            amount = todays.get((ln["name"], ln.get("provider")), ln.get("amount_pence") or 0)
            joint += Fraction(amount, 12) if ln["frequency"] == "annual" else amount
    if scenario_id == "maternity":
        d = Fraction(scenario["income"]["person_1"]["net_monthly_pence"])
        a = Fraction(scenario["income"]["person_2"]["net_monthly_pence"])
    else:
        ref = seed["reference_income"]
        d = Fraction(ref["person_1_gross_salary_pence"] + ref["person_1_benefits_allowance_pence"])
        a = Fraction(ref["person_2_gross_salary_pence"])

    def to_five(x: Fraction) -> int:
        return ceil(x / 500) * 500

    return joint, to_five(joint * d / (d + a)) + 500, to_five(joint * a / (d + a)) + 500


def test_cleanup_removes_joint_apple_fitness(
    session: Session, raw_import: ImportResult, seed: dict[str, Any]
) -> None:
    assert apply_post_import_cleanups(session, AS_OF) == [
        "remove_joint_apple_fitness",
        "back_to_normal_uses_current_prices",
    ]
    session.commit()

    for version, scenario_id in ((raw_import.live, "maternity"), (raw_import.what_if, "standard")):
        resolved = resolve(session, version, AS_OF)
        on = {(ln.name, ln.account) for ln in resolved.lines}
        assert ("Apple Fitness+", "joint_billing") not in on
        assert ("Apple Fitness+", "person_1_starling") in on

        after = calculate(resolved)
        joint, person_1, person_2 = _independent_joint_transfers(
            seed, scenario_id, "Apple Fitness+"
        )
        _, person_1_with, person_2_with = _independent_joint_transfers(seed, scenario_id, None)
        person_1_t = next(
            t
            for t in after.transfers
            if t.rule == "joint_share" and t.from_account == "person_1_monzo"
        )
        person_2_t = next(
            t
            for t in after.transfers
            if t.rule == "joint_share" and t.from_account == "person_2_rbs"
        )
        assert after.joint_total == joint
        assert (person_1_t.suggested_pence, person_2_t.suggested_pence) == (person_1, person_2)
        # Dropping it lowers both transfers (Person 1's most, as they paid it in full as well).
        assert person_1 < person_1_with
        assert person_2 <= person_2_with
