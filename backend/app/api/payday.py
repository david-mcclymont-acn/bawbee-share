"""Payday checklist API (PLAN.md §5.7, §11.3)."""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import SessionDep, UserDep, current_session
from app.domain import payday
from app.domain.auth import as_utc
from app.domain.models import Account, PaydayTransfer, Person, User

router = APIRouter(prefix="/api/payday", tags=["payday"], dependencies=[Depends(current_session)])


class PayDateOut(BaseModel):
    official: date
    expected: date


class TransferOut(BaseModel):
    id: int | None  # None until the run has been started
    line_id: int | None
    name: str
    from_account: str
    from_owner: str | None
    to_account: str
    suggested_pence: int
    actual_pence: int | None
    effective_pence: int
    # What the budget says to send, and why this payday's figure is what it is:
    # "calculated" by the rule, "fixed" by a typed-in amount in the budget, or
    # "changed" for this payday only.
    budget_pence: int
    basis: Literal["calculated", "fixed", "changed"]
    rule: str
    done_at: datetime | None
    done_by: str | None
    # "bank" when an imported bank transfer ticked it, "manual" when someone did.
    done_via: Literal["bank", "manual"] | None = None


class PaydayOut(BaseModel):
    month: date  # the payday month
    funds_month: date  # the month whose bills it pays
    pay_dates: dict[str, PayDateOut]
    transfer_date: date
    run_id: int | None
    transfers: list[TransferOut]
    complete: bool


class TransferChange(BaseModel):
    done: bool | None = None
    actual_pence: int | None = Field(None, ge=0)


def _month(year: int, month: int) -> date:
    if not 1 <= month <= 12:
        raise HTTPException(422, "Month must be 1 to 12")
    return payday.month_key(year, month)


def _view(session: SessionDep, month: date) -> PaydayOut:
    accounts = {a.id: a for a in session.scalars(select(Account))}
    by_key = {a.key: a for a in accounts.values()}
    people = {p.id: p.key for p in session.scalars(select(Person))}
    users = {u.id: u.display_name for u in session.scalars(select(User))}

    def owner(account: Account) -> str | None:
        return people.get(account.owner_person_id) if account.owner_person_id else None

    planned = payday.planned_transfers(session, month)
    by_line = {t.line_id: t for t in planned}

    def basis(
        effective: int, plan: payday.PlannedTransfer | None
    ) -> Literal["calculated", "fixed", "changed"]:
        if plan is not None and effective != plan.budget_pence:
            return "changed"
        if plan is not None and plan.actual_pence is not None:
            return "fixed"
        return "calculated"

    run = payday.get_run(session, month)
    transfers: list[TransferOut] = []
    if run is not None:
        for t in run.transfers:
            plan = by_line.get(t.line_id)
            effective = t.suggested_pence if t.actual_pence is None else t.actual_pence
            transfers.append(
                TransferOut(
                    id=t.id,
                    line_id=t.line_id,
                    name=t.name,
                    from_account=accounts[t.from_account_id].key,
                    from_owner=owner(accounts[t.from_account_id]),
                    to_account=accounts[t.to_account_id].key,
                    suggested_pence=t.suggested_pence,
                    actual_pence=t.actual_pence,
                    effective_pence=effective,
                    budget_pence=plan.budget_pence if plan else effective,
                    basis=basis(effective, plan),
                    rule=plan.rule if plan else "fixed",
                    done_at=as_utc(t.done_at) if t.done_at else None,
                    done_by=users.get(t.done_by) if t.done_by else None,
                    done_via="bank" if t.done_via == "bank" else "manual" if t.done_at else None,
                )
            )
    else:
        for plan in planned:
            transfers.append(
                TransferOut(
                    id=None,
                    line_id=plan.line_id,
                    name=plan.name,
                    from_account=plan.from_account,
                    from_owner=owner(by_key[plan.from_account]),
                    to_account=plan.to_account,
                    suggested_pence=plan.suggested_pence,
                    actual_pence=plan.actual_pence,
                    effective_pence=plan.budget_pence,
                    budget_pence=plan.budget_pence,
                    basis=basis(plan.budget_pence, plan),
                    rule=plan.rule,
                    done_at=None,
                    done_by=None,
                )
            )
    funds = payday.month_key(month.year + month.month // 12, month.month % 12 + 1)
    return PaydayOut(
        month=month,
        funds_month=funds,
        pay_dates={
            p: PayDateOut(official=d.official, expected=d.expected)
            for p, d in payday.pay_dates(session, month).items()
        },
        transfer_date=payday.transfer_date(session, month),
        run_id=run.id if run else None,
        transfers=transfers,
        complete=bool(transfers) and all(t.done_at is not None for t in transfers),
    )


@router.get("/{year}/{month}", response_model=PaydayOut)
def get_payday(year: int, month: int, session: SessionDep) -> PaydayOut:
    return _view(session, _month(year, month))


@router.post("/{year}/{month}/start", response_model=PaydayOut)
def start(year: int, month: int, user: UserDep, session: SessionDep) -> PaydayOut:
    key = _month(year, month)
    payday.start_run(session, key, user)
    session.commit()
    return _view(session, key)


@router.patch("/transfers/{transfer_id}", response_model=PaydayOut)
def update_transfer(
    transfer_id: int, body: TransferChange, user: UserDep, session: SessionDep
) -> PaydayOut:
    transfer = session.get(PaydayTransfer, transfer_id)
    if transfer is None:
        raise HTTPException(404, "No such transfer")
    fields = body.model_dump(exclude_unset=True)
    payday.update_transfer(
        session,
        transfer,
        user,
        done=fields.get("done"),
        actual_pence=fields.get("actual_pence", payday.UNSET),
    )
    session.commit()
    return _view(session, transfer.run.month)
