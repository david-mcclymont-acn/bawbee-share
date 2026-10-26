"""Budget versions API: reading versions. Changes live in app/api/editing.py."""

from datetime import date, datetime
from fractions import Fraction
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, current_session
from app.domain.models import BudgetVersion
from app.domain.projection import NoSuchAccount, month_projection
from app.domain.versions import (
    ResolvedLine,
    VersionError,
    calculate,
    conflicts,
    diff,
    redundant_overrides,
    resolve,
)
from app.engine.money import round_pence

router = APIRouter(
    prefix="/api/versions", tags=["versions"], dependencies=[Depends(current_session)]
)
AsOf = Annotated[date | None, Query(description="Defaults to today (UK time)")]


def today() -> date:
    return datetime.now(ZoneInfo("Europe/London")).date()


class VersionOut(BaseModel):
    id: int
    name: str
    description: str | None
    type: str
    status: str
    base_version_id: int | None
    planned_start: date | None
    period_start: date | None
    period_end: date | None


class ShareOut(BaseModel):
    exact: str  # e.g. "227300/312053"
    percent: str  # e.g. "72.8394", for display only


class TransferOut(BaseModel):
    line_id: int
    name: str
    from_account: str
    to_account: str
    rule: str
    suggested_pence: int
    actual_pence: int | None
    effective_pence: int
    difference_pence: int


class AccountOut(BaseModel):
    key: str
    name: str
    role: str
    owner: str | None
    inflow_pence: int
    outflow_pence: int


class SurplusesOut(BaseModel):
    personal: dict[str, int]
    joint: int
    household: int


class WarningOut(BaseModel):
    code: str
    message: str
    subject: str | None


class SummaryOut(BaseModel):
    version: VersionOut
    as_of: date
    split_basis: str
    # The version's resolved settings: split basis, rounding, buffer, ...
    settings: dict[str, Any]
    joint_total_pence: int
    shares: dict[str, ShareOut]
    transfers: list[TransferOut]
    accounts: list[AccountOut]
    surpluses: SurplusesOut
    # The suggested joint transfer for each person under every split basis (§5.8).
    split_options: dict[str, dict[str, int]]
    warnings: list[WarningOut]


class LineOut(BaseModel):
    line_id: int
    origin: str
    override_id: int | None
    monthly_pence: int
    values: dict[str, Any]


class LineChangeOut(BaseModel):
    line_id: int
    label: str
    account: str
    change: str
    fields: dict[str, tuple[Any, Any]]


class ConflictOut(BaseModel):
    override_id: int
    line_id: int
    label: str
    action: str
    based_on: dict[str, Any]
    live_now: dict[str, Any]
    override: dict[str, Any]


class DiffOut(BaseModel):
    version: VersionOut
    base_version_id: int | None
    as_of: date
    changes: list[LineChangeOut]
    conflicts: list[ConflictOut]
    redundant_override_ids: list[int]


def get_version_or_404(session: Session, version_id: int) -> BudgetVersion:
    version = session.get(BudgetVersion, version_id)
    if version is None:
        raise HTTPException(404, "No such budget version")
    return version


def version_out(v: BudgetVersion) -> VersionOut:
    return VersionOut.model_validate(v, from_attributes=True)


def _percent(share: Fraction) -> str:
    return f"{round_pence(share * 1_000_000) / 10_000:.4f}"


@router.get("", response_model=list[VersionOut])
def list_versions(session: SessionDep) -> list[VersionOut]:
    versions = session.scalars(select(BudgetVersion).order_by(BudgetVersion.id)).all()
    return [version_out(v) for v in versions]


@router.get("/{version_id}", response_model=VersionOut)
def get_version(version_id: int, session: SessionDep) -> VersionOut:
    return version_out(get_version_or_404(session, version_id))


