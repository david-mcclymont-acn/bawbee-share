"""The inbox: everything waiting for a yes (PLAN.md §11.3 screen 2, decision 19).

Stored items are what the bank proposes (price changes; later new recurring payments and
missed payments), plus approved price changes whose what-if choices are still to make. They
are what the nav counts. Chores (spreadsheet follow-ups, redundant overrides, stale salaries,
what-ifs whose planned start has arrived) sit below, uncounted, so the count can reach zero.
"""

from dataclasses import asdict
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import SessionDep, UserDep, current_session
from app.api.transactions import TransactionOut, txn_out
from app.api.versions import today
from app.domain.auth import as_utc
from app.domain.checks import stale_inputs
from app.domain.models import BankTransaction, BudgetVersion, FollowUp, InboxItem, User
from app.domain.versions import redundant_overrides
from app.sync import banks
from app.sync import inbox as inbox_domain

router = APIRouter(prefix="/api/inbox", tags=["inbox"], dependencies=[Depends(current_session)])


class ApproveIn(BaseModel):
    effective_from: date | None = None


class DecisionIn(BaseModel):
    version_id: int
    decision: Literal["keep", "take", "set"]
    amount_pence: int | None = Field(None, ge=0)


class SnoozeIn(BaseModel):
    until: date | None = None


def _item(session: SessionDep, item_id: int) -> InboxItem:
    item = session.get(InboxItem, item_id)
    if item is None:
        raise HTTPException(404, "No such inbox item")
    return item


