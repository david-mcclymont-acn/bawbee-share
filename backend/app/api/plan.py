"""The Plan screens (Phases 6 and 7): forecast, compare, take-home, childcare, mortgage,
renewals. Every write is audited with the person who made it."""

from datetime import date
from fractions import Fraction
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, UserDep, current_session
from app.api.versions import get_version_or_404, summary, today
from app.domain import audit, editing, forecast, takehome, taxconfig
from app.domain import childcare as care
from app.domain import mortgage as mortgage_domain
from app.domain.models import (
    Account,
    BudgetLine,
    BudgetVersion,
    ChildcareChild,
    ChildcareNursery,
    ChildcarePattern,
    ChildcareScenario,
    ForecastPath,
    ForecastStep,
    IncomeSource,
    Mortgage,
    OneOff,
    Payslip,
    Renewal,
    SavingsGoal,
)
from app.domain.versions import live_version, resolve
from app.engine import mortgage as mortgage_engine
from app.engine.money import decimal_text, round_pence
from app.engine.mortgage import add_months, months_between

router = APIRouter(prefix="/api/plan", tags=["plan"], dependencies=[Depends(current_session)])

LIVE_PATH = 0  # the live budget on its own, always available


def _first(d: date) -> date:
    return date(d.year, d.month, 1)


def _account(session: Session, key: str) -> Account:
    account = session.scalars(select(Account).where(Account.key == key)).first()
    if account is None:
        raise HTTPException(422, f"There's no account {key!r}.")
    return account


def _get[T](session: Session, model: type[T], ident: int, what: str) -> T:
    row = session.get(model, ident)
    if row is None:
        raise HTTPException(404, f"No such {what}.")
    return row


def _decimal(value: str, what: str) -> str:
    try:
        number = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise HTTPException(422, f"{what} should be a number.") from exc
    if number < 0:
        raise HTTPException(422, f"{what} can't be negative.")
    return value


# ---- forecast paths -------------------------------------------------------------------


class StepIn(BaseModel):
    version_id: int
    from_month: date

    @field_validator("from_month")
    @classmethod
    def _first_of_month(cls, v: date) -> date:
        return date(v.year, v.month, 1)


class PathIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    steps: list[StepIn] = Field(min_length=1, max_length=12)
    is_default: bool = False
    notes: str | None = Field(None, max_length=2000)


def _path_out(path: ForecastPath) -> dict[str, Any]:
    return {
        "id": path.id,
        "name": path.name,
        "is_default": path.is_default,
        "notes": path.notes,
        "steps": [
            {
                "version_id": s.version_id,
                "version_name": s.version.name,
                "from_month": s.from_month.isoformat(),
            }
            for s in sorted(path.steps, key=lambda s: s.from_month)
        ],
    }


@router.get("/paths")
def paths(session: SessionDep) -> dict[str, Any]:
    live = live_version(session)
    rows = session.scalars(select(ForecastPath).order_by(ForecastPath.id)).all()
    return {
        "live": {"id": LIVE_PATH, "name": f"{live.name} as it is", "version_id": live.id},
        "paths": [_path_out(p) for p in rows],
        "default_id": next((p.id for p in rows if p.is_default), LIVE_PATH),
    }


def _save_path(session: Session, path: ForecastPath, body: PathIn) -> None:
    months = [s.from_month for s in body.steps]
    if len(set(months)) != len(months):
        raise HTTPException(422, "Two versions can't start in the same month.")
    for step in body.steps:
        version = get_version_or_404(session, step.version_id)
        if version.type == "period":
            raise HTTPException(422, f"{version.name!r} is a past period, not a plan.")
    path.name = body.name
    path.notes = body.notes
    path.steps.clear()
    session.flush()
    for step in body.steps:
        path.steps.append(ForecastStep(version_id=step.version_id, from_month=step.from_month))
    if body.is_default:
        for other in session.scalars(select(ForecastPath).where(ForecastPath.id != path.id)):
            other.is_default = False
    path.is_default = body.is_default
    session.flush()


