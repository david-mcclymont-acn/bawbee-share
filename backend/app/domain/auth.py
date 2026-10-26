"""Logins: local passwords by default, sessions, rate limiting (PLAN.md §12, §13).

Passwords are Argon2id hashes. A session cookie carries a random token; the database keeps
only its SHA-256, so a stolen backup can't be replayed as a login.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.domain import audit
from app.domain.models import AuthSession, LoginAttempt, Person, User

MIN_PASSWORD_LENGTH = 12
ATTEMPT_WINDOW = timedelta(minutes=15)
USER_FAILURES_BEFORE_LOCK = 5
IP_FAILURES_BEFORE_LOCK = 20
MAX_LOCK = timedelta(minutes=15)
# Refresh a session's sliding expiry at most this often, to avoid a write per request.
TOUCH_EVERY = timedelta(minutes=5)

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
# Verified against when the username doesn't exist, so timing doesn't reveal which ones do.
_DUMMY_HASH = _hasher.hash("not-a-real-password-just-for-timing")


class AuthError(Exception):
    pass


class BadCredentials(AuthError):
    def __init__(self) -> None:
        super().__init__("That username and password don't match.")


class WeakPassword(AuthError):
    pass


class RateLimited(AuthError):
    def __init__(self, retry_after: timedelta) -> None:
        self.retry_after_seconds = max(1, int(retry_after.total_seconds()) + 1)
        minutes = -(-self.retry_after_seconds // 60)
        super().__init__(
            f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."
        )


def as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything here is stored as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def normalise_username(username: str) -> str:
    return username.strip().lower()


# ---- passwords ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(password)


def _verify(user: User | None, password: str) -> bool:
    try:
        ok = _hasher.verify(
            user.password_hash if user and user.password_hash else _DUMMY_HASH, password
        )
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return bool(ok and user and user.password_hash)


def create_user(
    session: Session,
    username: str,
    display_name: str,
    person_key: str | None,
    password: str | None,
    actor: User | None,
    email: str | None = None,
) -> User:
    username = normalise_username(username)
    if session.scalars(select(User).where(User.username == username)).first():
        raise AuthError(f"There's already a user called {username!r}.")
    person_id = None
    if person_key is not None:
        person = session.scalars(select(Person).where(Person.key == person_key)).first()
        if person is None:
            raise AuthError(f"No person {person_key!r}.")
        person_id = person.id
    user = User(
        username=username,
        display_name=display_name,
        person_id=person_id,
        email=email,
        password_hash=hash_password(password) if password else None,
    )
    session.add(user)
    session.flush()
    audit.record(
        session,
        actor,
        "user",
        user.id,
        "create",
        None,
        {"username": username, "person": person_key},
    )
    return user


def set_password(session: Session, user: User, password: str, actor: User | None) -> None:
    user.password_hash = hash_password(password)
    session.flush()
    audit.record(session, actor, "user", user.id, "set_password")


def change_password(
    session: Session, user: User, current: str, new: str, keep_session: AuthSession
) -> None:
    """Change your own password; every other session of yours is signed out."""
    if not _verify(user, current):
        raise BadCredentials()
    set_password(session, user, new, user)
    revoke_others(session, user, keep_session, user)


# ---- rate limiting -----------------------------------------------------------------------


def _check_rate_limit(session: Session, username: str, ip: str | None, now: datetime) -> None:
    since = now - ATTEMPT_WINDOW
    recent = session.scalars(
        select(LoginAttempt)
        .where(LoginAttempt.username == username, LoginAttempt.at >= since)
        .order_by(LoginAttempt.at)
    ).all()
    # Count failures since the last success: each one past the threshold doubles the wait.
    failures: list[LoginAttempt] = []
    for attempt in recent:
        failures = [] if attempt.success else [*failures, attempt]
    if len(failures) >= USER_FAILURES_BEFORE_LOCK:
        lock = min(timedelta(minutes=2 ** (len(failures) - USER_FAILURES_BEFORE_LOCK)), MAX_LOCK)
        until = as_utc(failures[-1].at) + lock
        if now < until:
            raise RateLimited(until - now)

    if ip is not None:
        ip_failures = session.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(LoginAttempt.ip == ip, LoginAttempt.at >= since, LoginAttempt.success.is_(False))
        )
        if (ip_failures or 0) >= IP_FAILURES_BEFORE_LOCK:
            oldest = session.scalar(
                select(func.min(LoginAttempt.at)).where(
                    LoginAttempt.ip == ip, LoginAttempt.at >= since, LoginAttempt.success.is_(False)
                )
            )
            raise RateLimited(as_utc(oldest) + ATTEMPT_WINDOW - now if oldest else ATTEMPT_WINDOW)


def authenticate(
    session: Session, username: str, password: str, ip: str | None, now: datetime | None = None
) -> User:
    now = now or utcnow()
    username = normalise_username(username)
    _check_rate_limit(session, username, ip, now)
    user = session.scalars(select(User).where(User.username == username)).first()
    ok = _verify(user, password) and user is not None and user.disabled_at is None
    session.add(LoginAttempt(username=username, ip=ip, success=ok, at=now))
    if not ok or user is None:
        audit.record(
            session,
            None,
            "user",
            user.id if user else None,
            "login_failed",
            None,
            {"username": username, "ip": ip},
        )
        session.flush()
        raise BadCredentials()
    if _hasher.check_needs_rehash(user.password_hash or ""):
        user.password_hash = _hasher.hash(password)
    session.flush()
    return user


def recently_confirmed(auth: AuthSession, hours: int, now: datetime | None = None) -> bool:
    """Signed in, or typed the password again, within `hours` (bank links need it, §13)."""
    now = now or utcnow()
    latest = max(as_utc(auth.created_at), as_utc(auth.confirmed_at or auth.created_at))
    return now - latest <= timedelta(hours=hours)


def confirm_password(session: Session, auth: AuthSession, password: str, ip: str | None) -> None:
    """Type the password again in this session; rate-limited and logged like a sign-in."""
    if not auth.user.password_hash:
        raise AuthError("Sign out and back in to confirm it's you.")
    authenticate(session, auth.user.username, password, ip)
    auth.confirmed_at = utcnow()
    session.flush()
    audit.record(session, auth.user, "auth_session", auth.id, "confirm_password")


# ---- sessions ----------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class NewSession:
    token: str
    session: AuthSession


def start_session(
    session: Session,
    user: User,
    method: str,
    ip: str | None,
    user_agent: str | None,
    now: datetime | None = None,
) -> NewSession:
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    auth = AuthSession(
        user_id=user.id,
        token_hash=_token_hash(token),
        csrf_token=secrets.token_urlsafe(32),
        method=method,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=get_settings().session_days),
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(auth)
    user.last_login_at = now
    session.flush()
    audit.record(
        session, user, "auth_session", auth.id, "login", None, {"method": method, "ip": ip}
    )
    return NewSession(token, auth)


def session_for_token(
    session: Session, token: str, now: datetime | None = None
) -> AuthSession | None:
    now = now or utcnow()
    auth = session.scalars(
        select(AuthSession).where(AuthSession.token_hash == _token_hash(token))
    ).first()
    if (
        auth is None
        or auth.revoked_at is not None
        or as_utc(auth.expires_at) <= now
        or auth.user.disabled_at is not None
    ):
        return None
    if now - as_utc(auth.last_seen_at) >= TOUCH_EVERY:
        auth.last_seen_at = now
        auth.expires_at = now + timedelta(days=get_settings().session_days)
        session.flush()
    return auth


def revoke(session: Session, auth: AuthSession, actor: User | None, action: str = "revoke") -> None:
    if auth.revoked_at is None:
        auth.revoked_at = utcnow()
        session.flush()
        audit.record(session, actor, "auth_session", auth.id, action)


def revoke_others(session: Session, user: User, keep: AuthSession, actor: User | None) -> None:
    for other in session.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.id != keep.id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        revoke(session, other, actor)


def active_sessions(session: Session, user: User, now: datetime | None = None) -> list[AuthSession]:
    now = now or utcnow()
    return [
        s
        for s in session.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .order_by(AuthSession.last_seen_at.desc())
        )
        if as_utc(s.expires_at) > now
    ]
