"""Local-account sign-in, sessions, CSRF and rate limiting (PLAN.md §12, §13)."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import utcnow
from app.domain import auth
from app.domain.models import AuditLog, AuthSession, LoginAttempt, User
from tests.conftest import PASSWORD, login


def _attempt(app: FastAPI, username: str, password: str) -> int:
    response = TestClient(app).post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return int(response.status_code)


def test_login_sets_an_httponly_session_cookie(app: FastAPI, users: dict[str, User]) -> None:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "PERSON_1", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "bawbee_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    me = c.get("/api/auth/me").json()
    assert (me["username"], me["person"], me["oidc_linked"]) == ("person_1", "person_1", False)


def test_session_cookie_is_secure_by_default() -> None:
    assert Settings().cookie_secure is True


def test_wrong_password_and_unknown_user_look_the_same(
    app: FastAPI, users: dict[str, User]
) -> None:
    c = TestClient(app)
    wrong = c.post("/api/auth/login", json={"username": "person_1", "password": "nope nope nope"})
    nobody = c.post("/api/auth/login", json={"username": "fergus", "password": "nope nope nope"})
    assert wrong.status_code == nobody.status_code == 401
    assert wrong.json() == nobody.json()


def test_passwords_are_argon2id_hashes(users: dict[str, User]) -> None:
    assert (users["person_1"].password_hash or "").startswith("$argon2id$")
    with pytest.raises(auth.WeakPassword):
        auth.hash_password("too short")


def test_failed_logins_lock_the_username_with_backoff(
    app: FastAPI, session: Session, users: dict[str, User]
) -> None:
    for _ in range(auth.USER_FAILURES_BEFORE_LOCK):
        assert _attempt(app, "person_1", "wrong password!") == 401
    # Locked: even the right password is refused for now.
    r = TestClient(app).post("/api/auth/login", json={"username": "person_1", "password": PASSWORD})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    # Person 2 isn't affected.
    assert _attempt(app, "person_2", PASSWORD) == 200

    # The lock lifts after a minute; another failure then locks for two.
    later = utcnow() + timedelta(minutes=1, seconds=5)
    with pytest.raises(auth.BadCredentials):
        auth.authenticate(session, "person_1", "wrong again!!", "testclient", now=later)
    with pytest.raises(auth.RateLimited) as locked:
        auth.authenticate(session, "person_1", PASSWORD, "testclient", now=later)
    assert 100 < locked.value.retry_after_seconds <= 121
    user = auth.authenticate(
        session, "person_1", PASSWORD, "testclient", now=later + timedelta(minutes=2, seconds=5)
    )
    assert user.username == "person_1"


def test_many_failures_from_one_address_lock_that_address(
    app: FastAPI, session: Session, users: dict[str, User]
) -> None:
    now = utcnow()
    session.add_all(
        LoginAttempt(username=f"guess{i}", ip="testclient", success=False, at=now)
        for i in range(auth.IP_FAILURES_BEFORE_LOCK)
    )
    session.commit()
    assert _attempt(app, "person_2", PASSWORD) == 429


def test_writes_need_the_csrf_token(app: FastAPI, client: TestClient, imported: object) -> None:
    body = {"name": "Back to work", "base_version_id": 1}
    no_token = TestClient(app, cookies=client.cookies)
    assert no_token.post("/api/versions", json=body).status_code == 403
    no_token.headers["X-CSRF-Token"] = "forged"
    assert no_token.post("/api/versions", json=body).status_code == 403
    assert client.post("/api/versions", json=body).status_code == 201
    # Reads don't need it.
    assert no_token.get("/api/versions").status_code == 200


def test_sessions_can_be_listed_and_revoked(app: FastAPI, users: dict[str, User]) -> None:
    laptop, phone = login(app, "person_1"), login(app, "person_1")
    listed = laptop.get("/api/auth/sessions").json()
    assert len(listed) == 2
    phone_id = next(s["id"] for s in listed if not s["current"])

    assert laptop.delete(f"/api/auth/sessions/{phone_id}").status_code == 204
    assert phone.get("/api/auth/me").status_code == 401
    assert laptop.get("/api/auth/me").status_code == 200

    # Person 2 can't revoke Person 1's sessions.
    person_2 = login(app, "person_2")
    mine = laptop.get("/api/auth/sessions").json()[0]["id"]
    assert person_2.delete(f"/api/auth/sessions/{mine}").status_code == 404

    assert laptop.post("/api/auth/logout").status_code == 204
    assert laptop.get("/api/auth/me").status_code == 401


def test_changing_password_signs_out_other_sessions(app: FastAPI, users: dict[str, User]) -> None:
    laptop, phone = login(app, "person_1"), login(app, "person_1")
    wrong = laptop.post(
        "/api/auth/password",
        json={"current_password": "not it at all", "new_password": "a brand new long one"},
    )
    assert wrong.status_code == 400
    weak = laptop.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert weak.status_code == 400
    ok = laptop.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new long one"},
    )
    assert ok.status_code == 204
    assert laptop.get("/api/auth/me").status_code == 200
    assert phone.get("/api/auth/me").status_code == 401
    assert _attempt(app, "person_1", PASSWORD) == 401
    assert _attempt(app, "person_1", "a brand new long one") == 200


def test_expired_and_disabled_sessions_stop_working(
    app: FastAPI, session: Session, users: dict[str, User]
) -> None:
    c = login(app, "person_1")
    stored = session.scalars(select(AuthSession)).one()
    stored.expires_at = utcnow() - timedelta(seconds=1)
    session.commit()
    assert c.get("/api/auth/me").status_code == 401

    c = login(app, "person_2")
    users["person_2"].disabled_at = utcnow()
    session.commit()
    assert c.get("/api/auth/me").status_code == 401
    assert _attempt(app, "person_2", PASSWORD) == 401


def test_sign_in_events_are_audited(app: FastAPI, session: Session, users: dict[str, User]) -> None:
    _attempt(app, "person_1", "wrong password!")
    login(app, "person_1").post("/api/auth/logout")
    actions = session.scalars(
        select(AuditLog.action).where(AuditLog.entity.in_(["user", "auth_session"]))
    ).all()
    assert {"login_failed", "login", "logout"} <= set(actions)


def test_password_login_can_be_switched_off(
    app: FastAPI, settings: Settings, users: dict[str, User]
) -> None:
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"auth_local": False}
    )
    assert _attempt(app, "person_1", PASSWORD) == 404
    config = TestClient(app).get("/api/auth/config").json()
    assert config == {"local": False, "oidc": {"enabled": False, "label": "authentik"}}


def test_sign_out_everywhere_else(app: FastAPI, users: dict[str, User]) -> None:
    laptop, phone, tablet = login(app, "person_1"), login(app, "person_1"), login(app, "person_1")
    assert laptop.post("/api/auth/sessions/revoke-others").status_code == 204
    assert laptop.get("/api/auth/me").status_code == 200
    assert phone.get("/api/auth/me").status_code == 401
    assert tablet.get("/api/auth/me").status_code == 401
