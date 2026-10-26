"""Bank connections (PLAN.md §8, §13): link, map, sync and disconnect.

Linking, re-linking and disconnecting need the password typed in the last 12 hours; if it
wasn't, these answer 428 and the browser asks for it (POST /api/auth/confirm).
"""

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import crypto
from app.api.deps import SESSION_COOKIE, AuthDep, SessionDep, UserDep, current_session
from app.config import Settings, get_settings
from app.domain.auth import recently_confirmed, session_for_token
from app.domain.models import Account, BankAccountLink, BankConnection
from app.providers.base import ProviderError
from app.sync import banks

router = APIRouter(prefix="/api/banks", tags=["banks"], dependencies=[Depends(current_session)])
# The bank's redirect back, which may arrive before anyone has signed in on this browser.
public = APIRouter(tags=["banks"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _confirmed(auth: AuthDep, settings: SettingsDep) -> None:
    if not recently_confirmed(auth, settings.reauth_hours):
        raise HTTPException(428, "Type your password again to change bank links.")


Confirmed = Depends(_confirmed)


def _connection(session: SessionDep, connection_id: int) -> BankConnection:
    connection = session.get(BankConnection, connection_id)
    if connection is None:
        raise HTTPException(404, "No such bank link.")
    return connection


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, banks.NotOwner):
        return HTTPException(403, str(exc))
    return HTTPException(422, str(exc))


def _view(
    session: SessionDep, settings: Settings, connection: BankConnection, user: Any
) -> dict[str, Any]:
    return banks.connection_view(session, connection, user, settings.manual_sync_minutes)


def _psu(request: Request) -> dict[str, str]:
    """The person is here (Sync now), so the bank needn't count it as unattended."""
    headers = {"Psu-User-Agent": request.headers.get("user-agent", "")}
    if request.client:
        headers["Psu-Ip-Address"] = request.client.host
    return headers


@router.get("")
def overview(
    auth: AuthDep, user: UserDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    connections = session.scalars(select(BankConnection).order_by(BankConnection.id)).all()
    fed = {
        link.account_id: link
        for link in session.scalars(
            select(BankAccountLink).where(BankAccountLink.account_id.is_not(None))
        )
    }
    return {
        "can_link": crypto.can_encrypt(settings),
        # Linking and disconnecting need the password typed in the last 12 hours.
        "confirmed": recently_confirmed(auth, settings.reauth_hours),
        "providers": {
            "enable_banking": settings.enable_banking_enabled,
            "starling": True,
            "monzo": settings.monzo_enabled,
            "demo": settings.banks_demo,
        },
        # Why a provider isn't available (None when it is), so the screen can say so.
        "setup": {
            "monzo": settings.monzo_problem,
            "enable_banking": settings.enable_banking_problem,
        },
        "connections": [_view(session, settings, c, user) for c in connections],
        "unlinked": [
            {"key": a.key, "name": a.name, "provider": a.provider}
            for a in session.scalars(select(Account).order_by(Account.id))
            if a.id not in fed and a.role != "dormant"
        ],
    }


@router.get("/institutions")
def institutions(settings: SettingsDep) -> list[dict[str, Any]]:
    """Banks that can be linked through Enable Banking (and the demo bank, if on)."""
    try:
        return [
            {
                "name": b.name,
                "country": b.country,
                "provider": b.provider,
                "max_consent_days": b.max_consent_days,
                "beta": b.beta,
            }
            for b in banks.available_banks(settings)
        ]
    except (banks.BankError, ProviderError) as exc:
        raise HTTPException(502, str(exc)) from exc


class LinkIn(BaseModel):
    bank: str = Field(min_length=1, max_length=100)
    country: str = Field("GB", min_length=2, max_length=2)
    relink_connection_id: int | None = None


@router.post("/link", dependencies=[Confirmed])
def link(body: LinkIn, user: UserDep, session: SessionDep, settings: SettingsDep) -> dict[str, str]:
    choices = {(b.name, b.country): b for b in banks.available_banks(settings)}
    bank = choices.get((body.bank, body.country))
    if bank is None:
        raise HTTPException(404, "That bank isn't available to link.")
    relink = _connection(session, body.relink_connection_id) if body.relink_connection_id else None
    try:
        url = banks.start_link(session, settings, user, bank, relink)
    except (banks.BankError, ProviderError) as exc:
        raise _fail(exc) from exc
    session.commit()
    return {"url": url}


def _signed_in(request: Request, session: SessionDep) -> Any:
    """The signed-in user, or None: the bank may hand back to a browser that isn't signed in."""
    token = request.cookies.get(SESSION_COOKIE)
    auth = session_for_token(session, token) if token else None
    return auth.user if auth else None


@public.get("/api/banks/callback", include_in_schema=False)
def callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    state: str = "",
    code: str = "",
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Where the bank sends the browser back. Lands on the Banks screen either way.

    On a phone the bank can hand back to the browser rather than the installed app, where
    nobody is signed in: then it's sign in first, and back here with the same code.
    """
    user = _signed_in(request, session)
    if user is None:
        back = f"/api/banks/callback?{request.url.query}"
        return RedirectResponse(f"/login?next={quote(back)}", status_code=303)
    if error or not code:
        reason = error_description or error or "The bank sign-in was cancelled."
        session.rollback()
        return RedirectResponse(f"/banks?error={_q(reason)}", status_code=303)
    try:
        result = banks.complete_link(session, settings, user, state, code)
    except (banks.BankError, ProviderError, crypto.SecretsUnavailable) as exc:
        session.commit()  # the flow is spent either way
        return RedirectResponse(f"/banks?error={_q(str(exc))}", status_code=303)
    session.commit()
    if result.pending:
        # Monzo: nothing can be read until access is approved in the Monzo app.
        return RedirectResponse(f"/banks?approve={result.connection.id}", status_code=303)
    if result.relinked and not result.new_accounts:
        banks.sync_connection(session, settings, result.connection, "link")
        session.commit()
        return RedirectResponse(f"/banks?relinked={result.connection.id}", status_code=303)
    return RedirectResponse(f"/banks?linked={result.connection.id}", status_code=303)


def _q(text: str) -> str:
    return quote(text[:300])


@router.post("/{connection_id}/continue")
def continue_link(
    connection_id: int, user: UserDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    """Access approved in the Monzo app: fetch the accounts. `map` asks which they feed."""
    connection = _connection(session, connection_id)
    try:
        result = banks.continue_link(session, settings, connection, user)
    except (banks.BankError, ProviderError, crypto.SecretsUnavailable) as exc:
        raise _fail(exc) from exc
    session.commit()
    if result.relinked and not result.new_accounts:
        # A re-link of accounts already mapped: sync now, inside Monzo's full-history window.
        banks.sync_connection(session, settings, connection, "link")
        session.commit()
    return {
        "connection": _view(session, settings, connection, user),
        "map": not result.relinked or result.new_accounts,
    }


class StarlingIn(BaseModel):
    token: str = Field(min_length=1, max_length=2000)
    replace_connection_id: int | None = None


@router.post("/starling", dependencies=[Confirmed], status_code=201)
def starling(
    body: StarlingIn, user: UserDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    replace = (
        _connection(session, body.replace_connection_id) if body.replace_connection_id else None
    )
    try:
        connection = banks.add_starling(session, settings, user, body.token, replace)
    except (banks.BankError, ProviderError) as exc:
        raise _fail(exc) from exc
    session.commit()
    return _view(session, settings, connection, user)


class MappingIn(BaseModel):
    accounts: dict[str, str | None]  # bank account uid -> our account key, or None


@router.put("/{connection_id}/accounts")
def map_accounts(
    connection_id: int, body: MappingIn, user: UserDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    connection = _connection(session, connection_id)
    try:
        banks.set_mapping(session, connection, user, body.accounts)
    except banks.BankError as exc:
        raise _fail(exc) from exc
    session.commit()
    # Bring the newly mapped accounts in straight away.
    banks.sync_connection(session, settings, connection, "link")
    session.commit()
    return _view(session, settings, connection, user)


@router.post("/{connection_id}/sync")
def sync(
    connection_id: int, request: Request, user: UserDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    connection = _connection(session, connection_id)
    try:
        run = banks.sync_now(session, settings, connection, user, _psu(request))
    except banks.BankError as exc:
        raise _fail(exc) from exc
    session.commit()
    if run.status != "ok":
        raise HTTPException(502, run.error or "The sync didn't finish.")
    return _view(session, settings, connection, user)


@router.delete("/{connection_id}", dependencies=[Confirmed])
def disconnect(
    connection_id: int,
    user: UserDep,
    session: SessionDep,
    settings: SettingsDep,
    delete_transactions: bool = False,
) -> dict[str, int]:
    connection = _connection(session, connection_id)
    try:
        removed = banks.disconnect(session, settings, connection, user, delete_transactions)
    except banks.BankError as exc:
        raise _fail(exc) from exc
    session.commit()
    return {"transactions_deleted": removed}
