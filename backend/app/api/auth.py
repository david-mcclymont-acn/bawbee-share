"""Sign-in API: local passwords by default, OIDC when configured (PLAN.md decision 27)."""

from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.deps import SESSION_COOKIE, AuthDep, SessionDep, UserDep, client_ip
from app.config import Settings, get_settings
from app.domain import auth as auth_domain
from app.domain.models import AuthSession, Person
from app.domain.oidc import OidcClient, OidcConfig, OidcError, unlink

router = APIRouter(prefix="/api/auth", tags=["auth"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_oidc_client(settings: SettingsDep) -> OidcClient | None:
    config = OidcConfig.from_settings(settings)
    return OidcClient(config) if config else None


OidcDep = Annotated[OidcClient | None, Depends(get_oidc_client)]


class OidcInfo(BaseModel):
    enabled: bool
    label: str


class AuthConfig(BaseModel):
    local: bool
    oidc: OidcInfo


class LoginIn(BaseModel):
    username: str
    password: str


class Me(BaseModel):
    id: int
    username: str
    display_name: str
    person: str | None
    oidc_linked: bool
    has_password: bool
    csrf_token: str


class SessionOut(BaseModel):
    id: int
    method: str
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def _me(session: SessionDep, auth: AuthSession) -> Me:
    user = auth.user
    person = session.get(Person, user.person_id) if user.person_id else None
    return Me(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        person=person.key if person else None,
        oidc_linked=user.oidc_subject is not None,
        has_password=user.password_hash is not None,
        csrf_token=auth.csrf_token,
    )


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.get("/config", response_model=AuthConfig)
def config(settings: SettingsDep) -> AuthConfig:
    return AuthConfig(
        local=settings.auth_local,
        oidc=OidcInfo(enabled=settings.oidc_enabled, label=settings.oidc_label),
    )


@router.post("/login", response_model=Me)
def login(
    body: LoginIn, request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> Me:
    if not settings.auth_local:
        raise HTTPException(404, "Password sign-in is turned off.")
    ip = client_ip(request)
    try:
        user = auth_domain.authenticate(session, body.username, body.password, ip)
    except auth_domain.RateLimited as exc:
        session.commit()
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after_seconds)}
        ) from exc
    except auth_domain.BadCredentials as exc:
        session.commit()  # keep the failed attempt for rate limiting
        raise HTTPException(401, str(exc)) from exc
    new = auth_domain.start_session(session, user, "local", ip, request.headers.get("user-agent"))
    session.commit()
    _set_session_cookie(response, new.token, settings)
    return _me(session, new.session)


@router.post("/logout", status_code=204)
def logout(auth: AuthDep, response: Response, session: SessionDep) -> None:
    auth_domain.revoke(session, auth, auth.user, "logout")
    session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=Me)
def me(auth: AuthDep, session: SessionDep) -> Me:
    return _me(session, auth)


@router.get("/sessions", response_model=list[SessionOut])
def sessions(auth: AuthDep, session: SessionDep) -> list[SessionOut]:
    return [
        SessionOut(
            id=s.id,
            method=s.method,
            created_at=auth_domain.as_utc(s.created_at),
            last_seen_at=auth_domain.as_utc(s.last_seen_at),
            ip=s.ip,
            user_agent=s.user_agent,
            current=s.id == auth.id,
        )
        for s in auth_domain.active_sessions(session, auth.user)
    ]


@router.post("/sessions/revoke-others", status_code=204)
def revoke_other_sessions(auth: AuthDep, session: SessionDep) -> None:
    """Sign out everywhere except this browser."""
    auth_domain.revoke_others(session, auth.user, auth, auth.user)
    session.commit()


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(session_id: int, auth: AuthDep, session: SessionDep) -> None:
    target = session.get(AuthSession, session_id)
    if target is None or target.user_id != auth.user_id:
        raise HTTPException(404, "No such session.")
    auth_domain.revoke(session, target, auth.user)
    session.commit()


@router.post("/password", status_code=204)
def change_password(body: PasswordIn, auth: AuthDep, session: SessionDep) -> None:
    try:
        auth_domain.change_password(
            session, auth.user, body.current_password, body.new_password, auth
        )
    except auth_domain.BadCredentials as exc:
        raise HTTPException(400, "Your current password isn't right.") from exc
    except auth_domain.WeakPassword as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()


class ConfirmIn(BaseModel):
    password: str


@router.post("/confirm", status_code=204)
def confirm(body: ConfirmIn, request: Request, auth: AuthDep, session: SessionDep) -> None:
    """Type the password again before linking or disconnecting a bank (PLAN.md §13)."""
    try:
        auth_domain.confirm_password(session, auth, body.password, client_ip(request))
    except auth_domain.RateLimited as exc:
        session.commit()
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after_seconds)}
        ) from exc
    except auth_domain.BadCredentials as exc:
        session.commit()
        raise HTTPException(400, "That password isn't right.") from exc
    except auth_domain.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()


# ---- OIDC ------------------------------------------------------------------------------


def _require_oidc(client: OidcClient | None) -> OidcClient:
    if client is None:
        raise HTTPException(404, "Single sign-on isn't set up.")
    return client


@router.get("/oidc/start")
def oidc_start(session: SessionDep, client: OidcDep) -> RedirectResponse:
    url = _require_oidc(client).start(session)
    session.commit()
    return RedirectResponse(url, status_code=303)


@router.post("/oidc/link")
def oidc_link(user: UserDep, session: SessionDep, client: OidcDep) -> dict[str, str]:
    """Start linking the signed-in user; the browser then follows `redirect`."""
    url = _require_oidc(client).start(session, link_user=user)
    session.commit()
    return {"redirect": url}


@router.delete("/oidc/link", status_code=204)
def oidc_unlink(user: UserDep, session: SessionDep, settings: SettingsDep) -> None:
    try:
        unlink(session, user, settings.auth_local)
    except OidcError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()


@router.get("/oidc/callback")
def oidc_callback(
    request: Request,
    session: SessionDep,
    client: OidcDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    oidc = _require_oidc(client)
    if error or not code or not state:
        return RedirectResponse(f"/login?error={quote(error or 'cancelled')}", status_code=303)
    try:
        user, linked = oidc.finish(session, code, state)
    except OidcError as exc:
        session.commit()
        return RedirectResponse(f"/login?error={quote(str(exc))}", status_code=303)
    if linked:
        session.commit()
        return RedirectResponse("/settings?linked=1", status_code=303)
    new = auth_domain.start_session(
        session, user, "oidc", client_ip(request), request.headers.get("user-agent")
    )
    session.commit()
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, new.token, settings)
    return response
