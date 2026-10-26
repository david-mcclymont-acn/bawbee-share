"""Promoting a what-if to live (PLAN.md §5.4), and undoing it.

Promotion takes the *current* live budget and applies only what the what-if changed. Lines
the what-if never touched keep their latest live values. The old live budget is frozen as a
read-only Period.

Line identity fields (name, account, ...) aren't dated, so they change as soon as the
promotion is committed; amounts change from the effective date. The Period keeps the old
values either way.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import must_get, utcnow
from app.domain import audit
from app.domain.editing import apply_line_level, end_live_line, update_live_line, version_kwargs
from app.domain.models import (
    BudgetLine,
    BudgetLineVersion,
    BudgetVersion,
    IncomeSource,
    IncomeVersion,
    PaydayRun,
    Promotion,
    User,
)
from app.domain.versions import (
    LINE_LEVEL_FIELDS,
    VERSION_LEVEL_FIELDS,
    Conflict,
    LineChange,
    Lookups,
    VersionError,
    conflicts,
    current_line_version,
    diff,
    live_version,
    resolve,
    to_snapshot,
)

Resolution = Literal["override", "live"]


class PromotionConflictError(VersionError):
    def __init__(self, unresolved: list[Conflict]) -> None:
        self.unresolved = unresolved
        names = ", ".join(c.label for c in unresolved)
        super().__init__(f"Choose override or live for each changed line first: {names}")


@dataclass(frozen=True)
class PromotionPreview:
    what_if_id: int
    live_id: int
    effective_date: date
    changes: list[LineChange]
    conflicts: list[Conflict]
    settings_changes: dict[str, tuple[Any, Any]]
    income_changes: dict[str, tuple[int | None, int | None]]


def _target(
    session: Session,
    what_if: BudgetVersion,
    live: BudgetVersion,
    effective_date: date,
    resolutions: dict[int, Resolution],
) -> tuple[Any, Any, list[Conflict]]:
    current = resolve(session, live, effective_date)
    target = resolve(session, what_if, effective_date)
    found = conflicts(session, what_if, effective_date)
    # Where the user chose "live", the line keeps its current live values.
    for c in found:
        if resolutions.get(c.override_id) == "live":
            live_line = current.by_id().get(c.line_id)
            target.lines = [ln for ln in target.lines if ln.line_id != c.line_id]
            if live_line is not None:
                target.lines.append(live_line)
    return current, target, found


def preview(
    session: Session,
    what_if: BudgetVersion,
    effective_date: date,
    resolutions: dict[int, Resolution] | None = None,
) -> PromotionPreview:
    live = live_version(session)
    current, target, found = _target(session, what_if, live, effective_date, resolutions or {})
    settings = {
        k: (current.settings.get(k), v)
        for k, v in target.settings.items()
        if current.settings.get(k) != v
    }
    cur_inc = {i.person: i.net_monthly_pence for i in current.incomes}
    incomes = {
        i.person: (cur_inc.get(i.person), i.net_monthly_pence)
        for i in target.incomes
        if cur_inc.get(i.person) != i.net_monthly_pence
    }
    return PromotionPreview(
        what_if.id, live.id, effective_date, diff(current, target), found, settings, incomes
    )


def promote(
    session: Session,
    what_if: BudgetVersion,
    effective_date: date,
    actor: User | None,
    resolutions: dict[int, Resolution] | None = None,
) -> Promotion:
    if what_if.type != "what_if" or what_if.status != "draft":
        raise VersionError(f"{what_if.name!r} can't be promoted")
    live = live_version(session)
    if live.period_start is not None and effective_date <= live.period_start:
        raise VersionError("The effective date must be after the live budget started")
    resolutions = resolutions or {}
    current, target, found = _target(session, what_if, live, effective_date, resolutions)
    unresolved = [c for c in found if c.override_id not in resolutions]
    if unresolved:
        raise PromotionConflictError(unresolved)

    lookups = Lookups.load(session)
    period_end = effective_date - timedelta(days=1)
    snapshot = resolve(session, live, period_end)
    undo: dict[str, Any] = {
        "created_versions": [],
        "closed_versions": [],
        "line_fields": {},
        "activated_lines": [],
        "incomes": [],
    }

    # 1. Write the what-if's changes into live, dated from the effective date.
    for change in diff(current, target):
        line = must_get(session, BudgetLine, change.line_id)
        new_values = {k: after for k, (_, after) in change.fields.items()}
        cur = current_line_version(session, line.id, effective_date)
        if change.change == "modified":
            undo["line_fields"][str(line.id)] = {
                k: before for k, (before, _) in change.fields.items() if k in LINE_LEVEL_FIELDS
            }
            old_to = cur.effective_to.isoformat() if cur and cur.effective_to else None
            created = update_live_line(session, line, new_values, effective_date, actor)
            if created is not None and cur is not None:
                undo["closed_versions"].append([cur.id, old_to])
                undo["created_versions"].append(created.id)
        elif change.change == "removed":
            if cur is None:
                raise VersionError(f"{line.name!r} isn't live on {effective_date}")
            undo["closed_versions"].append(
                [cur.id, cur.effective_to.isoformat() if cur.effective_to else None]
            )
            end_live_line(session, line, effective_date, actor)
        else:
            undo["activated_lines"].append([line.id, line.scope_version_id])
            line.scope_version_id = None
            apply_line_level(
                line, {k: v for k, v in new_values.items() if k in LINE_LEVEL_FIELDS}, lookups
            )
            created_version = BudgetLineVersion(
                line_id=line.id,
                effective_from=effective_date,
                created_by=actor.id if actor else None,
                **version_kwargs({k: new_values[k] for k in VERSION_LEVEL_FIELDS}),
            )
            session.add(created_version)
            session.flush()
            undo["created_versions"].append(created_version.id)

    # 2. Settings and incomes.
    undo["live"] = {
        "name": live.name,
        "description": live.description,
        "period_start": live.period_start.isoformat() if live.period_start else None,
        "settings_json": dict(live.settings_json or {}),
    }
    live.settings_json = dict(target.settings)
    cur_inc = {i.person: i.net_monthly_pence for i in current.incomes}
    for inc in target.incomes:
        if cur_inc.get(inc.person) == inc.net_monthly_pence:
            continue
        source = session.scalars(
            select(IncomeSource)
            .where(IncomeSource.person_id == lookups.person_id[inc.person])
            .order_by(IncomeSource.as_of_date.desc(), IncomeSource.id.desc())
        ).first()
        if source is None:
            raise VersionError(f"No income recorded for {inc.person}")
        row = session.scalars(
            select(IncomeVersion).where(
                IncomeVersion.income_source_id == source.id, IncomeVersion.version_id == live.id
            )
        ).first()
        if row is None:
            row = IncomeVersion(income_source_id=source.id, version_id=live.id)
            session.add(row)
            session.flush()
            undo["incomes"].append([row.id, None, True])
        else:
            undo["incomes"].append([row.id, row.net_monthly_override_pence, False])
        row.net_monthly_override_pence = inc.net_monthly_pence

    # 3. Freeze the old live budget as a Period, and hand over to the what-if.
    period = BudgetVersion(
        name=live.name,
        description=live.description,
        type="period",
        period_start=live.period_start,
        period_end=period_end,
        status="archived",
        settings_json={},
        snapshot_json=to_snapshot(snapshot),
    )
    session.add(period)
    live.name = what_if.name
    live.description = what_if.description
    live.period_start = effective_date
    what_if.status = "promoted"
    what_if.promoted_at = utcnow()

    # 4. What-ifs built on this one now build on live (which now contains it).
    children = session.scalars(
        select(BudgetVersion).where(BudgetVersion.base_version_id == what_if.id)
    ).all()
    for child in children:
        child.base_version_id = live.id
    undo["repointed"] = [c.id for c in children]
    session.flush()

    promotion = Promotion(
        what_if_id=what_if.id,
        period_id=period.id,
        effective_date=effective_date,
        undo_json=undo,
        created_by=actor.id if actor else None,
    )
    session.add(promotion)
    session.flush()
    audit.record(
        session,
        actor,
        "promotion",
        promotion.id,
        "promote",
        None,
        {
            "what_if_id": what_if.id,
            "period_id": period.id,
            "effective_date": effective_date.isoformat(),
            "resolutions": {str(k): v for k, v in resolutions.items()},
        },
    )
    return promotion


def promotion_is_undoable(session: Session, promotion: Promotion) -> bool:
    if promotion.undone_at is not None:
        return False
    later_run = session.scalars(
        select(PaydayRun).where(PaydayRun.created_at >= promotion.created_at)
    ).first()
    return later_run is None


def latest_promotion(session: Session) -> Promotion | None:
    return session.scalars(
        select(Promotion).where(Promotion.undone_at.is_(None)).order_by(Promotion.id.desc())
    ).first()


def undo_promotion(session: Session, promotion: Promotion, actor: User | None) -> None:
    """Put everything back as it was before the promotion.

    Allowed until the first payday run under the new live budget (PLAN.md §5.4): after
    that, real money has moved on its numbers.
    """
    if promotion.undone_at is not None:
        raise VersionError("This promotion has already been undone")
    if not promotion_is_undoable(session, promotion):
        raise VersionError(
            "A payday has been run on this budget since it was promoted, so it can't be undone"
        )
    lookups = Lookups.load(session)
    undo = promotion.undo_json
    live = live_version(session)
    what_if = must_get(session, BudgetVersion, promotion.what_if_id)

    for version_id in undo["created_versions"]:
        created = session.get(BudgetLineVersion, version_id)
        if created is not None:
            session.delete(created)
    session.flush()
    for version_id, old_to in undo["closed_versions"]:
        closed = must_get(session, BudgetLineVersion, version_id)
        closed.effective_to = date.fromisoformat(old_to) if old_to else None
    for line_id, before in undo["line_fields"].items():
        line = must_get(session, BudgetLine, int(line_id))
        apply_line_level(line, before, lookups)
    for line_id, scope_version_id in undo["activated_lines"]:
        line = must_get(session, BudgetLine, line_id)
        line.scope_version_id = scope_version_id
    for row_id, before_net, created_row in undo["incomes"]:
        row = must_get(session, IncomeVersion, row_id)
        if created_row:
            session.delete(row)
        else:
            row.net_monthly_override_pence = before_net

    previous = undo["live"]
    live.name = previous["name"]
    live.description = previous["description"]
    live.period_start = (
        date.fromisoformat(previous["period_start"]) if previous["period_start"] else None
    )
    live.settings_json = previous["settings_json"]
    what_if.status = "draft"
    what_if.promoted_at = None
    for child_id in undo["repointed"]:
        child = session.get(BudgetVersion, child_id)
        if child is not None:
            child.base_version_id = what_if.id

    period = session.get(BudgetVersion, promotion.period_id) if promotion.period_id else None
    promotion.period_id = None
    promotion.undone_at = utcnow()
    promotion.undone_by = actor.id if actor else None
    session.flush()
    if period is not None:
        session.delete(period)
    session.flush()
    audit.record(session, actor, "promotion", promotion.id, "undo", None, None)
