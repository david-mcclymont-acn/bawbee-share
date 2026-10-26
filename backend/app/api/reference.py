"""Reference data for the screens: people, accounts, categories, follow-ups, checks."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import SessionDep, UserDep, current_session
from app.api.versions import today
from app.db import utcnow
from app.domain import audit
from app.domain.checks import stale_inputs
from app.domain.models import Account, Category, FollowUp, IncomeSource, Person

router = APIRouter(prefix="/api", tags=["reference"], dependencies=[Depends(current_session)])


class PersonOut(BaseModel):
    key: str
    name: str


class AccountOut(BaseModel):
    key: str
    name: str
    role: str
    owner: str | None
    provider: str | None


class FollowUpOut(BaseModel):
    id: int
    line_id: int | None
    line_name: str | None
    reason: str
    note: str | None
    status: str


class FollowUpChange(BaseModel):
    status: Literal["open", "done", "dismissed"]


class IncomeOut(BaseModel):
    person: str
    employer: str | None
    as_of_date: date
    gross_annual_pence: int
    benefits_allowance_pence: int | None


class StaleOut(BaseModel):
    label: str
    as_of: date


@router.get("/people", response_model=list[PersonOut])
def people(session: SessionDep) -> list[PersonOut]:
    return [
        PersonOut(key=p.key, name=p.name)
        for p in session.scalars(select(Person).order_by(Person.id))
    ]


@router.get("/accounts", response_model=list[AccountOut])
def accounts(session: SessionDep) -> list[AccountOut]:
    return [
        AccountOut(
            key=a.key,
            name=a.name,
            role=a.role,
            provider=a.provider,
            owner=a.owner.key if a.owner else None,
        )
        for a in session.scalars(select(Account).order_by(Account.id))
    ]


@router.get("/categories", response_model=list[str])
def categories(session: SessionDep) -> list[str]:
    return list(session.scalars(select(Category.name).order_by(Category.name)))


@router.get("/incomes", response_model=list[IncomeOut])
def incomes(session: SessionDep) -> list[IncomeOut]:
    return [
        IncomeOut(
            person=s.person.key,
            employer=s.employer,
            as_of_date=s.as_of_date,
            gross_annual_pence=s.gross_annual_pence,
            benefits_allowance_pence=s.benefits_allowance_pence,
        )
        for s in session.scalars(select(IncomeSource).order_by(IncomeSource.id))
    ]


@router.get("/follow-ups", response_model=list[FollowUpOut])
def follow_ups(session: SessionDep, status: str | None = "open") -> list[FollowUpOut]:
    query = select(FollowUp).order_by(FollowUp.id)
    if status:
        query = query.where(FollowUp.status == status)
    return [
        FollowUpOut(
            id=f.id,
            line_id=f.line_id,
            line_name=f.line.name if f.line else None,
            reason=f.reason,
            note=f.note,
            status=f.status,
        )
        for f in session.scalars(query)
    ]


@router.patch("/follow-ups/{follow_up_id}", response_model=FollowUpOut)
def update_follow_up(
    follow_up_id: int, body: FollowUpChange, user: UserDep, session: SessionDep
) -> FollowUpOut:
    item = session.get(FollowUp, follow_up_id)
    if item is None:
        raise HTTPException(404, "No such follow-up")
    before = item.status
    item.status = body.status
    item.resolved_at = None if body.status == "open" else utcnow()
    audit.record(session, user, "follow_up", item.id, "status", before, body.status)
    session.commit()
    return FollowUpOut(
        id=item.id,
        line_id=item.line_id,
        line_name=item.line.name if item.line else None,
        reason=item.reason,
        note=item.note,
        status=item.status,
    )


@router.get("/checks/stale", response_model=list[StaleOut])
def stale(session: SessionDep) -> list[StaleOut]:
    return [StaleOut(label=s.label, as_of=s.as_of) for s in stale_inputs(session, today())]