@router.post("/paths", status_code=201)
def create_path(body: PathIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    path = ForecastPath(name=body.name)
    session.add(path)
    session.flush()
    _save_path(session, path, body)
    audit.record(
        session, user, "forecast_path", path.id, "create", None, body.model_dump(mode="json")
    )
    session.commit()
    return _path_out(path)


@router.put("/paths/{path_id}")
def update_path(path_id: int, body: PathIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    path = _get(session, ForecastPath, path_id, "forecast path")
    before = _path_out(path)
    _save_path(session, path, body)
    audit.record(
        session, user, "forecast_path", path.id, "update", before, body.model_dump(mode="json")
    )
    session.commit()
    return _path_out(path)


@router.delete("/paths/{path_id}", status_code=204)
def delete_path(path_id: int, user: UserDep, session: SessionDep) -> None:
    path = _get(session, ForecastPath, path_id, "forecast path")
    before = _path_out(path)
    for one in session.scalars(select(OneOff).where(OneOff.path_id == path.id)):
        session.delete(one)
    session.delete(path)
    audit.record(session, user, "forecast_path", path_id, "delete", before, None)
    session.commit()


# ---- the forecast -----------------------------------------------------------------------


def _plan(session: Session, path_id: int) -> forecast.Plan:
    if path_id == LIVE_PATH:
        return forecast.live_plan(session)
    path = _get(session, ForecastPath, path_id, "forecast path")
    try:
        return forecast.plan_for(path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _run_out(run: forecast.Run) -> dict[str, Any]:
    f = run.forecast
    return {
        "path_id": run.plan.id if run.plan.id is not None else LIVE_PATH,
        "name": run.plan.name,
        "months": [
            {
                "month": m.month.isoformat(),
                "version_id": m.version_id,
                "version_name": m.version_name,
                "household_pence": m.household_surplus,
                "joint_pence": m.joint_surplus,
                "personal_pence": m.personal_surplus,
                "balances": m.balances,
                "events": list(m.events),
                "one_offs": [
                    {"account": o.account, "pence": o.pence, "label": o.label} for o in m.one_offs
                ],
                "goals": m.goals,
            }
            for m in f.months
        ],
        "goals": [
            {
                "key": g.key,
                "id": run.goal_ids.get(g.key),
                "name": g.name,
                "target_pence": g.target,
                "target_month": g.target_month.isoformat(),
                "saved_pence": g.saved,
                "needed_pence": g.needed_monthly,
                "allocation_pence": g.allocation,
                "on_track": g.on_track,
                "short_by_pence": g.short_by,
            }
            for g in f.goals
        ],
        "warnings": [
            {
                "month": w.month.isoformat(),
                "code": w.code,
                "subject": w.subject,
                "message": w.message,
            }
            for w in f.warnings
        ],
    }


@router.get("/forecast")
def forecast_view(
    session: SessionDep,
    path: Annotated[list[int] | None, Query(max_length=4)] = None,
) -> dict[str, Any]:
    """One run per path asked for (up to four), or the default path."""
    ids = path or [
        next(
            (
                p.id
                for p in session.scalars(
                    select(ForecastPath).where(ForecastPath.is_default.is_(True))
                )
            ),
            LIVE_PATH,
        )
    ]
    when = today()
    runs = [forecast.run(session, _plan(session, i), when) for i in dict.fromkeys(ids)]
    opening = runs[0].opening if runs else {}
    accounts = session.scalars(
        select(Account).where(Account.role != "dormant").order_by(Account.id)
    )
    return {
        "start": forecast.start_month(when).isoformat(),
        "accounts": [
            {
                "key": a.key,
                "name": a.name,
                "role": a.role,
                "owner": a.owner.key if a.owner else None,
                "opening_pence": opening[a.key].pence if a.key in opening else 0,
                "opening_source": opening[a.key].source if a.key in opening else "none",
                "opening_as_of": opening[a.key].as_of if a.key in opening else None,
            }
            for a in accounts
        ],
        "runs": [_run_out(r) for r in runs],
    }


class OpeningIn(BaseModel):
    pence: int | None = Field(None, ge=-100_000_000, le=1_000_000_000)


@router.put("/opening/{account_key}", status_code=204)
def set_opening(account_key: str, body: OpeningIn, user: UserDep, session: SessionDep) -> None:
    """A balance typed in for an account with no bank link (savings, say)."""
    account = _account(session, account_key)
    before = account.opening_balance_pence
    account.opening_balance_pence = body.pence
    audit.record(session, user, "account", account.id, "opening_balance", before, body.pence)
    session.commit()


# ---- one-offs ---------------------------------------------------------------------------


class OneOffIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    account: str
    on: date
    amount_pence: int = Field(ge=-100_000_000, le=100_000_000)  # in positive, out negative
    path_id: int | None = None


def _one_off_out(o: OneOff) -> dict[str, Any]:
    return {
        "id": o.id,
        "label": o.label,
        "account": o.account.key,
        "on": o.on.isoformat(),
        "amount_pence": o.amount_pence,
        "path_id": o.path_id,
    }


@router.get("/one-offs")
def one_offs(session: SessionDep) -> list[dict[str, Any]]:
    return [_one_off_out(o) for o in session.scalars(select(OneOff).order_by(OneOff.on))]


def _fill_one_off(session: Session, o: OneOff, body: OneOffIn) -> None:
    if body.amount_pence == 0:
        raise HTTPException(422, "A one-off needs an amount.")
    if body.path_id not in (None, LIVE_PATH):
        _get(session, ForecastPath, body.path_id, "forecast path")
    o.label, o.on, o.amount_pence = body.label, body.on, body.amount_pence
    o.account_id = _account(session, body.account).id
    o.path_id = body.path_id or None


@router.post("/one-offs", status_code=201)
def create_one_off(body: OneOffIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    o = OneOff()
    _fill_one_off(session, o, body)
    session.add(o)
    session.flush()
    audit.record(session, user, "one_off", o.id, "create", None, body.model_dump(mode="json"))
    session.commit()
    return _one_off_out(o)


@router.put("/one-offs/{one_off_id}")
def update_one_off(
    one_off_id: int, body: OneOffIn, user: UserDep, session: SessionDep
) -> dict[str, Any]:
    o = _get(session, OneOff, one_off_id, "one-off")
    before = _one_off_out(o)
    _fill_one_off(session, o, body)
    session.flush()
    audit.record(session, user, "one_off", o.id, "update", before, body.model_dump(mode="json"))
    session.commit()
    return _one_off_out(o)


@router.delete("/one-offs/{one_off_id}", status_code=204)
def delete_one_off(one_off_id: int, user: UserDep, session: SessionDep) -> None:
    o = _get(session, OneOff, one_off_id, "one-off")
    audit.record(session, user, "one_off", o.id, "delete", _one_off_out(o), None)
    session.delete(o)
    session.commit()


# ---- savings goals ----------------------------------------------------------------------


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account: str
    line_id: int | None = None
    target_pence: int = Field(gt=0, le=1_000_000_000)
    target_date: date
    saved_pence: int = Field(ge=0, le=1_000_000_000)
    saved_on: date | None = None
    repeats_yearly: bool = False


def _goal_out(g: SavingsGoal) -> dict[str, Any]:
    return {
        "id": g.id,
        "name": g.name,
        "account": g.account.key,
        "line_id": g.line_id,
        "line_label": g.line.name if g.line else None,
        "target_pence": g.target_pence,
        "target_date": g.target_date.isoformat(),
        "saved_pence": g.saved_pence,
        "saved_on": g.saved_on.isoformat(),
        "repeats_yearly": g.repeats_yearly,
        "status": g.status,
    }


@router.get("/goals")
def goals(session: SessionDep) -> dict[str, Any]:
    rows = session.scalars(select(SavingsGoal).order_by(SavingsGoal.id)).all()
    taken = {g.line_id for g in rows if g.line_id}
    live = resolve(session, live_version(session), today())
    # Allocation lines that look like sinking funds, offered as goals to set up.
    candidates = [
        {
            "line_id": ln.line_id,
            "label": ln.label,
            "account": ln.account,
            "monthly_pence": round_pence(ln.monthly()),
        }
        for ln in live.lines
        if ln.kind == "allocation"
        and ln.line_id not in taken
        and ln.line_id > 0
        and (ln.category or "").lower() in ("sinking fund", "savings")
    ]
    return {"goals": [_goal_out(g) for g in rows], "candidates": candidates}


def _fill_goal(session: Session, g: SavingsGoal, body: GoalIn) -> None:
    g.name, g.target_pence, g.target_date = body.name, body.target_pence, body.target_date
    g.saved_pence, g.saved_on = body.saved_pence, body.saved_on or today()
    g.repeats_yearly = body.repeats_yearly
    g.account_id = _account(session, body.account).id
    if body.line_id is not None:
        line = _get(session, BudgetLine, body.line_id, "budget line")
        if line.kind != "allocation":
            raise HTTPException(422, "A goal is paid by an allocation line.")
    g.line_id = body.line_id


@router.post("/goals", status_code=201)
def create_goal(body: GoalIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    g = SavingsGoal()
    _fill_goal(session, g, body)
    session.add(g)
    session.flush()
    audit.record(session, user, "savings_goal", g.id, "create", None, body.model_dump(mode="json"))
    session.commit()
    return _goal_out(g)


@router.put("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    g = _get(session, SavingsGoal, goal_id, "goal")
    before = _goal_out(g)
    _fill_goal(session, g, body)
    session.flush()
    audit.record(
        session, user, "savings_goal", g.id, "update", before, body.model_dump(mode="json")
    )
    session.commit()
    return _goal_out(g)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: int, user: UserDep, session: SessionDep) -> None:
    g = _get(session, SavingsGoal, goal_id, "goal")
    audit.record(session, user, "savings_goal", g.id, "delete", _goal_out(g), None)
    session.delete(g)
    session.commit()


# ---- compare (PLAN.md §5.8) --------------------------------------------------------------


@router.get("/compare")
def compare(
    session: SessionDep, version: Annotated[list[int], Query(min_length=2, max_length=4)]
) -> dict[str, Any]:
    """Two to four versions side by side: every line by account, and what differs."""
    when = today()
    ids = list(dict.fromkeys(version))
    if len(ids) < 2:
        raise HTTPException(422, "Choose two to four different versions.")
    summaries = [summary(i, session) for i in ids]
    rows: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for vid in ids:
        resolved = resolve(session, get_version_or_404(session, vid), when)
        for ln in resolved.lines:
            if ln.line_id not in rows:
                order.append(ln.line_id)
                rows[ln.line_id] = {
                    "line_id": ln.line_id,
                    "label": ln.label,
                    "account": ln.account,
                    "kind": ln.kind,
                    "computed": ln.origin == "computed",
                    "amounts": {},
                }
            rows[ln.line_id]["amounts"][str(vid)] = round_pence(ln.monthly())
    lines = []
    for line_id in order:
        row = rows[line_id]
        values = [row["amounts"].get(str(v)) for v in ids]
        row["differs"] = len(set(values)) > 1
        lines.append(row)
    return {
        "versions": [s.model_dump(mode="json") for s in summaries],
        "lines": lines,
    }


# ---- take-home ---------------------------------------------------------------------------


def _source_fields(session: Session, s: IncomeSource) -> dict[str, Any]:
    payslip = takehome.latest_payslip(session, s)
    return {
        "id": s.id,
        "employer": s.employer,
        "gross_annual_pence": s.gross_annual_pence,
        "benefits_allowance_pence": s.benefits_allowance_pence,
        "salary_sacrifice_pence": s.salary_sacrifice_pence,
        "pension_scheme": s.pension_scheme,
        "pension_pct": s.pension_pct,
        "tax_code": s.tax_code,
        "payslip_net_monthly_pence": payslip.net_monthly_pence if payslip else None,
        "payslip_on": payslip.on.isoformat() if payslip else None,
    }


def _p(pounds: Fraction) -> int:
    return round_pence(pounds * 100)


@router.get("/take-home")
def take_home(session: SessionDep) -> dict[str, Any]:
    when = today()
    try:
        checks = takehome.checks(session, when)
    except taxconfig.TaxConfigMissing as exc:
        raise HTTPException(422, str(exc)) from exc
    sources = {s.person.key: s for s in takehome.latest_sources(session)}
    year = taxconfig.year_for(when)
    raw = taxconfig.raw(year.label)
    return {
        "tax_year": year.label,
        "exact_year": taxconfig.is_exact(when),
        "sources": {
            "bands": raw.get("scottish_bands_source"),
            "national_insurance": raw["national_insurance"].get("source"),
            "pension": raw.get("nhs_scotland_pension_source"),
        },
        "tolerance_pence": takehome.TOLERANCE_PENCE,
        "people": [
            {
                "person": c.person,
                "name": c.name,
                "income": _source_fields(session, sources[c.person]),
                "calculated": {
                    "gross_pence": _p(c.calculated.gross),
                    "sacrifice_pence": _p(c.calculated.sacrifice),
                    "pension_rate": decimal_text(c.calculated.pension_rate),
                    "pension_pence": _p(c.calculated.pension),
                    "allowance_pence": _p(c.calculated.allowance),
                    "tax_pence": _p(c.calculated.tax),
                    "tax_slices": [
                        {"label": sl.label, "rate": decimal_text(sl.rate), "pence": _p(sl.amount)}
                        for sl in c.calculated.tax_slices
                    ],
                    "ni_pence": _p(c.calculated.ni),
                    "net_pence": _p(c.calculated.net),
                    "net_monthly_pence": c.calculated_monthly_pence,
                },
                "budget_monthly_pence": c.budget_monthly_pence,
                "expected_monthly_pence": c.expected_pence,
                "payslip_monthly_pence": c.payslip_monthly_pence,
                "salaries": [
                    {
                        "on": s.on.isoformat(),
                        "pence": s.pence,
                        "description": s.description,
                        "difference_pence": s.pence - c.expected_pence,
                    }
                    for s in c.salaries
                ],
            }
            for c in checks
        ],
    }


class IncomeIn(BaseModel):
    gross_annual_pence: int = Field(ge=0, le=100_000_000)
    benefits_allowance_pence: int | None = Field(None, ge=0, le=100_000_000)
    salary_sacrifice_pence: int | None = Field(None, ge=0, le=100_000_000)
    pension_scheme: Literal["nhs_scotland", "workplace_pct", "none"]
    pension_pct: str | None = Field(None, max_length=16)
    tax_code: str | None = Field(None, max_length=16, pattern=r"^[A-Za-z0-9 ]*$")
    payslip_net_monthly_pence: int | None = Field(None, ge=0, le=10_000_000)
    payslip_on: date | None = None


@router.put("/take-home/{source_id}")
def update_income(
    source_id: int, body: IncomeIn, user: UserDep, session: SessionDep
) -> dict[str, Any]:
    source = _get(session, IncomeSource, source_id, "income")
    if body.pension_pct:
        _decimal(body.pension_pct, "The pension percentage")
    before = _source_fields(session, source)
    fields = body.model_dump(exclude={"payslip_net_monthly_pence", "payslip_on"})
    for key, value in fields.items():
        setattr(
            source, key, (value.strip().upper() or None) if key == "tax_code" and value else value
        )
    latest = takehome.latest_payslip(session, source)
    if body.payslip_net_monthly_pence is not None and (
        latest is None
        or (latest.net_monthly_pence, latest.on)
        != (body.payslip_net_monthly_pence, body.payslip_on)
    ):
        session.add(
            Payslip(
                income_source_id=source.id,
                on=body.payslip_on or today(),
                net_monthly_pence=body.payslip_net_monthly_pence,
                created_by=user.id,
            )
        )
    session.flush()
    after = _source_fields(session, source)
    audit.record(session, user, "income_source", source.id, "update", before, after)
    session.commit()
    return after


@router.post("/take-home/{source_id}/use", status_code=204)
def use_calculated(source_id: int, user: UserDep, session: SessionDep) -> None:
    """The live budget takes the calculated take-home for this person."""
    source = _get(session, IncomeSource, source_id, "income")
    net = takehome.monthly_pence(takehome.calculate(source, today()).net)
    editing.set_net_income(session, live_version(session), source, net, user)
    session.commit()


# ---- childcare ---------------------------------------------------------------------------


@router.get("/childcare")
def childcare_view(session: SessionDep) -> dict[str, Any]:
    when = today()
    try:
        year = taxconfig.year_for(when)
    except taxconfig.TaxConfigMissing as exc:
        raise HTTPException(422, str(exc)) from exc
    nurseries, patterns, grid = care.cells(session, when)
    flat = [cell for row in grid for cell in row]
    best = max(flat, key=lambda c: c.scenario.annual) if flat else None
    kids = care.children(session)
    funded = care.funded_hours(session, when)
    scenarios = session.scalars(select(ChildcareScenario).order_by(ChildcareScenario.id)).all()
    return {
        "tax_year": year.label,
        "fields": care.fields(session),
        "children": [
            {
                "id": c.id,
                "name": c.name,
                "born_on": c.born_on.isoformat() if c.born_on else None,
                "eligible_at_two": c.eligible_at_two,
                "funded_from": (f.isoformat() if (f := care.funded_from(c, year.label)) else None),
                "funded_now": bool(funded[i]) if i < len(funded) else False,
            }
            for i, c in enumerate(kids)
        ],
        "nurseries": [
            {
                "id": n.id,
                "name": n.name,
                "basis": n.basis,
                "takes_tfc": n.takes_tfc,
                "bills_leave": n.bills_leave,
                "discounts_confirmed": n.discounts_confirmed,
                "rates": n.rates_json,
            }
            for n in nurseries
        ],
        "patterns": [{"id": p.id, "days": p.days, "hours": p.hours} for p in patterns],
        "cells": [
            {
                "nursery_id": c.nursery_id,
                "pattern_id": c.pattern_id,
                **care.scenario_out(c.scenario),
            }
            for c in flat
        ],
        "best": {"nursery_id": best.nursery_id, "pattern_id": best.pattern_id} if best else None,
        "payslip_check": care.payslip_check(session, when),
        "scenarios": [
            {
                "id": s.id,
                "name": s.name,
                "nursery_id": s.nursery_id,
                "pattern_id": s.pattern_id,
                "account": s.account.key,
                "version_id": s.version_id,
                "version_name": s.version.name if s.version else None,
            }
            for s in scenarios
        ],
    }


class ProfileIn(BaseModel):
    fields: dict[str, str | bool | None]


NUMBER_FIELDS = {
    "salary",
    "ref_hours",
    "ref_days",
    "bank_holidays",
    "leave_days",
    "partner_income",
    "cb_first_weekly",
    "cb_other_weekly",
    "tfc_cap",
    "tfc_rate",
    "ni_primary_threshold",
    "ni_upper_limit",
    "payslip_monthly",
}
BOOL_FIELDS = {"use_child_benefit", "net_pay_arrangement"}


@router.put("/childcare/profile", status_code=204)
def update_profile(body: ProfileIn, user: UserDep, session: SessionDep) -> None:
    clean: dict[str, Any] = {}
    for key, value in body.fields.items():
        if key in BOOL_FIELDS:
            clean[key] = bool(value)
        elif key in NUMBER_FIELDS:
            clean[key] = (
                _decimal(str(value), key.replace("_", " ")) if value not in (None, "") else None
            )
        elif key == "tax_code":
            clean[key] = str(value).strip().upper() or None if value else None
        else:
            raise HTTPException(422, f"Unknown field {key!r}.")
    if clean.get("ref_hours") in ("0", 0) or clean.get("ref_days") in ("0", 0):
        raise HTTPException(422, "The reference hours and days must be more than zero.")
    row = care.profile_row(session)
    before = dict(row.fields_json or {})
    row.fields_json = {**before, **clean}
    audit.record(session, user, "childcare", row.id, "profile", before, row.fields_json)
    session.commit()


class ChildIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    born_on: date | None = None
    eligible_at_two: bool = False


@router.post("/childcare/children", status_code=201)
def add_child(body: ChildIn, user: UserDep, session: SessionDep) -> dict[str, int]:
    child = ChildcareChild(**body.model_dump(), position=len(care.children(session)))
    session.add(child)
    session.flush()
    for nursery in care.nurseries(session):  # a rate for the new child, like the old app
        rates = list(nursery.rates_json or [])
        rates.append(
            dict(rates[-1]) if rates else {"rate": "0", "discount_pct": "0", "min_days": "0"}
        )
        nursery.rates_json = rates
    audit.record(
        session, user, "childcare_child", child.id, "create", None, body.model_dump(mode="json")
    )
    session.commit()
    return {"id": child.id}


@router.put("/childcare/children/{child_id}", status_code=204)
def update_child(child_id: int, body: ChildIn, user: UserDep, session: SessionDep) -> None:
    child = _get(session, ChildcareChild, child_id, "child")
    before = {
        "name": child.name,
        "born_on": str(child.born_on),
        "eligible_at_two": child.eligible_at_two,
    }
    child.name, child.born_on, child.eligible_at_two = body.name, body.born_on, body.eligible_at_two
    audit.record(
        session, user, "childcare_child", child.id, "update", before, body.model_dump(mode="json")
    )
    session.commit()


@router.delete("/childcare/children/{child_id}", status_code=204)
def delete_child(child_id: int, user: UserDep, session: SessionDep) -> None:
    kids = care.children(session)
    child = _get(session, ChildcareChild, child_id, "child")
    index = next(i for i, c in enumerate(kids) if c.id == child.id)
    for nursery in care.nurseries(session):
        rates = list(nursery.rates_json or [])
        if index < len(rates):
            rates.pop(index)
            nursery.rates_json = rates
    audit.record(session, user, "childcare_child", child.id, "delete", {"name": child.name}, None)
    session.delete(child)
    session.commit()


class RateIn(BaseModel):
    rate: str = Field(max_length=16)
    discount_pct: str = Field("0", max_length=16)
    min_days: str = Field("0", max_length=16)


class NurseryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    basis: Literal["day", "hour"]
    takes_tfc: bool = True
    bills_leave: bool = False
    discounts_confirmed: bool = False
    rates: list[RateIn] = Field(max_length=10)


def _fill_nursery(n: ChildcareNursery, body: NurseryIn) -> None:
    for r in body.rates:
        _decimal(r.rate, "A rate")
        _decimal(r.discount_pct, "A discount")
        _decimal(r.min_days, "The minimum days")
        if Fraction(r.discount_pct) > 100:
            raise HTTPException(422, "A discount is at most 100%.")
    n.name, n.basis, n.takes_tfc, n.bills_leave = (
        body.name,
        body.basis,
        body.takes_tfc,
        body.bills_leave,
    )
    n.discounts_confirmed = body.discounts_confirmed
    n.rates_json = [r.model_dump() for r in body.rates]


@router.post("/childcare/nurseries", status_code=201)
def add_nursery(body: NurseryIn, user: UserDep, session: SessionDep) -> dict[str, int]:
    n = ChildcareNursery(position=len(care.nurseries(session)))
    _fill_nursery(n, body)
    session.add(n)
    session.flush()
    audit.record(
        session, user, "childcare_nursery", n.id, "create", None, body.model_dump(mode="json")
    )
    session.commit()
    return {"id": n.id}


@router.put("/childcare/nurseries/{nursery_id}", status_code=204)
def update_nursery(nursery_id: int, body: NurseryIn, user: UserDep, session: SessionDep) -> None:
    n = _get(session, ChildcareNursery, nursery_id, "nursery")
    before = {"name": n.name, "rates": n.rates_json}
    _fill_nursery(n, body)
    audit.record(
        session, user, "childcare_nursery", n.id, "update", before, body.model_dump(mode="json")
    )
    session.commit()


@router.delete("/childcare/nurseries/{nursery_id}", status_code=204)
def delete_nursery(nursery_id: int, user: UserDep, session: SessionDep) -> None:
    n = _get(session, ChildcareNursery, nursery_id, "nursery")
    if session.scalars(
        select(ChildcareScenario).where(ChildcareScenario.nursery_id == n.id)
    ).first():
        raise HTTPException(422, "A saved scenario uses this nursery; remove that first.")
    audit.record(session, user, "childcare_nursery", n.id, "delete", {"name": n.name}, None)
    session.delete(n)
    session.commit()


class PatternIn(BaseModel):
    days: str = Field(max_length=16)
    hours: str = Field(max_length=16)


def _check_pattern(body: PatternIn) -> None:
    _decimal(body.days, "Days a week")
    _decimal(body.hours, "Hours a week")
    if not Fraction(0) < Fraction(body.days) <= 7 or Fraction(body.hours) <= 0:
        raise HTTPException(422, "A pattern is more than 0 and at most 7 days, with some hours.")


@router.post("/childcare/patterns", status_code=201)
def add_pattern(body: PatternIn, user: UserDep, session: SessionDep) -> dict[str, int]:
    _check_pattern(body)
    p = ChildcarePattern(days=body.days, hours=body.hours, position=len(care.patterns(session)))
    session.add(p)
    session.flush()
    audit.record(session, user, "childcare_pattern", p.id, "create", None, body.model_dump())
    session.commit()
    return {"id": p.id}


@router.put("/childcare/patterns/{pattern_id}", status_code=204)
def update_pattern(pattern_id: int, body: PatternIn, user: UserDep, session: SessionDep) -> None:
    _check_pattern(body)
    p = _get(session, ChildcarePattern, pattern_id, "pattern")
    before = {"days": p.days, "hours": p.hours}
    p.days, p.hours = body.days, body.hours
    audit.record(session, user, "childcare_pattern", p.id, "update", before, body.model_dump())
    session.commit()


@router.delete("/childcare/patterns/{pattern_id}", status_code=204)
def delete_pattern(pattern_id: int, user: UserDep, session: SessionDep) -> None:
    p = _get(session, ChildcarePattern, pattern_id, "pattern")
    if session.scalars(
        select(ChildcareScenario).where(ChildcareScenario.pattern_id == p.id)
    ).first():
        raise HTTPException(422, "A saved scenario uses this pattern; remove that first.")
    audit.record(
        session, user, "childcare_pattern", p.id, "delete", {"days": p.days, "hours": p.hours}, None
    )
    session.delete(p)
    session.commit()


@router.post("/childcare/import")
def import_childcare(
    user: UserDep, session: SessionDep, state: Annotated[dict[str, Any], Body()]
) -> dict[str, int]:
    """The old calculator's state.json (its Download a copy button)."""
    try:
        counts = care.import_state(session, state, user)
    except care.ChildcareError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return counts


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    nursery_id: int
    pattern_id: int
    account: str


class LinkIn(BaseModel):
    version_id: int | None


@router.post("/childcare/scenarios", status_code=201)
def add_scenario(body: ScenarioIn, user: UserDep, session: SessionDep) -> dict[str, int]:
    _get(session, ChildcareNursery, body.nursery_id, "nursery")
    _get(session, ChildcarePattern, body.pattern_id, "pattern")
    s = ChildcareScenario(
        name=body.name,
        nursery_id=body.nursery_id,
        pattern_id=body.pattern_id,
        account_id=_account(session, body.account).id,
    )
    session.add(s)
    session.flush()
    audit.record(session, user, "childcare_scenario", s.id, "create", None, body.model_dump())
    session.commit()
    return {"id": s.id}


@router.put("/childcare/scenarios/{scenario_id}/link", status_code=204)
def link_scenario(scenario_id: int, body: LinkIn, user: UserDep, session: SessionDep) -> None:
    s = _get(session, ChildcareScenario, scenario_id, "scenario")
    version = get_version_or_404(session, body.version_id) if body.version_id else None
    try:
        care.link(session, s, version, user)
    except care.ChildcareError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()


@router.delete("/childcare/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, user: UserDep, session: SessionDep) -> None:
    s = _get(session, ChildcareScenario, scenario_id, "scenario")
    audit.record(session, user, "childcare_scenario", s.id, "delete", {"name": s.name}, None)
    session.delete(s)
    session.commit()


# ---- the mortgage ------------------------------------------------------------------------


def _mortgage(session: Session) -> Mortgage:
    m = mortgage_domain.current(session)
    if m is None:
        raise HTTPException(
            404,
            "No mortgage set up yet. On the server: bawbee import-plan - < seed/budget-seed.json",
        )
    return m


@router.get("/mortgage")
def mortgage_view(session: SessionDep) -> dict[str, Any]:
    m = mortgage_domain.current(session)
    if m is None:
        return {"mortgage": None, "history": []}
    when = today()
    this_month = _first(when)
    try:
        engine_m = mortgage_domain.to_engine(m)
        owed, anchored = mortgage_domain.balance(m, add_months(this_month, -1))
    except mortgage_domain.MortgageError as exc:
        raise HTTPException(422, str(exc)) from exc
    fix_end = _first(m.fix_end_on) if m.fix_end_on else None
    rows = mortgage_engine.schedule(
        owed,
        engine_m.rate_pct,
        engine_m.monthly_payment,
        this_month,
        max(1, months_between(this_month, fix_end) + 1) if fix_end and fix_end > this_month else 12,
    )
    at_fix_end = rows[-1].closing if rows else owed
    value = m.property_value_pence
    line_amount = None
    if m.line_id:
        live = resolve(session, live_version(session), when)
        found = live.by_id().get(m.line_id)
        line_amount = found.amount_pence if found else None
    statement = mortgage_domain.latest_statement(m)
    window = mortgage_domain.remortgage_window(m, forecast.REMORTGAGE_LEAD_MONTHS)
    ltv = mortgage_engine.ltv(owed, value) if value else None
    return {
        "mortgage": {
            "id": m.id,
            "lender": m.lender,
            "principal_pence": m.principal_pence,
            "rate_pct": m.rate_pct,
            "term_months": m.term_months,
            "started_on": m.started_on.isoformat() if m.started_on else None,
            "first_payment_on": m.first_payment_on.isoformat() if m.first_payment_on else None,
            "fix_end_on": m.fix_end_on.isoformat() if m.fix_end_on else None,
            "fix_end_exact": m.fix_end_exact,
            "payment_pence": m.payment_pence,
            "overpay_allowance_pct": m.overpay_allowance_pct,
            "allowance_basis": m.allowance_basis,
            "property_value_pence": value,
            "property_value_on": m.property_value_on.isoformat() if m.property_value_on else None,
            "notes": m.notes,
            "line_id": m.line_id,
            "line_amount_pence": line_amount,
        },
        "balance_pence": owed,
        "balance_anchored": anchored,
        "balance_as_of": add_months(this_month, -1).isoformat(),
        "statement": {"on": statement.on.isoformat(), "balance_pence": statement.balance_pence}
        if statement
        else None,
        "statements": [
            {"on": s.on.isoformat(), "balance_pence": s.balance_pence} for s in m.statements
        ],
        "months_to_fix_end": months_between(this_month, fix_end) if fix_end else None,
        "remortgage_window": window.isoformat() if window else None,
        "balance_at_fix_end_pence": at_fix_end,
        "months_left": mortgage_domain.months_left(m, this_month),
        "ltv": {
            "pct": f"{float(ltv.pct):.1f}",
            "band": ltv.band,
            "next_band": ltv.next_band,
            "to_next_band_pence": ltv.to_next_band,
        }
        if ltv
        else None,
        "schedule": [
            {
                "on": r.on.isoformat(),
                "opening_pence": r.opening,
                "interest_pence": r.interest,
                "payment_pence": r.payment,
                "closing_pence": r.closing,
            }
            for r in rows
        ],
        "history": [
            {"lender": h.lender, "started": h.started_on.year if h.started_on else None}
            for h in mortgage_domain.history(session)
        ],
    }


class MortgageIn(BaseModel):
    fix_end_on: date | None = None
    fix_end_exact: bool | None = None
    payment_pence: int | None = Field(None, gt=0, le=10_000_000)
    rate_pct: str | None = Field(None, max_length=16)
    overpay_allowance_pct: str | None = Field(None, max_length=16)
    allowance_basis: Literal["anniversary", "calendar_year"] | None = None
    property_value_pence: int | None = Field(None, gt=0, le=10_000_000_000)
    property_value_on: date | None = None


@router.put("/mortgage", status_code=204)
def update_mortgage(body: MortgageIn, user: UserDep, session: SessionDep) -> None:
    m = _mortgage(session)
    changes = body.model_dump(exclude_unset=True)
    for key in ("rate_pct", "overpay_allowance_pct"):
        if changes.get(key):
            _decimal(changes[key], key.replace("_", " "))
    try:
        mortgage_domain.update(session, m, changes, user, today())
    except mortgage_domain.MortgageError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()


class StatementIn(BaseModel):
    on: date
    balance_pence: int = Field(gt=0, le=10_000_000_000)


@router.post("/mortgage/statements", status_code=201)
def add_statement(body: StatementIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    m = _mortgage(session)
    if body.on > today():
        raise HTTPException(422, "A statement can't be from the future.")
    try:
        mortgage_domain.add_statement(session, m, body.on, body.balance_pence, user)
    except mortgage_domain.MortgageError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return {"on": body.on.isoformat(), "balance_pence": body.balance_pence}


@router.get("/mortgage/overpay")
def overpay(
    session: SessionDep,
    monthly_pence: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
    lump_pence: Annotated[int, Query(ge=0, le=1_000_000_000)] = 0,
    lump_on: date | None = None,
) -> dict[str, Any]:
    m = _mortgage(session)
    this_month = _first(today())
    try:
        engine_m = mortgage_domain.to_engine(m)
        owed, _ = mortgage_domain.balance(m, add_months(this_month, -1))
    except mortgage_domain.MortgageError as exc:
        raise HTTPException(422, str(exc)) from exc
    left = mortgage_domain.months_left(m, add_months(this_month, -1))
    fix_end = _first(m.fix_end_on) if m.fix_end_on else None
    lumps = {_first(lump_on or this_month): lump_pence} if lump_pence else None
    plan = mortgage_engine.overpayment_plan(
        owed,
        engine_m.rate_pct,
        engine_m.monthly_payment,
        this_month,
        left,
        monthly_overpayment=monthly_pence,
        lumps=lumps,
        allowance_pct=Fraction(m.overpay_allowance_pct or "10"),
        year_starts=mortgage_domain.allowance_year_starts(
            m, this_month, fix_end or add_months(this_month, 12)
        ),
        fix_end=fix_end,
    )
    return {
        "months_saved": plan.months_saved,
        "interest_saved_pence": plan.interest_saved,
        "interest_without_pence": plan.interest_without,
        "interest_with_pence": plan.interest_with,
        "paid_off_without": plan.paid_off_without.isoformat() if plan.paid_off_without else None,
        "paid_off_with": plan.paid_off_with.isoformat() if plan.paid_off_with else None,
        "over_allowance": [
            {"year_from": start.isoformat(), "overpaid_pence": over, "allowance_pence": allowed}
            for start, over, allowed in plan.over_allowance
        ],
    }


class RemortgageIn(BaseModel):
    rate_pct: str = Field(min_length=1, max_length=16)
    base_version_id: int | None = None  # what it builds on; the live budget if not given


@router.post("/mortgage/remortgage", status_code=201)
def remortgage(body: RemortgageIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    m = _mortgage(session)
    base = get_version_or_404(session, body.base_version_id) if body.base_version_id else None
    try:
        version, payment = mortgage_domain.remortgage_whatif(
            session, m, Fraction(_decimal(body.rate_pct, "The rate")), user, today(), base
        )
    except (mortgage_domain.MortgageError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return {"version_id": version.id, "name": version.name, "payment_pence": payment}


# ---- renewals ----------------------------------------------------------------------------


class RenewalIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str | None = Field(None, max_length=200)
    line_id: int | None = None
    renews_on: date
    cost_pence: int | None = Field(None, ge=0, le=100_000_000)
    remind_days: int = Field(30, ge=1, le=366)
    notes: str | None = Field(None, max_length=2000)


def _renewal_out(r: Renewal) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "provider": r.provider,
        "line_id": r.line_id,
        "line_label": (f"{r.line.name} ({r.line.provider})" if r.line.provider else r.line.name)
        if r.line
        else None,
        "renews_on": r.renews_on.isoformat(),
        "cost_pence": r.cost_pence,
        "remind_days": r.remind_days,
        "notes": r.notes,
    }


@router.get("/renewals")
def renewals(session: SessionDep) -> dict[str, Any]:
    rows = session.scalars(select(Renewal).order_by(Renewal.renews_on)).all()
    taken = {r.line_id for r in rows if r.line_id}
    live = resolve(session, live_version(session), today())
    candidates = [
        {
            "line_id": ln.line_id,
            "label": ln.label,
            "name": ln.name,
            "provider": ln.provider,
            "account": ln.account,
            "monthly_pence": round_pence(ln.monthly()),
            "renewal_date": ln.renewal_date,
        }
        for ln in live.lines
        if ln.kind == "bill"
        and ln.line_id > 0
        and ln.line_id not in taken
        and (ln.category or "").lower() == "insurance"
    ]
    return {"renewals": [_renewal_out(r) for r in rows], "candidates": candidates}


def _fill_renewal(session: Session, r: Renewal, body: RenewalIn) -> None:
    if body.line_id is not None:
        _get(session, BudgetLine, body.line_id, "budget line")
    for key, value in body.model_dump().items():
        setattr(r, key, value)


@router.post("/renewals", status_code=201)
def create_renewal(body: RenewalIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    r = Renewal()
    _fill_renewal(session, r, body)
    session.add(r)
    session.flush()
    audit.record(session, user, "renewal", r.id, "create", None, body.model_dump(mode="json"))
    session.commit()
    return _renewal_out(r)


@router.put("/renewals/{renewal_id}")
def update_renewal(
    renewal_id: int, body: RenewalIn, user: UserDep, session: SessionDep
) -> dict[str, Any]:
    r = _get(session, Renewal, renewal_id, "renewal")
    before = _renewal_out(r)
    _fill_renewal(session, r, body)
    session.flush()
    audit.record(session, user, "renewal", r.id, "update", before, body.model_dump(mode="json"))
    session.commit()
    return _renewal_out(r)


@router.delete("/renewals/{renewal_id}", status_code=204)
def delete_renewal(renewal_id: int, user: UserDep, session: SessionDep) -> None:
    r = _get(session, Renewal, renewal_id, "renewal")
    audit.record(session, user, "renewal", r.id, "delete", _renewal_out(r), None)
    session.delete(r)
    session.commit()


# ---- the versions a path or comparison can use -------------------------------------------


@router.get("/versions")
def plan_versions(session: SessionDep) -> list[dict[str, Any]]:
    """Live and the draft what-ifs, with the planned start of each."""
    rows = session.scalars(
        select(BudgetVersion)
        .where(
            (BudgetVersion.type == "live")
            | ((BudgetVersion.type == "what_if") & (BudgetVersion.status == "draft"))
        )
        .order_by(BudgetVersion.id)
    )
    linked = {
        s.version_id: s.name for s in session.scalars(select(ChildcareScenario)) if s.version_id
    }
    return [
        {
            "id": v.id,
            "name": v.name,
            "type": v.type,
            "planned_start": v.planned_start.isoformat() if v.planned_start else None,
            "childcare": linked.get(v.id),
        }
        for v in rows
    ]
