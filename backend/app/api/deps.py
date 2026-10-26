"""Request dependencies: the database session, the signed-in user, and CSRF on writes."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_session
from app.domain.auth import session_for_token
from app.domain.models import AuthSession, User

SESSION_COOKIE = "bawbee_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SessionDep = Annotated[Session, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    # uvicorn runs with --proxy-headers behind nginx, so this is the real client address.
    return request.client.host if request.client else None


def current_session(request: Request, session: SessionDep) -> AuthSession:
    token = request.cookies.get(SESSION_COOKIE)
    auth = session_for_token(session, token) if token else None
    if auth is None:
        raise HTTPException(401, "Please sign in.")
    session.commit()  # persists the sliding expiry, if it moved
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent, auth.csrf_token):
            raise HTTPException(
                403, "This request is missing its security token. Reload and try again."
            )
    return auth


def current_user(auth: Annotated[AuthSession, Depends(current_session)]) -> User:
    return auth.user


AuthDep = Annotated[AuthSession, Depends(current_session)]
UserDep = Annotated[User, Depends(current_user)]
