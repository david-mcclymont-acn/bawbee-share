"""Home Assistant (PLAN.md Phase 5): a read-only summary for REST sensors, and its tokens.

GET /api/ha/summary takes `Authorization: Bearer <token>`, a token made in Settings. Only
its hash is stored. It can read the summary and nothing else: no session, no writes.
"""

import hashlib
import secrets
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import notify
from app.api.deps import SessionDep, UserDep, current_session
from app.api.versions import today
from app.config import Settings, get_settings
from app.db import utcnow
from app.domain import audit, payday
from app.domain.auth import as_utc
from app.domain.models import BankConnection, HaToken, InboxItem
from app.domain.projection import month_projection
from app.domain.versions import calculate, live_version, resolve
from app.engine.money import round_pence
from app.sync import alerts

router = APIRouter(
    prefix="/api/ha", tags=["home assistant"], dependencies=[Depends(current_session)]
)
public = APIRouter(tags=["home assistant"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- the sensor ----------------------------------------------------------------------------


def summary(session: SessionDep, when: date) -> dict[str, Any]:
    """Everything a dashboard tile might want, read-only."""
    live = live_version(session)
    result = calculate(resolve(session, live, when))
    items = session.scalars(select(InboxItem).where(InboxItem.status == "open")).all()
    kinds: dict[str, int] = {}
    for item in items:
        kinds[item.type] = kinds.get(item.type, 0) + 1

    month = payday.month_key(when.year, when.month)
    run = payday.get_run(session, month)
    planned = payday.planned_transfers(session, month)
    done = sum(1 for t in run.transfers if t.done_at) if run else 0
    try:
        projected = month_projection(session, live, when.year, when.month)
        low: dict[str, Any] | None = {
            "pence": round_pence(projected.month.minimum),
            "day": projected.month.minimum_day.isoformat(),
        }
    except (payday.PaydayError, LookupError):
        low = None

    connections = session.scalars(select(BankConnection)).all()
    ends = [as_utc(c.consent_expires_at) for c in connections if c.consent_expires_at]
    syncs = [as_utc(c.last_sync_at) for c in connections if c.last_sync_at]
    return {
        "inbox": {"count": len(items), **kinds},
        "payday": {
            "date": payday.transfer_date(session, month).isoformat(),
            "transfers": len(run.transfers) if run else len(planned),
            "done": done,
        },
        "surplus_pence": {
            "household": round_pence(result.household_surplus),
            "joint": round_pence(result.joint_surplus),
            **{k: round_pence(v) for k, v in result.personal_surplus.items()},
        },
        "joint_low_point": low,
        "banks": {
            "links": len(connections),
            "ended": sum(1 for c in connections if c.status == "expired"),
            "next_consent_end": min(ends).date().isoformat() if ends else None,
            "oldest_sync": min(syncs).isoformat() if syncs else None,
        },
        "as_of": utcnow().isoformat(),
    }


@public.get("/api/ha/summary")
def sensor(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> dict[str, Any]:
    token = (authorization or "").removeprefix("Bearer ").strip()
    found = (
        session.scalars(select(HaToken).where(HaToken.token_hash == _hash(token))).first()
        if token
        else None
    )
    if found is None or found.revoked_at is not None:
        raise HTTPException(401, "A Home Assistant token from Settings is needed.")
    found.last_used_at = utcnow()
    data = summary(session, today())
    session.commit()
    return data


# ---- Settings: tokens and a test notification -----------------------------------------------


@router.get("")
def status(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    tokens = session.scalars(
        select(HaToken).where(HaToken.revoked_at.is_(None)).order_by(HaToken.id)
    ).all()
    return {
        "webhook": bool(settings.ha_webhook_url),
        "low_balance_pence": settings.ha_low_balance_pence,
        "summary_url": f"{settings.public_url.rstrip('/')}/api/ha/summary",
        "tokens": [
            {
                "id": t.id,
                "label": t.label,
                "created_at": as_utc(t.created_at).isoformat(),
                "last_used_at": as_utc(t.last_used_at).isoformat() if t.last_used_at else None,
            }
            for t in tokens
        ],
    }


class TokenIn(BaseModel):
    label: str = Field("Home Assistant", min_length=1, max_length=100)


@router.post("/tokens", status_code=201)
def make_token(body: TokenIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    """A new read-only token, shown this once."""
    token = "bwb_" + secrets.token_urlsafe(32)
    row = HaToken(token_hash=_hash(token), label=body.label, created_by=user.id)
    session.add(row)
    session.flush()
    audit.record(session, user, "ha_token", row.id, "create", None, {"label": body.label})
    session.commit()
    return {"id": row.id, "token": token}


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, user: UserDep, session: SessionDep) -> None:
    row = session.get(HaToken, token_id)
    if row is None or row.revoked_at is not None:
        raise HTTPException(404, "No such token.")
    row.revoked_at = utcnow()
    audit.record(session, user, "ha_token", row.id, "revoke")
    session.commit()


@router.post("/test", status_code=204)
def test_notification(settings: SettingsDep) -> None:
    error = notify.send(
        settings,
        "test",
        "Bawbee: test",
        "Notifications from A Wee Bawbee reach Home Assistant. "
        f"A sample amount: {alerts.pounds(119246)}.",
    )
    if error:
        raise HTTPException(502, error)
