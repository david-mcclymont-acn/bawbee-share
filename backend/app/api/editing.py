"""Changing budgets: what-ifs, overrides, live edits and promotion (PLAN.md §5).

Every route needs a signed-in user and the CSRF header, and every change is audited with
that user.
"""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import SessionDep, UserDep, current_session
from app.api.versions import (
    ConflictOut,
    LineChangeOut,
    VersionOut,
    get_version_or_404,
    today,
    version_out,
)
from app.db import must_get
from app.domain import editing, promotion
from app.domain.models import BudgetLine, BudgetVersion, IncomeSource, Person, Promotion
from app.domain.versions import VersionError

router = APIRouter(prefix="/api", tags=["editing"], dependencies=[Depends(current_session)])


class LineFields(BaseModel):
    """Any subset of a line's fields. Sending null clears a field."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=200)
    provider: str | None = Field(None, max_length=200)
    kind: Literal["bill", "allocation", "transfer"] | None = None
    account: str | None = None
    to_account: str | None = None
    category: str | None = None
    owner: str | None = None
    amount_pence: int | None = Field(None, ge=0)
    frequency: Literal["monthly", "annual", "quarterly", "weekly"] | None = None
    day_of_month: int | None = Field(None, ge=1, le=31)
    renewal_date: date | None = None
    transfer_rule: dict[str, Any] | None = None
    actual_override_pence: int | None = Field(None, ge=0)
    components: list[dict[str, Any]] | None = None
    agreement: dict[str, Any] | None = None
    tags: list[str] | None = None
    flags: list[str] | None = None
    notes: str | None = None

    def values(self) -> dict[str, Any]:
        out = self.model_dump(exclude_unset=True)
        if "renewal_date" in out and out["renewal_date"] is not None:
            out["renewal_date"] = out["renewal_date"].isoformat()
        for list_field in ("components", "tags", "flags"):
            if list_field in out and out[list_field] is None:
                out[list_field] = []
        return out


class LineChange(BaseModel):
    changes: LineFields
    # Live edits only: when the change takes effect (default today, UK time).
    effective_from: date | None = None


class NewLine(BaseModel):
    values: LineFields
    effective_from: date | None = None


class NewWhatIf(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_version_id: int
    description: str | None = None
    planned_start: date | None = None


class WhatIfChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    planned_start: date | None = None


class Duplicate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class Rebase(BaseModel):
    base_version_id: int


class SettingsChanges(BaseModel):
    """Null clears a what-if's own value so it inherits from its base again."""

    model_config = ConfigDict(extra="forbid")

    split_basis: Literal["gross", "take_home", "surplus", "fixed_pct"] | None = None
    fixed_shares: dict[str, str] | None = None
    rounding_step_pence: int | None = Field(None, gt=0)
    buffer_pence: int | None = Field(None, ge=0)
    extra_lines_mode: Literal["on_top_of_share", "exclusive"] | None = None


class IncomeChange(BaseModel):
    net_monthly_pence: int | None = Field(None, ge=0)


class PromotionIn(BaseModel):
    effective_date: date
    resolutions: dict[int, Literal["override", "live"]] = {}


class PromotionPreviewOut(BaseModel):
    effective_date: date
    changes: list[LineChangeOut]
    conflicts: list[ConflictOut]
    settings_changes: dict[str, tuple[Any, Any]]
    income_changes: dict[str, tuple[int | None, int | None]]


class PromotionOut(BaseModel):
    id: int
    what_if_id: int
    what_if_name: str
    period_id: int | None
    effective_date: date
    undoable: bool


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    return HTTPException(422, str(exc))


def _line(session: SessionDep, line_id: int) -> BudgetLine:
    line = session.get(BudgetLine, line_id)
    if line is None:
        raise HTTPException(404, "No such line")
    return line


# ---- what-ifs ----------------------------------------------------------------------------


