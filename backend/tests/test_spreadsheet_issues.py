"""One test per problem in the old spreadsheet (PLAN.md §4)."""

from datetime import date
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import editing
from app.domain.checks import duplicate_lines, stale_inputs
from app.domain.models import BudgetLine, FollowUp
from app.domain.versions import calculate, diff, redundant_overrides, resolve, to_engine
from app.engine.calendar import PayDayRule, pay_day
from app.engine.money import round_pence
from app.engine.projection import project_month
from app.importers.seed import ImportResult
from tests.conftest import AS_OF


def test_4_1_surplus_split_is_derived_not_hard_coded(
    session: Session, imported: ImportResult
) -> None:
    """The sheet hard-coded £4,002.38 as the combined surplus; the real figure is £3,750.75."""
    editing.set_settings(session, imported.live, {"split_basis": "surplus"}, None)
    result = calculate(resolve(session, imported.live, AS_OF))

    # Personal surplus before the joint transfer: take-home minus everything else leaving.
    person_1 = 421580 - (13500 + 40000 + 0 + 30000 + 15000 + 5000)
    person_2 = 152375 - (37186 + 1895 + 1299 + 30000 + 5000 + 10000 + 10000)
    assert person_1 + person_2 == 375075
    assert result.shares == {
        "person_1": Fraction(person_1, 375075),
        "person_2": Fraction(person_2, 375075),
    }
    assert sum(result.shares.values()) == 1
    # Under every basis, the suggested transfers fully fund the joint account.
    for basis, transfers in result.split_options.items():
        assert sum(transfers.values()) >= result.joint_total, basis


def test_4_2_household_surplus_always_includes_joint(
    session: Session, raw_import: ImportResult
) -> None:
    live = calculate(resolve(session, raw_import.live, AS_OF))
    back_to_normal = calculate(resolve(session, raw_import.what_if, AS_OF))
    for result in (live, back_to_normal):
        assert (
            result.household_surplus == sum(result.personal_surplus.values()) + result.joint_surplus
        )
    # The Maternity sheet showed £155.75 because it left the joint surplus out.
    assert round_pence(live.household_surplus) == 44325
    assert round_pence(back_to_normal.household_surplus) == 81725


def test_4_3_what_ifs_store_only_differences(session: Session, imported: ImportResult) -> None:
    back_to_normal = resolve(session, imported.what_if, AS_OF)
    live = resolve(session, imported.live, AS_OF)
    actions = [o.action for o in imported.what_if.overrides]
    # 59 lines in the Back to normal sheet, but only the 30 that differ are stored (36 differ in the
    # sheet; the six old prices were dropped so Back to normal uses today's, decision 28).
    assert (actions.count("modify"), actions.count("add"), actions.count("remove")) == (18, 8, 4)
    assert redundant_overrides(session, imported.what_if, AS_OF) == []
    inherited = [ln for ln in back_to_normal.lines if ln.origin == "base"]
    assert len(inherited) == len(live.lines) - 18 - 4

    # A live change to a line Back to normal doesn't override reaches Back to normal with no action.
    octopus = live.find("Gas/Electricity")
    line = session.get(BudgetLine, octopus.line_id)
    assert line is not None
    editing.update_live_line(session, line, {"amount_pence": 21000}, AS_OF, None)
    assert resolve(session, imported.what_if, AS_OF).line(octopus.line_id).amount_pence == 21000


def test_4_4_stale_inputs_are_flagged(session: Session, imported: ImportResult) -> None:
    stale = stale_inputs(session, AS_OF)
    assert {s.label for s in stale} == {
        "Person 1's salary (Example Ltd)",
        "Person 2's salary (NHS Scotland)",
    }
    assert all(s.as_of == date(2022, 11, 1) for s in stale)
    assert stale_inputs(session, date(2023, 6, 1)) == []

    mortgage = resolve(session, imported.live, AS_OF).find("Mortgage")
    notes = session.scalars(
        select(FollowUp.note).where(
            FollowUp.line_id == mortgage.line_id, FollowUp.reason == "check_price"
        )
    ).all()
    assert len(notes) == 1
    assert "£1,193.80" in (notes[0] or "")


