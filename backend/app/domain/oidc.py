"""Optional OIDC sign-in (e.g. authentik): authorization code + PKCE (PLAN.md §12).

Built on discovery, so any standard provider works. ID tokens are verified with PyJWT
against the provider's JWKS. An unknown subject never gets an account: it must already be
linked to a user (from Settings, or by a verified email that matches).
"""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.domain import audit
from app.domain.auth import AuthError, as_utc
from app.domain.models import AuthFlow, User

FLOW_LIFETIME = timedelta(minutes=10)
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class OidcError(AuthError):
    pass


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    label: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "OidcConfig | None":
        issuer, client_id = settings.oidc_issuer, settings.oidc_client_id
        secret = settings.oidc_client_secret
        if not (issuer and client_id and secret):
            return None
        return cls(
            issuer=issuer.rstrip("/") + "/",
            client_id=client_id,
            client_secret=secret,
            redirect_uri=settings.public_url.rstrip("/") + "/api/auth/oidc/callback",
            label=settings.oidc_label,
        )


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class OidcClient:
    def __init__(self, config: OidcConfig, http: httpx.Client | None = None) -> None:
        self.config = config
        self.http = http or httpx.Client(timeout=10)
        self._discovery: dict[str, Any] | None = None

    def discovery(self) -> dict[str, Any]:
        if self._discovery is None:
            url = self.config.issuer + ".well-known/openid-configuration"
            response = self.http.get(url)
            response.raise_for_status()
            self._discovery = response.json()
        return self._discovery

    def start(self, session: Session, link_user: User | None = None) -> str:
        """Begin a sign-in (or a link); returns the provider URL to redirect to."""
        verifier = secrets.token_urlsafe(64)
        flow = AuthFlow(
            state=secrets.token_urlsafe(32),
            code_verifier=verifier,
            nonce=secrets.token_urlsafe(32),
            link_user_id=link_user.id if link_user else None,
        )
        session.add(flow)
        session.flush()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": "openid email profile",
                "state": flow.state,
                "nonce": flow.nonce,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{self.discovery()['authorization_endpoint']}?{query}"

    def _claims(self, code: str, flow: AuthFlow) -> dict[str, Any]:
        meta = self.discovery()
        response = self.http.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": flow.code_verifier,
            },
            auth=(self.config.client_id, self.config.client_secret),
        )
        if response.status_code != 200:
            raise OidcError("The sign-in provider didn't accept the login.")
        id_token = response.json().get("id_token")
        if not id_token:
            raise OidcError("The sign-in provider didn't return an ID token.")

        header = jwt.get_unverified_header(id_token)
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise OidcError("The ID token uses an algorithm we don't accept.")
        jwks = self.http.get(meta["jwks_uri"])
        jwks.raise_for_status()
        keys = jwks.json().get("keys", [])
        jwk = next((k for k in keys if k.get("kid") == header.get("kid")), None)
        if jwk is None and len(keys) == 1:
            jwk = keys[0]
        if jwk is None:
            raise OidcError("The ID token's signing key isn't published by the provider.")
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=jwt.PyJWK.from_json(json.dumps(jwk)).key,
                algorithms=[header["alg"]],
                audience=self.config.client_id,
                issuer=meta.get("issuer", self.config.issuer),
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"The ID token didn't verify: {exc}") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), flow.nonce):
            raise OidcError("The ID token's nonce doesn't match this sign-in.")
        return claims

    def finish(self, session: Session, code: str, state: str) -> tuple[User, bool]:
        """Complete the flow. Returns the user and whether this was a link from Settings."""
        flow = session.scalars(select(AuthFlow).where(AuthFlow.state == state)).first()
        if flow is None:
            raise OidcError("That sign-in link has expired. Try again.")
        session.delete(flow)
        session.flush()
        if utcnow() - as_utc(flow.created_at) > FLOW_LIFETIME:
            raise OidcError("That sign-in took too long. Try again.")

        claims = self._claims(code, flow)
        subject = str(claims["sub"])
        owner = session.scalars(select(User).where(User.oidc_subject == subject)).first()

        if flow.link_user_id is not None:
            user = session.get(User, flow.link_user_id)
            if user is None:
                raise OidcError("That user no longer exists.")
            if owner is not None and owner.id != user.id:
                raise OidcError(f"That {self.config.label} account is linked to someone else.")
            user.oidc_subject = subject
            session.flush()
            audit.record(session, user, "user", user.id, "oidc_link", None, {"subject": subject})
            return user, True

        if owner is None:
            email = claims.get("email")
            if email and claims.get("email_verified") is True:
                owner = session.scalars(
                    select(User).where(User.email == email, User.oidc_subject.is_(None))
                ).first()
                if owner is not None:
                    owner.oidc_subject = subject
                    audit.record(
                        session,
                        owner,
                        "user",
                        owner.id,
                        "oidc_link",
                        None,
                        {"subject": subject, "by": "verified_email"},
                    )
        if owner is None or owner.disabled_at is not None:
            raise OidcError(
                f"That {self.config.label} account isn't linked to anyone here. Sign in with "
                "your password and link it from Settings."
            )
        session.flush()
        return owner, False


def unlink(session: Session, user: User, local_login_enabled: bool) -> None:
    if not local_login_enabled and not user.password_hash:
        raise OidcError("Unlinking would leave you no way to sign in.")
    before = user.oidc_subject
    user.oidc_subject = None
    session.flush()
    audit.record(session, user, "user", user.id, "oidc_unlink", {"subject": before}, None)
