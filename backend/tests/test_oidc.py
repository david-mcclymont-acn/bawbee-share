"""Optional OIDC sign-in against a mock provider (PLAN.md decision 27: CI needs no authentik)."""

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import get_oidc_client
from app.domain.models import User
from app.domain.oidc import OidcClient, OidcConfig
from tests.conftest import login

ISSUER = "https://auth.example.test/application/o/bawbee/"
CLIENT_ID = "bawbee"


@dataclass
class MockProvider:
    """Discovery, JWKS and a token endpoint that checks PKCE, like authentik does."""

    subject: str = "authentik-user-1"
    email: str | None = None
    email_verified: bool = False
    audience: str = CLIENT_ID
    wrong_nonce: bool = False
    key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    challenges: dict[str, str] = field(default_factory=dict)  # code -> PKCE challenge
    nonces: dict[str, str] = field(default_factory=dict)  # code -> nonce

    def authorize(self, url: str) -> tuple[str, str]:
        """Play the user signing in at the provider: returns (code, state)."""
        query = parse_qs(urlparse(url).query)
        assert query["code_challenge_method"] == ["S256"]
        code = f"code-{len(self.challenges)}"
        self.challenges[code] = query["code_challenge"][0]
        self.nonces[code] = query["nonce"][0]
        return code, query["state"][0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": "https://auth.example.test/authorize",
                    "token_endpoint": "https://auth.example.test/token",
                    "jwks_uri": "https://auth.example.test/jwks",
                },
            )
        if request.url.path == "/jwks":
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return httpx.Response(200, json={"keys": [jwk | {"kid": "k1", "use": "sig"}]})
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            code, verifier = form["code"][0], form["code_verifier"][0]
            expected = self.challenges.get(code)
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            if expected is None or digest.rstrip(b"=").decode() != expected:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims: dict[str, Any] = {
                "iss": ISSUER,
                "aud": self.audience,
                "sub": self.subject,
                "iat": now,
                "exp": now + 300,
                "nonce": "not-it" if self.wrong_nonce else self.nonces[code],
            }
            if self.email:
                claims |= {"email": self.email, "email_verified": self.email_verified}
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def provider(app: FastAPI) -> MockProvider:
    mock = MockProvider()
    config = OidcConfig(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="s3cret",
        redirect_uri="http://testserver/api/auth/oidc/callback",
        label="authentik",
    )
    http = httpx.Client(transport=httpx.MockTransport(mock.handler))
    app.dependency_overrides[get_oidc_client] = lambda: OidcClient(config, http)
    return mock


def _sign_in(app: FastAPI, provider: MockProvider) -> tuple[TestClient, httpx.Response]:
    browser = TestClient(app, follow_redirects=False)
    start = browser.get("/api/auth/oidc/start")
    assert start.status_code == 303
    code, state = provider.authorize(start.headers["location"])
    return browser, browser.get("/api/auth/oidc/callback", params={"code": code, "state": state})


def test_an_unlinked_identity_gets_no_account(
    app: FastAPI, provider: MockProvider, users: dict[str, User]
) -> None:
    browser, done = _sign_in(app, provider)
    assert done.status_code == 303
    assert done.headers["location"].startswith("/login?error=")
    assert "bawbee_session" not in done.headers.get("set-cookie", "")
    assert browser.get("/api/auth/me").status_code == 401


def test_link_from_settings_then_sign_in_as_the_same_user(
    app: FastAPI, provider: MockProvider, users: dict[str, User], session: Session
) -> None:
    person_1 = login(app, "person_1")
    redirect = person_1.post("/api/auth/oidc/link").json()["redirect"]
    code, state = provider.authorize(redirect)
    linked = person_1.get(
        "/api/auth/oidc/callback", params={"code": code, "state": state}, follow_redirects=False
    )
    assert linked.headers["location"] == "/settings?linked=1"
    session.refresh(users["person_1"])
    assert users["person_1"].oidc_subject == "authentik-user-1"

    browser, done = _sign_in(app, provider)
    assert (done.status_code, done.headers["location"]) == (303, "/")
    me = browser.get("/api/auth/me").json()
    assert (me["username"], me["oidc_linked"]) == ("person_1", True)
    sessions = browser.get("/api/auth/sessions").json()
    assert next(s for s in sessions if s["current"])["method"] == "oidc"

    # Unlinking puts it back to an unknown identity.
    assert person_1.delete("/api/auth/oidc/link").status_code == 204
    _, again = _sign_in(app, provider)
    assert again.headers["location"].startswith("/login?error=")


def test_a_verified_matching_email_links_on_first_sign_in(
    app: FastAPI, provider: MockProvider, users: dict[str, User]
) -> None:
    provider.subject, provider.email = "authentik-person_2", "person_2@example.com"
    provider.email_verified = False
    _, refused = _sign_in(app, provider)
    assert refused.headers["location"].startswith("/login?error=")

    provider.email_verified = True
    browser, done = _sign_in(app, provider)
    assert done.headers["location"] == "/"
    assert browser.get("/api/auth/me").json()["username"] == "person_2"


@pytest.mark.parametrize("problem", ["wrong_nonce", "wrong_audience"])
def test_tokens_that_dont_verify_are_refused(
    app: FastAPI, provider: MockProvider, users: dict[str, User], session: Session, problem: str
) -> None:
    users["person_1"].oidc_subject = provider.subject
    session.commit()
    if problem == "wrong_nonce":
        provider.wrong_nonce = True
    else:
        provider.audience = "someone-else"
    browser, done = _sign_in(app, provider)
    assert done.headers["location"].startswith("/login?error=")
    assert browser.get("/api/auth/me").status_code == 401


def test_a_sign_in_link_works_once(
    app: FastAPI, provider: MockProvider, users: dict[str, User], session: Session
) -> None:
    users["person_1"].oidc_subject = provider.subject
    session.commit()
    browser = TestClient(app, follow_redirects=False)
    code, state = provider.authorize(browser.get("/api/auth/oidc/start").headers["location"])
    first = browser.get("/api/auth/oidc/callback", params={"code": code, "state": state})
    assert first.headers["location"] == "/"
    replay = TestClient(app, follow_redirects=False).get(
        "/api/auth/oidc/callback", params={"code": code, "state": state}
    )
    assert replay.headers["location"].startswith("/login?error=")


def test_oidc_is_off_unless_configured(app: FastAPI, users: dict[str, User]) -> None:
    assert TestClient(app).get("/api/auth/config").json()["oidc"]["enabled"] is False
    r = TestClient(app, follow_redirects=False).get("/api/auth/oidc/start")
    assert r.status_code == 404