@router.post("/versions", response_model=VersionOut, status_code=201)
def create_what_if(body: NewWhatIf, user: UserDep, session: SessionDep) -> VersionOut:
    base = get_version_or_404(session, body.base_version_id)
    try:
        version = editing.create_what_if(
            session, body.name.strip(), base, user, body.description, body.planned_start
        )
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return version_out(version)


@router.patch("/versions/{version_id}", response_model=VersionOut)
def update_what_if(
    version_id: int, body: WhatIfChanges, user: UserDep, session: SessionDep
) -> VersionOut:
    version = get_version_or_404(session, version_id)
    try:
        editing.update_what_if(session, version, body.model_dump(exclude_unset=True), user)
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return version_out(version)


@router.post("/versions/{version_id}/duplicate", response_model=VersionOut, status_code=201)
def duplicate(version_id: int, body: Duplicate, user: UserDep, session: SessionDep) -> VersionOut:
    try:
        copy = editing.duplicate_what_if(
            session, get_version_or_404(session, version_id), body.name, user
        )
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return version_out(copy)


@router.post("/versions/{version_id}/rebase", response_model=VersionOut)
def rebase(version_id: int, body: Rebase, user: UserDep, session: SessionDep) -> VersionOut:
    version = get_version_or_404(session, version_id)
    try:
        editing.rebase(session, version, get_version_or_404(session, body.base_version_id), user)
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return version_out(version)


@router.post("/versions/{version_id}/archive", response_model=VersionOut)
def archive(version_id: int, user: UserDep, session: SessionDep) -> VersionOut:
    version = get_version_or_404(session, version_id)
    try:
        editing.archive_what_if(session, version, user)
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return version_out(version)


@router.post("/versions/{version_id}/restore", response_model=VersionOut)
def restore(version_id: int, user: UserDep, session: SessionDep) -> VersionOut:
    """Bring back an archived what-if (the undo for archive)."""
    version = get_version_or_404(session, version_id)
    try:
        editing.restore_what_if(session, version, user)
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return version_out(version)


@router.patch("/versions/{version_id}/settings", response_model=VersionOut)
def settings(
    version_id: int, body: SettingsChanges, user: UserDep, session: SessionDep
) -> VersionOut:
    version = get_version_or_404(session, version_id)
    changes = body.model_dump(exclude_unset=True)
    if version.type == "live" and any(v is None for v in changes.values()):
        raise HTTPException(422, "The live budget can't leave a setting blank")
    try:
        editing.set_settings(session, version, changes, user)
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return version_out(version)


@router.put("/versions/{version_id}/incomes/{person}", status_code=204)
def set_income(
    version_id: int, person: str, body: IncomeChange, user: UserDep, session: SessionDep
) -> None:
    version = get_version_or_404(session, version_id)
    if version.type == "period":
        raise HTTPException(422, "Periods are read-only")
    source = session.scalars(
        select(IncomeSource)
        .join(Person, Person.id == IncomeSource.person_id)
        .where(Person.key == person)
        .order_by(IncomeSource.as_of_date.desc(), IncomeSource.id.desc())
    ).first()
    if source is None:
        raise HTTPException(404, f"No income recorded for {person}")
    editing.set_net_income(session, version, source, body.net_monthly_pence, user)
    session.commit()


# ---- lines: live edits are dated; what-if edits become overrides ------------------------


@router.patch("/versions/{version_id}/lines/{line_id}", status_code=204)
def change_line(
    version_id: int, line_id: int, body: LineChange, user: UserDep, session: SessionDep
) -> None:
    version = get_version_or_404(session, version_id)
    values = body.changes.values()
    try:
        if version.type == "live":
            editing.update_live_line(
                session, _line(session, line_id), values, body.effective_from or today(), user
            )
        elif version.type == "what_if":
            editing.override_line(session, version, line_id, values, today(), user)
        else:
            raise VersionError("Periods are read-only")
    except (VersionError, KeyError) as exc:
        raise _fail(exc) from exc
    session.commit()


