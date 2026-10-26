"""Promotion to live, frozen periods and undo (PLAN.md §5.4)."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import must_get
from app.domain import editing
from app.domain.models import BudgetLine, BudgetVersion, Promotion
from app.domain.promotion import PromotionConflictError, preview, promote, undo_promotion
from app.domain.versions import ResolvedBudget, VersionError, calculate, live_version, resolve
from app.importers.seed import ImportResult
from tests.conftest import AS_OF

EFFECTIVE = date(2026, 10, 30)  # Person 1's payday in October 2026
LATER = EFFECTIVE + timedelta(days=10)


def _line(
    session: Session, resolved: ResolvedBudget, name: str, provider: str | None = None
) -> BudgetLine:
    target = next(
        ln
        for ln in resolved.lines
        if ln.name == name and (provider is None or ln.provider == provider)
    )
    return must_get(session, BudgetLine, target.line_id)


@pytest.fixture
def updated_live(session: Session, imported: ImportResult) -> dict[str, BudgetLine]:
    """Live changes made after Back to normal was imported. Back to normal overrides Groceries
    (£600), but follows live for Octopus and, since decision 28, for Sky."""
    live = resolve(session, imported.live, AS_OF)
    lines = {
        "groceries": _line(session, live, "Groceries"),
        "octopus": _line(session, live, "Gas/Electricity"),
        "sky": _line(session, live, "TV", "Sky"),
    }
    editing.update_live_line(session, lines["groceries"], {"amount_pence": 75000}, AS_OF, None)
    editing.update_live_line(session, lines["octopus"], {"amount_pence": 21000}, AS_OF, None)
    editing.update_live_line(session, lines["sky"], {"amount_pence": 10850}, AS_OF, None)
    session.commit()
    return lines


def _override_id(imported: ImportResult, line: BudgetLine) -> int:
    return next(o.id for o in imported.what_if.overrides if o.line_id == line.id)


def test_preview_shows_changes_and_conflicts(
    session: Session, imported: ImportResult, updated_live: dict[str, BudgetLine]
) -> None:
    p = preview(session, imported.what_if, EFFECTIVE)
    by_label = {c.label: c for c in p.changes}
    assert by_label["Petrol"].fields["amount_pence"] == (5000, 25000)
    assert by_label["HayU"].change == "added"
    assert by_label["Claude AI"].change == "removed"
    # Back to normal follows live for these, so promoting it doesn't change them.
    assert "Gas/Electricity (Octopus)" not in by_label
    assert "TV (Sky)" not in by_label
    assert [c.label for c in p.conflicts] == ["Groceries"]
    assert p.settings_changes == {"split_basis": ("take_home", "gross")}
    assert p.income_changes == {"person_1": (421580, 386245), "person_2": (152375, 298430)}


def test_promotion_needs_every_conflict_resolved(
    session: Session, imported: ImportResult, updated_live: dict[str, BudgetLine]
) -> None:
    with pytest.raises(PromotionConflictError) as err:
        promote(session, imported.what_if, EFFECTIVE, None)
    assert [c.label for c in err.value.unresolved] == ["Groceries"]


def test_promotion_applies_overrides_onto_updated_live_and_freezes_a_period(
    session: Session, imported: ImportResult, updated_live: dict[str, BudgetLine]
) -> None:
    groceries, octopus, sky = (updated_live[k] for k in ("groceries", "octopus", "sky"))
    before = resolve(session, imported.live, EFFECTIVE - timedelta(days=1))

    promotion = promote(
        session,
        imported.what_if,
        EFFECTIVE,
        None,
        resolutions={_override_id(imported, groceries): "live"},
    )
    session.commit()

    live = live_version(session)
    assert live.name == "Back to normal"
    assert live.period_start == EFFECTIVE
    assert imported.what_if.status == "promoted"

    now = resolve(session, live, EFFECTIVE)
    assert now.line(octopus.id).amount_pence == 21000  # live's update survives
    assert now.line(sky.id).amount_pence == 10850  # today's price (decision 28)
    assert now.line(groceries.id).amount_pence == 75000  # conflict resolved as "live"
    assert now.find("Petrol").amount_pence == 25000  # Back to normal's override applied
    assert now.find("HayU").amount_pence == 499  # added by Back to normal
    assert all(ln.name != "Claude AI" for ln in now.lines)  # removed by Back to normal
    assert now.settings["split_basis"] == "gross"
    assert {i.person: i.net_monthly_pence for i in now.incomes} == {
        "person_1": 386245,
        "person_2": 298430,
    }
    # The day before the effective date, live is still the Maternity budget.
    assert resolve(session, live, EFFECTIVE - timedelta(days=1)).find("Petrol").amount_pence == 5000

    period = session.get(BudgetVersion, promotion.period_id)
    assert period is not None
    assert period.type == "period"
    assert (period.name, period.period_start, period.period_end) == (
        "Maternity",
        AS_OF,
        EFFECTIVE - timedelta(days=1),
    )
    frozen = resolve(session, period, LATER)
    assert [ln.values() for ln in frozen.lines] == [ln.values() for ln in before.lines]
    assert calculate(frozen).household_surplus == calculate(before).household_surplus

    # Later live changes never reach the frozen period.
    editing.update_live_line(session, octopus, {"amount_pence": 25000}, LATER, None)
    assert resolve(session, period, LATER).line(octopus.id).amount_pence == 21000


def test_promotion_can_be_undone(
    session: Session, imported: ImportResult, updated_live: dict[str, BudgetLine]
) -> None:
    groceries = updated_live["groceries"]
    child = editing.create_what_if(session, "Second child", imported.what_if, None)
    before = {
        d: resolve(session, imported.live, d) for d in (EFFECTIVE - timedelta(days=1), EFFECTIVE)
    }
    settings_before = dict(imported.live.settings_json)

    promotion = promote(
        session,
        imported.what_if,
        EFFECTIVE,
        None,
        resolutions={_override_id(imported, groceries): "override"},
    )
    session.commit()
    now = resolve(session, live_version(session), EFFECTIVE)
    assert now.line(groceries.id).amount_pence == 60000  # conflict resolved as "override"
    assert child.base_version_id == live_version(session).id  # re-pointed at live

    undo_promotion(session, promotion, None)
    session.commit()

    live = live_version(session)
    assert (live.name, live.period_start, live.settings_json) == (
        "Maternity",
        AS_OF,
        settings_before,
    )
    for d, resolved in before.items():
        after = resolve(session, live, d)
        assert [ln.values() for ln in after.lines] == [ln.values() for ln in resolved.lines]
        assert after.incomes == resolved.incomes
    assert imported.what_if.status == "draft"
    assert child.base_version_id == imported.what_if.id
    assert session.scalars(select(BudgetVersion).where(BudgetVersion.type == "period")).all() == []
    assert must_get(session, Promotion, promotion.id).undone_at is not None
    with pytest.raises(VersionError, match="already been undone"):
        undo_promotion(session, promotion, None)

    # And it can be promoted again afterwards.
    promote(
        session,
        imported.what_if,
        EFFECTIVE,
        None,
        resolutions={_override_id(imported, groceries): "live"},
    )