def test_4_5_apple_fitness_duplicate_is_visible_then_fixed(
    session: Session, raw_import: ImportResult
) -> None:
    from app.importers.seed import apply_post_import_cleanups

    dupes = duplicate_lines(resolve(session, raw_import.live, AS_OF))
    assert [(d.name, d.accounts) for d in dupes] == [
        ("Apple Fitness+", ("joint_billing", "person_1_starling"))
    ]
    apply_post_import_cleanups(session, AS_OF)
    assert duplicate_lines(resolve(session, raw_import.live, AS_OF)) == []
    assert duplicate_lines(resolve(session, raw_import.what_if, AS_OF)) == []


PRICE_CHANGES = {
    ("Pet Insurance (Ultimate Pet Partners)", (9655, 13874)),
    ("TV (Sky)", (9800, 10475)),
    ("Council Tax (Exampletown)", (24350, 25980)),
    ("Spotify (PayPal)", (1699, 1799)),
    ("Car Insurance (1stCentral)", (3912, 4127)),
    ("Broadband (Vodafone)", (4900, 2612)),
}


def _amount_changes(session: Session, result: ImportResult) -> set[tuple[str, tuple[int, int]]]:
    changes = diff(resolve(session, result.what_if, AS_OF), resolve(session, result.live, AS_OF))
    return {
        (c.label, c.fields["amount_pence"])
        for c in changes
        if c.change == "modified" and "amount_pence" in c.fields
    }


def test_4_6_price_changes_between_the_sheets_are_listed(
    session: Session, raw_import: ImportResult
) -> None:
    assert _amount_changes(session, raw_import) >= PRICE_CHANGES


def test_4_6_back_to_normal_then_uses_todays_prices(
    session: Session, imported: ImportResult
) -> None:
    """Decision 28: the old prices aren't kept as Back to normal differences."""
    changes = _amount_changes(session, imported)
    assert not changes & PRICE_CHANGES
    assert ("Groceries", (60000, 70000)) in changes  # real differences stay
    pets = resolve(session, imported.what_if, AS_OF).find("Pet Insurance")
    assert pets.amount_pence == 13874
    assert [c["amount_pence"] for c in pets.components] == [6512, 7362]
    assert pets.origin == "base"


def test_4_7_thin_joint_buffer_is_projected_by_day(
    session: Session, imported: ImportResult
) -> None:
    resolved = resolve(session, imported.live, AS_OF)
    budget = to_engine(resolved)
    result = calculate(resolved)
    transfers_in = sum(
        t.effective_pence for t in result.transfers if t.to_account == "joint_billing"
    )
    # Transfers for November are made on payday, after both salaries land in late October.
    paydays = [
        pay_day(PayDayRule("last_working_day"), 2026, 11),
        pay_day(PayDayRule("last_thursday", typical_offset_days=-1), 2026, 11),
    ]
    inflow = max(p.expected for p in paydays)

    exact = project_month(budget, result, "joint_billing", 2026, 11, transfers_in, inflow)
    # Opening with last month's transfers, the lowest point is the thin monthly buffer.
    assert not exact.goes_negative
    assert exact.minimum == transfers_in - result.joint_total
    assert round_pence(exact.minimum) == 7249  # £62.50 + £9.99 now Apple Fitness+ has gone
    assert "Groceries" in exact.undated_lines

    short = project_month(budget, result, "joint_billing", 2026, 11, transfers_in - 10000, inflow)
    assert short.goes_negative
    assert short.minimum_day < inflow


def test_imported_follow_up_notes_are_plain_words(session: Session, imported: ImportResult) -> None:
    notes = set(session.scalars(select(FollowUp.note)))
    assert not any("Pink highlight" in (n or "") or "CANCEL!!" in (n or "") for n in notes)
    assert "Marked to cancel in the old spreadsheet." in notes