@router.post("/versions/{version_id}/lines", status_code=201)
def add_line(version_id: int, body: NewLine, user: UserDep, session: SessionDep) -> dict[str, int]:
    version = get_version_or_404(session, version_id)
    values = body.values.values()
    for required in ("name", "kind", "account"):
        if not values.get(required):
            raise HTTPException(422, f"A new line needs a {required}")
    try:
        if version.type == "live":
            line = editing.add_live_line(session, values, body.effective_from or today(), user)
            line_id = line.id
        elif version.type == "what_if":
            line_id = editing.add_line(session, version, values, user).line_id
        else:
            raise VersionError("Periods are read-only")
    except (VersionError, KeyError) as exc:
        raise _fail(exc) from exc
    session.commit()
    return {"line_id": line_id}


@router.delete("/versions/{version_id}/lines/{line_id}", status_code=204)
def remove_line(
    version_id: int,
    line_id: int,
    user: UserDep,
    session: SessionDep,
    effective_from: date | None = None,
) -> None:
    version = get_version_or_404(session, version_id)
    try:
        if version.type == "live":
            editing.end_live_line(session, _line(session, line_id), effective_from or today(), user)
        elif version.type == "what_if":
            existing = next((o for o in version.overrides if o.line_id == line_id), None)
            if existing is not None and existing.action == "add":
                editing.revert_override(session, existing, user)
            else:
                editing.remove_line(session, version, line_id, today(), user)
        else:
            raise VersionError("Periods are read-only")
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()


@router.post("/versions/{version_id}/lines/{line_id}/revert", status_code=204)
def revert_line(version_id: int, line_id: int, user: UserDep, session: SessionDep) -> None:
    """Drop this what-if's change to a line so it follows its base again."""
    version = get_version_or_404(session, version_id)
    override = next((o for o in version.overrides if o.line_id == line_id), None)
    if override is None:
        raise HTTPException(404, "This line isn't changed in this what-if")
    try:
        editing.revert_override(session, override, user)
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()


# ---- promotion ---------------------------------------------------------------------------


def _promotion_out(session: SessionDep, p: Promotion) -> PromotionOut:
    what_if = must_get(session, BudgetVersion, p.what_if_id)
    return PromotionOut(
        id=p.id,
        what_if_id=p.what_if_id,
        what_if_name=what_if.name,
        period_id=p.period_id,
        effective_date=p.effective_date,
        undoable=promotion.promotion_is_undoable(session, p),
    )


@router.post("/versions/{version_id}/promotion-preview", response_model=PromotionPreviewOut)
def promotion_preview(
    version_id: int, body: PromotionIn, session: SessionDep
) -> PromotionPreviewOut:
    version = get_version_or_404(session, version_id)
    p = promotion.preview(session, version, body.effective_date, dict(body.resolutions))
    return PromotionPreviewOut(
        effective_date=p.effective_date,
        changes=[LineChangeOut(**c.__dict__) for c in p.changes],
        conflicts=[ConflictOut(**c.__dict__) for c in p.conflicts],
        settings_changes=p.settings_changes,
        income_changes=p.income_changes,
    )


@router.post("/versions/{version_id}/promote", response_model=PromotionOut)
def promote(version_id: int, body: PromotionIn, user: UserDep, session: SessionDep) -> PromotionOut:
    version = get_version_or_404(session, version_id)
    try:
        p = promotion.promote(session, version, body.effective_date, user, dict(body.resolutions))
    except promotion.PromotionConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
    return _promotion_out(session, p)


@router.get("/promotions/latest", response_model=PromotionOut | None)
def latest_promotion(session: SessionDep) -> PromotionOut | None:
    p = promotion.latest_promotion(session)
    return _promotion_out(session, p) if p else None


@router.post("/promotions/{promotion_id}/undo", status_code=204)
def undo(promotion_id: int, user: UserDep, session: SessionDep) -> None:
    p = session.get(Promotion, promotion_id)
    if p is None:
        raise HTTPException(404, "No such promotion")
    try:
        promotion.undo_promotion(session, p, user)
    except VersionError as exc:
        raise _fail(exc) from exc
    session.commit()