def _reminders(session: SessionDep, when: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    drafts = session.scalars(
        select(BudgetVersion).where(
            BudgetVersion.type == "what_if", BudgetVersion.status == "draft"
        )
    ).all()
    for v in drafts:
        if v.planned_start and v.planned_start <= when:
            out.append(
                {
                    "kind": "promotion_due",
                    "version_id": v.id,
                    "name": v.name,
                    "planned_start": v.planned_start.isoformat(),
                }
            )
        for ov in redundant_overrides(session, v, when):
            out.append(
                {
                    "kind": "redundant_override",
                    "version_id": v.id,
                    "name": v.name,
                    "line_id": ov.line_id,
                    "line_name": ov.line.name,
                }
            )
    for s in stale_inputs(session, when):
        out.append({"kind": "stale_input", "label": s.label, "as_of": s.as_of.isoformat()})
    return out


def _item_out(session: SessionDep, i: InboxItem) -> dict[str, Any]:
    payload = {k: v for k, v in i.payload_json.items() if k not in ("offered", "pending")}
    return {
        **payload,
        "id": i.id,
        "kind": i.type,
        "status": i.status,
        "line_id": i.line_id,
        "transaction_id": i.transaction_id,
        "created_at": i.created_at.isoformat(),
        "overriding": [asdict(o) for o in inbox_domain.overriding_what_ifs(session, i.line_id)]
        if i.line_id
        else [],
        "pending": [asdict(o) for o in inbox_domain.pending_what_ifs(session, i)],
    }


def _mine(item: InboxItem, user: User) -> bool:
    """A bank consent is its owner's to deal with; everything else is for both of us."""
    return item.type != "consent_expiring" or item.payload_json.get("owner_user_id") == user.id


def _watching(item: InboxItem | None) -> str | None:
    """When "I've cancelled it" was answered, if it was: the chore is watching since then."""
    if item is None or item.status != "approved" or item.resolved_at is None:
        return None
    return as_utc(item.resolved_at).date().isoformat()


def _waiting(session: SessionDep, when: date, user: User) -> list[InboxItem]:
    items = inbox_domain.awaiting_what_ifs(session) + inbox_domain.open_items(session, when)
    return [i for i in items if _mine(i, user)]


def _others_consents(session: SessionDep, when: date, user: User) -> list[dict[str, Any]]:
    """The other person's ending bank links: a quiet line, not something to act on."""
    return [
        {
            "bank": i.payload_json.get("bank"),
            "owner": i.payload_json.get("owner"),
            "expires_on": i.payload_json.get("expires_on"),
            "expired": i.payload_json.get("expired", False),
            "accounts": i.payload_json.get("accounts", []),
        }
        for i in inbox_domain.open_items(session, when)
        if not _mine(i, user)
    ]


@router.get("")
def inbox(session: SessionDep, user: UserDep) -> dict[str, Any]:
    when = today()
    items = _waiting(session, when, user)
    follow_ups = session.scalars(
        select(FollowUp).where(FollowUp.status == "open").order_by(FollowUp.id)
    ).all()
    # A "cancel" follow-up with a still-paying item open is that item now, not a chore too;
    # once answered "I've cancelled it", the chore shows it's being watched.
    paying = {
        i.payload_json.get("follow_up_id"): i
        for i in session.scalars(select(InboxItem).where(InboxItem.type == "still_paying"))
    }
    follow_ups = [
        f for f in follow_ups if not (f.id in paying and paying[f.id].status in ("open", "snoozed"))
    ]
    reminders = _reminders(session, when)
    return {
        "count": len(items),
        "chores_count": len(follow_ups) + len(reminders),
        "items": [_item_out(session, i) for i in items],
        "others_consents": _others_consents(session, when, user),
        "follow_ups": [
            {
                "id": f.id,
                "line_id": f.line_id,
                "line_name": f.line.name if f.line else None,
                "reason": f.reason,
                "note": f.note,
                "watching_since": _watching(paying.get(f.id)),
            }
            for f in follow_ups
        ],
        "reminders": reminders,
    }


@router.get("/count")
def inbox_count(session: SessionDep, user: UserDep) -> dict[str, int]:
    return {"count": len(_waiting(session, today(), user))}


@router.post("/{item_id}/approve")
def approve(item_id: int, body: ApproveIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    item = _item(session, item_id)
    try:
        overriding = inbox_domain.approve_price_change(session, item, user, body.effective_from)
    except inbox_domain.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return _item_out(session, item) | {"overriding": [asdict(o) for o in overriding]}


@router.post("/{item_id}/what-ifs", status_code=204)
def decide(item_id: int, body: DecisionIn, user: UserDep, session: SessionDep) -> None:
    item = _item(session, item_id)
    version = session.get(BudgetVersion, body.version_id)
    if version is None:
        raise HTTPException(404, "No such what-if")
    try:
        inbox_domain.decide_what_if(session, item, version, body.decision, user, body.amount_pence)
    except inbox_domain.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()


@router.post("/{item_id}/reject", status_code=204)
def reject(item_id: int, user: UserDep, session: SessionDep) -> None:
    try:
        inbox_domain.reject(session, _item(session, item_id), user)
    except inbox_domain.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()


@router.post("/{item_id}/snooze", status_code=204)
def snooze(item_id: int, body: SnoozeIn, user: UserDep, session: SessionDep) -> None:
    item = _item(session, item_id)
    until = body.until
    if item.type == "consent_expiring":
        # Only the owner, and never so long that the link lapses unnoticed.
        try:
            until = banks.consent_snooze_until(item, user, today())
        except banks.NotOwner as exc:
            raise HTTPException(403, str(exc)) from exc
        except banks.BankError as exc:
            raise HTTPException(422, str(exc)) from exc
    try:
        inbox_domain.snooze(session, item, user, until, today())
    except inbox_domain.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()


@router.post("/{item_id}/undo", status_code=204)
def undo(item_id: int, user: UserDep, session: SessionDep) -> None:
    try:
        inbox_domain.undo(session, _item(session, item_id), user)
    except inbox_domain.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()


# ---- new recurring payments, missed payments, still paying (Phase 5) ---------------------


class AcceptIn(BaseModel):
    """What to call the new line, and anything to change from what the bank showed."""

    name: str | None = Field(None, min_length=1, max_length=200)
    provider: str | None = Field(None, max_length=200)
    category: str | None = Field(None, max_length=100)
    account: str | None = None
    amount_pence: int | None = Field(None, ge=0)
    frequency: Literal["monthly", "annual", "quarterly", "weekly"] | None = None
    day_of_month: int | None = Field(None, ge=1, le=31)


def _act(item_id: int, session: SessionDep, action: Any, *args: Any) -> Any:
    try:
        result = action(session, _item(session, item_id), *args)
    except inbox_domain.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return result


@router.post("/{item_id}/accept")
def accept(item_id: int, body: AcceptIn, user: UserDep, session: SessionDep) -> dict[str, int]:
    """A new regular payment: add it to the live budget."""
    line = _act(item_id, session, inbox_domain.accept_recurring, user, body.model_dump())
    return {"line_id": line.id}


class LinkIn(BaseModel):
    line_id: int


@router.post("/{item_id}/link")
def link(item_id: int, body: LinkIn, user: UserDep, session: SessionDep) -> dict[str, int]:
    """A new regular payment that's a bill the budget already has: match it to that line."""
    line = _act(item_id, session, inbox_domain.link_recurring, user, body.line_id)
    return {"line_id": line.id}


@router.post("/{item_id}/ignore", status_code=204)
def ignore(item_id: int, user: UserDep, session: SessionDep) -> None:
    """A new regular payment that isn't a bill: never propose it again."""
    _act(item_id, session, inbox_domain.ignore_recurring, user)


@router.get("/{item_id}/payments", response_model=list[TransactionOut])
def payments(item_id: int, session: SessionDep) -> list[TransactionOut]:
    """A missed payment: the money out around its month that could be it."""
    try:
        options = inbox_domain.payment_options(session, _item(session, item_id))
    except inbox_domain.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [txn_out(t) for t in options]


class PaidIn(BaseModel):
    transaction_id: int
    always: bool = False


@router.post("/{item_id}/paid", status_code=204)
def paid(item_id: int, body: PaidIn, user: UserDep, session: SessionDep) -> None:
    """A missed payment that was paid: this is the payment the matcher didn't see."""
    txn = session.get(BankTransaction, body.transaction_id)
    if txn is None:
        raise HTTPException(404, "No such transaction")
    _act(item_id, session, inbox_domain.missed_paid, user, txn, body.always)


@router.post("/{item_id}/stopped", status_code=204)
def stopped(item_id: int, user: UserDep, session: SessionDep) -> None:
    """A missed payment because the bill has ended: out of the live budget from that month."""
    _act(item_id, session, inbox_domain.missed_stopped, user)


@router.post("/{item_id}/cancelled", status_code=204)
def cancelled(item_id: int, user: UserDep, session: SessionDep) -> None:
    """Still paying something marked to cancel: cancelled now."""
    _act(item_id, session, inbox_domain.still_paying_cancelled, user)


@router.post("/{item_id}/keep", status_code=204)
def keep(item_id: int, user: UserDep, session: SessionDep) -> None:
    """Still paying something marked to cancel: keeping it after all."""
    _act(item_id, session, inbox_domain.still_paying_keep, user)


# ---- planning reminders (Phases 6 and 7) -------------------------------------------------


@router.post("/{item_id}/take", status_code=204)
def take(item_id: int, user: UserDep, session: SessionDep) -> None:
    """A goal's needed amount, or the pay that landed: into the live budget."""
    item = _item(session, item_id)
    action = (
        inbox_domain.take_goal_amount if item.type == "goal_adjust" else inbox_domain.use_landed_pay
    )
    _act(item_id, session, action, user, today())


@router.post("/{item_id}/done", status_code=204)
def done(item_id: int, user: UserDep, session: SessionDep) -> None:
    """A reminder dealt with (a renewal moves on to next year)."""
    _act(item_id, session, inbox_domain.done, user)