@router.get("/{version_id}/summary", response_model=SummaryOut)
def summary(version_id: int, session: SessionDep, as_of: AsOf = None) -> SummaryOut:
    version = get_version_or_404(session, version_id)
    when = as_of or today()
    try:
        resolved = resolve(session, version, when)
        result = calculate(resolved)
    except VersionError as exc:
        raise HTTPException(422, str(exc)) from exc
    accounts = {a.key: a for a in resolved.accounts}
    return SummaryOut(
        version=version_out(version),
        as_of=resolved.as_of,
        split_basis=result.split_basis,
        settings=resolved.settings,
        joint_total_pence=round_pence(result.joint_total),
        shares={
            p: ShareOut(exact=f"{s.numerator}/{s.denominator}", percent=_percent(s))
            for p, s in result.shares.items()
        },
        transfers=[
            TransferOut(
                line_id=int(t.line_key.removeprefix("line:")),
                name=t.name,
                from_account=t.from_account,
                to_account=t.to_account,
                rule=t.rule,
                suggested_pence=t.suggested_pence,
                actual_pence=t.actual_pence,
                effective_pence=t.effective_pence,
                difference_pence=t.difference_pence,
            )
            for t in result.transfers
        ],
        accounts=[
            AccountOut(
                key=key,
                name=accounts[key].name,
                role=accounts[key].role,
                owner=accounts[key].owner,
                inflow_pence=round_pence(flow.inflow),
                outflow_pence=round_pence(flow.outflow),
            )
            for key, flow in result.flows.items()
        ],
        surpluses=SurplusesOut(
            personal={p: round_pence(v) for p, v in result.personal_surplus.items()},
            joint=round_pence(result.joint_surplus),
            household=round_pence(result.household_surplus),
        ),
        split_options={k: dict(v) for k, v in result.split_options.items()},
        warnings=[
            WarningOut(code=w.code, message=w.message, subject=w.subject) for w in result.warnings
        ],
    )


def _line_out(ln: ResolvedLine) -> LineOut:
    return LineOut(
        line_id=ln.line_id,
        origin=ln.origin,
        override_id=ln.override_id,
        monthly_pence=round_pence(ln.monthly()),
        values=ln.values(),
    )


@router.get("/{version_id}/lines", response_model=list[LineOut])
def lines(version_id: int, session: SessionDep, as_of: AsOf = None) -> list[LineOut]:
    resolved = resolve(session, get_version_or_404(session, version_id), as_of or today())
    return [_line_out(ln) for ln in resolved.lines]


@router.get("/{version_id}/diff", response_model=DiffOut)
def version_diff(version_id: int, session: SessionDep, as_of: AsOf = None) -> DiffOut:
    """What this what-if changes compared with its base, plus conflicts and redundant
    overrides (PLAN.md §5.3, §5.4)."""
    version = get_version_or_404(session, version_id)
    if version.type != "what_if" or version.base is None:
        raise HTTPException(422, "Only what-ifs have a diff against a base")
    when = as_of or today()
    changes = diff(resolve(session, version.base, when), resolve(session, version, when))
    return DiffOut(
        version=version_out(version),
        base_version_id=version.base_version_id,
        as_of=when,
        changes=[LineChangeOut(**c.__dict__) for c in changes],
        conflicts=[ConflictOut(**c.__dict__) for c in conflicts(session, version, when)],
        redundant_override_ids=[o.id for o in redundant_overrides(session, version, when)],
    )


class MovementOut(BaseModel):
    name: str
    pence: int


class DayOut(BaseModel):
    day: date
    closing_pence: int
    movements: list[MovementOut]


class ProjectionOut(BaseModel):
    account: str
    payday_month: date
    bill_month: date
    opening_pence: int
    minimum_pence: int
    minimum_day: date
    goes_negative: bool
    undated_lines: list[str]
    days: list[DayOut]


@router.get("/{version_id}/projection", response_model=ProjectionOut)
def projection(
    version_id: int,
    session: SessionDep,
    year: int,
    month: int,
    account: str | None = None,
) -> ProjectionOut:
    """The joint account through the month a payday funds (PLAN.md §4.7).

    Until bank sync (Phase 4) it opens with exactly what that payday transfers in, so the
    lowest point is the month's real buffer.
    """
    version = get_version_or_404(session, version_id)
    try:
        projected = month_projection(session, version, year, month, account)
    except NoSuchAccount as exc:
        raise HTTPException(404, "No such account") from exc
    p, target, opening = projected.month, projected.account, projected.opening_pence
    payday, bill_month = projected.payday_month, projected.bill_month
    return ProjectionOut(
        account=target,
        payday_month=payday,
        bill_month=bill_month,
        opening_pence=opening,
        minimum_pence=round_pence(p.minimum),
        minimum_day=p.minimum_day,
        goes_negative=p.goes_negative,
        undated_lines=list(p.undated_lines),
        days=[
            DayOut(
                day=d.day,
                closing_pence=round_pence(d.closing),
                movements=[MovementOut(name=n, pence=round_pence(v)) for n, v in d.movements],
            )
            for d in p.days
        ],
    )


class VersionIncomeOut(BaseModel):
    person: str
    net_monthly_pence: int | None
    gross_annual_pence: int
    benefits_allowance_pence: int
    as_of_date: date
    employer: str | None


@router.get("/{version_id}/incomes", response_model=list[VersionIncomeOut])
def version_incomes(version_id: int, session: SessionDep) -> list[VersionIncomeOut]:
    """Each person's pay as this version sees it (its own override, or inherited)."""
    resolved = resolve(session, get_version_or_404(session, version_id), today())
    return [VersionIncomeOut(**i.__dict__) for i in resolved.incomes]
