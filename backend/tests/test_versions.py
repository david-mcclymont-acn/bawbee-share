"""Version resolution, inheritance and overrides (PLAN.md §5.2, §5.3)."""

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.domain import editing
from app.domain.models import BudgetLine
from app.domain.versions import VersionError, conflicts, redundant_overrides, resolve
from app.importers.seed import ImportResult
from tests.conftest import AS_OF

NEXT_WEEK = AS_OF + timedelta(days=7)


@pytest.fixture
def sky(session: Session, imported: ImportResult) -> BudgetLine:
    """Two lines are called TV; Sky is the one with provider "Sky"."""
    resolved = resolve(session, imported.live, AS_OF)
    sky = next(ln for ln in resolved.lines if ln.name == "TV" and ln.provider == "Sky")
    line = session.get(BudgetLine, sky.line_id)
    assert line is not None
    return line


def test_changing_sky_on_live_reaches_what_ifs_that_dont_override_it(
    session: Session, imported: ImportResult, sky: BudgetLine
) -> None:
    back_to_work = editing.create_what_if(session, "Back to work", imported.live, None)
    editing.update_live_line(session, sky, {"amount_pence": 13500}, NEXT_WEEK, None)

    assert resolve(session, back_to_work, NEXT_WEEK).line(sky.id).amount_pence == 13500
    # Back to normal follows live for Sky too, since it uses today's prices (decision 28).
    assert resolve(session, imported.what_if, NEXT_WEEK).line(sky.id).amount_pence == 13500
    # Before the change takes effect, everyone still sees the old price.
    assert resolve(session, back_to_work, AS_OF).line(sky.id).amount_pence == 10475


def test_changing_sky_on_live_leaves_an_override_alone_and_flags_it(
    session: Session, imported: ImportResult, sky: BudgetLine
) -> None:
    old_deal = editing.create_what_if(session, "Old Sky deal", imported.live, None)
    editing.override_line(session, old_deal, sky.id, {"amount_pence": 9800}, AS_OF, None)
    assert conflicts(session, old_deal, AS_OF) == []

    editing.update_live_line(session, sky, {"amount_pence": 13500}, NEXT_WEEK, None)

    assert resolve(session, old_deal, NEXT_WEEK).line(sky.id).amount_pence == 9800
    [conflict] = conflicts(session, old_deal, NEXT_WEEK)
    assert conflict.line_id == sky.id
    assert conflict.based_on["amount_pence"] == 10475
    assert conflict.live_now["amount_pence"] == 13500
    assert conflict.override == {"amount_pence": 9800}


def test_override_matching_the_new_live_value_is_redundant(
    session: Session, imported: ImportResult, sky: BudgetLine
) -> None:
    what_if = editing.create_what_if(session, "Cheaper Sky", imported.live, None)
    ov = editing.override_line(session, what_if, sky.id, {"amount_pence": 10850}, AS_OF, None)
    assert redundant_overrides(session, what_if, AS_OF) == []

    editing.update_live_line(session, sky, {"amount_pence": 10850}, AS_OF, None)
    assert redundant_overrides(session, what_if, AS_OF) == [ov]


def test_overrides_record_only_real_differences(
    session: Session, imported: ImportResult, sky: BudgetLine
) -> None:
    what_if = editing.create_what_if(session, "Same Sky", imported.live, None)
    ov = editing.override_line(
        session, what_if, sky.id, {"amount_pence": 10475, "day_of_month": 3}, AS_OF, None
    )
    assert ov.fields_json == {"day_of_month": 3}


def test_what_ifs_chain_and_revert_to_base(
    session: Session, imported: ImportResult, sky: BudgetLine
) -> None:
    back_to_work = editing.create_what_if(session, "Back to work 3 days", imported.live, None)
    editing.override_line(session, back_to_work, sky.id, {"amount_pence": 9900}, AS_OF, None)
    second_child = editing.create_what_if(session, "Second child", back_to_work, None)
    groceries = resolve(session, imported.live, AS_OF).find("Groceries")
    editing.override_line(
        session, second_child, groceries.line_id, {"amount_pence": 90000}, AS_OF, None
    )

    resolved = resolve(session, second_child, AS_OF)
    # Inherited from its base what-if, and its own change on top.
    assert resolved.line(sky.id).amount_pence == 9900
    assert resolved.line(sky.id).origin == "base"
    assert resolved.line(groceries.line_id).amount_pence == 90000
    assert resolved.line(groceries.line_id).origin == "modified"

    [ov] = [o for o in second_child.overrides if o.line_id == groceries.line_id]
    editing.revert_override(session, ov, None)
    assert resolve(session, second_child, AS_OF).line(groceries.line_id).amount_pence == 70000


def test_add_and_remove_lines_in_a_what_if(session: Session, imported: ImportResult) -> None:
    what_if = editing.create_what_if(session, "Nursery", imported.live, None)
    ov = editing.add_line(
        session,
        what_if,
        {
            "name": "Nursery",
            "provider": "Little Acorns",
            "kind": "bill",
            "account": "joint_billing",
            "amount_pence": 95000,
            "day_of_month": 1,
        },
        None,
    )
    petrol = resolve(session, imported.live, AS_OF).find("Petrol")
    editing.remove_line(session, what_if, petrol.line_id, AS_OF, None)

    resolved = resolve(session, what_if, AS_OF)
    assert resolved.line(ov.line_id).origin == "added"
    assert resolved.line(ov.line_id).amount_pence == 95000
    assert all(ln.line_id != petrol.line_id for ln in resolved.lines)
    # Live is untouched.
    live = resolve(session, imported.live, AS_OF)
    assert all(ln.line_id != ov.line_id for ln in live.lines)
    assert live.line(petrol.line_id).amount_pence == 5000


def test_settings_inherit_unless_set(session: Session, imported: ImportResult) -> None:
    child = editing.create_what_if(session, "Child of Back to normal", imported.what_if, None)
    assert resolve(session, child, AS_OF).settings["split_basis"] == "gross"
    editing.set_settings(
        session,
        child,
        {"split_basis": "fixed_pct", "fixed_shares": {"person_1": "0.6", "person_2": "0.4"}},
        None,
    )
    assert resolve(session, child, AS_OF).settings["split_basis"] == "fixed_pct"
    assert resolve(session, imported.what_if, AS_OF).settings["split_basis"] == "gross"
    editing.set_settings(session, child, {"split_basis": None, "fixed_shares": None}, None)
    assert resolve(session, child, AS_OF).settings["split_basis"] == "gross"


def test_periods_cant_be_a_what_if_base(session: Session, imported: ImportResult) -> None:
    from app.domain.models import BudgetVersion

    period = BudgetVersion(name="Old", type="period", status="archived", settings_json={})
    session.add(period)
    session.flush()
    with pytest.raises(VersionError):
        editing.create_what_if(session, "Nope", period, None)
