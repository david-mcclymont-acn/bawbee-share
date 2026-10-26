"""Monzo through its own Developer API (decision 29), against a fake Monzo.

The fake keeps Monzo's awkward parts: nothing can be read until access is approved in the
app (403), access tokens expire and each refresh replaces both tokens, and transactions
come in pages of 100 that carry on from the last id.
"""

from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crypto
from app.config import Settings
from app.domain.models import AuditLog, BankConnection, BankTransaction, User
from app.providers import monzo
from app.providers.base import ConsentExpired, ProviderAccount, ProviderError, RateLimited
from app.sync import banks, recurring
from tests.conftest import login


class FakeMonzo:
    def __init__(self) -> None:
        self.approved = False
        self.access = "access-1"
        self.refresh = "refresh-1"
        self.issued = 1
        self.logged_out: list[str] = []
        self.transactions: list[dict[str, Any]] = []
        self.calls: list[httpx.Request] = []
        self.limited = False

    def rotate(self) -> dict[str, Any]:
        self.issued += 1
        self.access, self.refresh = f"access-{self.issued}", f"refresh-{self.issued}"
        return {
            "access_token": self.access,
            "refresh_token": self.refresh,
            "expires_in": 21600,
            "user_id": "user_1",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["oauth2client_1"]
            assert form["client_secret"] == ["mnzconf.secret"]
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["good-code"]:
                    return httpx.Response(400, json={"code": "bad_request"})
                assert form["redirect_uri"] == ["http://testserver/api/banks/callback"]
                self.issued = 0
                return httpx.Response(200, json=self.rotate())
            if form["grant_type"] == ["refresh_token"] and form["refresh_token"] == [self.refresh]:
                return httpx.Response(200, json=self.rotate())
            return httpx.Response(401, json={"code": "unauthorized.bad_refresh_token"})
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if path == "/oauth2/logout":
            self.logged_out.append(token)
            return httpx.Response(200, json={})
        if token != self.access:
            return httpx.Response(401, json={"code": "unauthorized.bad_access_token"})
        if not self.approved:
            return httpx.Response(403, json={"code": "forbidden.insufficient_permissions"})
        if self.limited:
            return httpx.Response(429, json={})
        assert request.method == "GET", "read-only: nothing but GETs for data"
        params = request.url.params
        if path == "/accounts":
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "id": "acc_personal",
                            "type": "uk_retail",
                            "account_number": "12345678",
                            "currency": "GBP",
                            "closed": False,
                        },
                        {
                            "id": "acc_joint",
                            "type": "uk_retail_joint",
                            "account_number": "87654321",
                            "currency": "GBP",
                            "closed": False,
                        },
                        {"id": "acc_old", "type": "uk_retail", "closed": True},
                    ]
                },
            )
        if path == "/balance":
            return httpx.Response(200, json={"balance": 123456, "total_balance": 150000})
        if path == "/pots":
            assert params["current_account_id"] == "acc_personal"
            return httpx.Response(200, json={"pots": [{"id": "pot_1", "name": "Holiday"}]})
        if path == "/transactions":
            assert params["limit"] == "100"
            assert params["expand[]"] == "merchant"
            rows = [t for t in self.transactions if t["account_id"] == params["account_id"]]
            since = params.get("since")
            if since and since.startswith("tx_"):
                ids = [t["id"] for t in rows]
                rows = rows[ids.index(since) + 1 :]
            return httpx.Response(200, json={"transactions": rows[:100]})
        return httpx.Response(404)


def tx(n: int, **fields: Any) -> dict[str, Any]:
    return {
        "id": f"tx_{n:05d}",
        "account_id": "acc_personal",
        "amount": -385,
        "created": "2026-10-06T12:00:00Z",
        "settled": "2026-10-07T04:00:00Z",
        "description": "COSTA COFFEE EXAMPLETOWN",
        "merchant": {"name": "Costa Coffee"},
        "category": "eating_out",
        "decline_reason": None,
        "metadata": {},
    } | fields


def provider(fake: FakeMonzo, **kw: Any) -> monzo.MonzoProvider:
    return monzo.MonzoProvider(
        "oauth2client_1",
        "mnzconf.secret",
        kw.pop(
            "tokens",
            {"access_token": fake.access, "refresh_token": fake.refresh, "expires_at": 2**40},
        ),
        http=httpx.Client(base_url="https://api.monzo.com", transport=httpx.MockTransport(fake)),
        **kw,
    )


ACCOUNT = ProviderAccount("acc_personal", "Monzo personal account")

# ---- the provider ----------------------------------------------------------------------------


def test_the_sign_in_link_and_the_code_exchange() -> None:
    url = urlparse(monzo.authorize_url("oauth2client_1", "https://b.example/cb", "s1"))
    assert url.netloc == "auth.monzo.com"
    assert parse_qs(url.query) == {
        "client_id": ["oauth2client_1"],
        "redirect_uri": ["https://b.example/cb"],
        "response_type": ["code"],
        "state": ["s1"],
    }
    fake = FakeMonzo()
    http = httpx.Client(base_url="https://api.monzo.com", transport=httpx.MockTransport(fake))
    tokens = monzo.exchange_code(
        "oauth2client_1",
        "mnzconf.secret",
        "http://testserver/api/banks/callback",
        "good-code",
        http,
    )
    assert tokens["access_token"] == "access-1"
    assert tokens["refresh_token"] == "refresh-1"
    with pytest.raises(ProviderError):
        monzo.exchange_code(
            "oauth2client_1", "mnzconf.secret", "http://testserver/api/banks/callback", "bad", http
        )


def test_nothing_reads_until_approved_in_the_app() -> None:
    fake = FakeMonzo()
    with pytest.raises(monzo.AwaitingApproval):
        provider(fake).list_accounts()
    fake.approved = True
    accounts = provider(fake).list_accounts()
    assert [(a.uid, a.name, a.detail) for a in accounts] == [
        ("acc_personal", "Monzo personal account", "Personal account …5678"),
        ("acc_joint", "Monzo joint account", "Joint account …4321"),
    ]
    assert accounts[1].identification_hash == "monzo:acc_joint"


def test_an_expired_token_is_refreshed_once_and_the_new_pair_saved() -> None:
    fake = FakeMonzo()
    fake.approved = True
    saved: list[dict[str, Any]] = []
    p = provider(fake, on_tokens=saved.append)
    fake.rotate()  # Monzo has moved on: the token we hold is now refused
    fake.refresh = "refresh-1"
    fake.access = "access-elsewhere"
    assert len(p.list_accounts()) == 2
    assert [t["access_token"] for t in saved] == ["access-3"]
    assert p.tokens["refresh_token"] == "refresh-3"
    # A refresh token works once: with it gone, it's a re-link.
    p.tokens["access_token"] = "stale"
    p.tokens["refresh_token"] = "refresh-1"
    with pytest.raises(ConsentExpired):
        p.list_accounts()


def test_a_token_near_its_end_is_refreshed_before_asking() -> None:
    fake = FakeMonzo()
    fake.approved = True
    saved: list[dict[str, Any]] = []
    p = provider(
        fake,
        tokens={"access_token": "access-1", "refresh_token": "refresh-1", "expires_at": 0},
        on_tokens=saved.append,
    )
    p.list_accounts()
    assert len(saved) == 1
    assert [c.url.path for c in fake.calls] == ["/oauth2/token", "/accounts"]


def test_transactions_page_on_from_the_last_id() -> None:
    fake = FakeMonzo()
    fake.approved = True
    fake.transactions = [tx(n) for n in range(150)]
    got = provider(fake).fetch_transactions(ACCOUNT, date(2026, 10, 1), date(2026, 11, 28))
    assert len(got) == 150
    pages = [c for c in fake.calls if c.url.path == "/transactions"]
    assert len(pages) == 2
    assert pages[0].url.params["since"] == "2026-09-30T23:00:00Z"  # 1 Oct, UK time
    assert pages[0].url.params["before"] == "2026-11-29T00:00:00Z"
    assert pages[1].url.params["since"] == "tx_00099"


def test_parsing_pending_settled_declined_and_pots() -> None:
    settled = monzo.parse_transaction(tx(1))
    assert settled is not None
    assert (settled.status, settled.amount_pence, settled.counterparty) == (
        "booked",
        -385,
        "Costa Coffee",
    )
    assert settled.booked_date == date(2026, 10, 7)
    pending = monzo.parse_transaction(tx(2, settled=""))
    assert pending is not None
    assert pending.status == "pending"
    assert pending.booked_date == date(2026, 10, 6)
    # Late on 30 June in UTC is 1 July in the UK.
    late = monzo.parse_transaction(tx(3, created="2026-06-30T23:30:00Z", settled=""))
    assert late is not None
    assert late.booked_date == date(2026, 7, 1)
    assert monzo.parse_transaction(tx(4, decline_reason="INSUFFICIENT_FUNDS")) is None
    pot = monzo.parse_transaction(
        tx(5, amount=-5000, merchant=None, metadata={"pot_id": "pot_1"}), {"pot_1": "Holiday"}
    )
    assert pot is not None
    assert (pot.counterparty, pot.description, pot.bank_category) == (
        "Pot: Holiday",
        "Pot: Holiday",
        "pot_transfer",
    )


def test_being_asked_to_slow_down() -> None:
    fake = FakeMonzo()
    fake.approved = True
    fake.limited = True
    with pytest.raises(RateLimited):
        provider(fake).fetch_balance(ACCOUNT)
    fake.limited = False
    balance = provider(fake).fetch_balance(ACCOUNT)
    assert balance is not None
    assert balance.amount_pence == 123456


# ---- linking, end to end -----------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        static_dir=tmp_path,
        cookie_secure=False,
        public_url="http://testserver",
        fernet_key=crypto.new_key(),
        monzo_client_id="oauth2client_1",
        monzo_client_secret="mnzconf.secret",
        manual_sync_minutes=30,
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeMonzo:
    server = FakeMonzo()
    monkeypatch.setattr(
        banks,
        "monzo_http",
        lambda settings: httpx.Client(
            base_url=settings.monzo_api, transport=httpx.MockTransport(server)
        ),
    )
    return server


def tokens_of(session: Session, settings: Settings, connection_id: int) -> dict[str, Any]:
    connection = session.get(BankConnection, connection_id)
    assert connection is not None
    session.refresh(connection)
    return monzo.load(crypto.decrypt(settings, connection.secret_encrypted))


def test_link_approve_map_sync_and_disconnect(
    client: TestClient,
    app: FastAPI,
    session: Session,
    settings: Settings,
    users: dict[str, User],
    fake: FakeMonzo,
) -> None:
    overview = client.get("/api/banks").json()
    assert overview["providers"]["monzo"] is True
    assert overview["setup"]["monzo"] is None
    assert "BAWBEE_ENABLE_BANKING_APP_ID" in overview["setup"]["enable_banking"]
    assert ("Monzo", "monzo") in {
        (b["name"], b["provider"]) for b in client.get("/api/banks/institutions").json()
    }

    started = client.post("/api/banks/link", json={"bank": "Monzo", "country": "GB"})
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["url"])
    assert url.netloc == "auth.monzo.com"
    state = parse_qs(url.query)["state"][0]

    # Back from Monzo, signed in, but not yet approved in the app.
    back = client.get(f"/api/banks/callback?state={state}&code=good-code", follow_redirects=False)
    assert back.status_code == 303
    assert back.headers["location"].startswith("/banks?approve=")
    connection_id = int(back.headers["location"].split("=")[1])
    connection = session.get(BankConnection, connection_id)
    assert connection is not None
    assert (connection.provider, connection.status) == ("monzo", "pending")
    assert "access-1" not in connection.secret_encrypted
    assert connection.consent_expires_at is None

    assert client.post(f"/api/banks/{connection_id}/sync").status_code == 422
    person_2 = login(app, "person_2")
    assert person_2.post(f"/api/banks/{connection_id}/continue").status_code == 403
    early = client.post(f"/api/banks/{connection_id}/continue")
    assert early.status_code == 422
    assert "Monzo app" in early.json()["detail"]

    fake.approved = True
    fake.transactions = [
        tx(1, created="2026-10-03T09:00:00Z", settled="2026-10-03T09:00:00Z"),
        tx(2, decline_reason="INSUFFICIENT_FUNDS"),
        tx(3, amount=-5000, merchant=None, metadata={"pot_id": "pot_1"}),
        tx(4, settled=""),
    ]
    went_on = client.post(f"/api/banks/{connection_id}/continue")
    assert went_on.status_code == 200, went_on.text
    assert went_on.json()["map"] is True
    view = went_on.json()["connection"]
    assert view["status"] == "active"
    assert [a["uid"] for a in view["accounts"]] == ["acc_personal", "acc_joint"]

    mapped = client.put(
        f"/api/banks/{connection_id}/accounts",
        json={"accounts": {"acc_personal": "person_1_monzo", "acc_joint": None}},
    )
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["last_run"]["status"] == "ok"
    rows = session.scalars(
        select(BankTransaction).where(BankTransaction.connection_id == connection_id)
    ).all()
    assert sorted((r.external_id, r.status) for r in rows) == [
        ("tx_00001", "booked"),
        ("tx_00003", "booked"),
        ("tx_00004", "pending"),
    ]
    pot = next(r for r in rows if r.external_id == "tx_00003")
    assert (pot.counterparty, pot.bank_category) == ("Pot: Holiday", "pot_transfer")
    personal = next(a for a in mapped.json()["accounts"] if a["uid"] == "acc_personal")
    assert personal["balance_pence"] == 123456

    # Hours later the access token has gone: the sync refreshes it and keeps the new pair.
    fake.access = "access-elsewhere"
    connection = session.get(BankConnection, connection_id)
    assert connection is not None
    run = banks.sync_connection(session, settings, connection, "scheduled")
    session.commit()
    assert run.status == "ok", run.error
    assert tokens_of(session, settings, connection_id)["refresh_token"] == fake.refresh

    gone = client.delete(f"/api/banks/{connection_id}")
    assert gone.status_code == 200
    assert fake.logged_out == [fake.access]
    actions = set(
        session.scalars(
            select(AuditLog.action).where(
                AuditLog.entity == "bank_connection", AuditLog.entity_id == connection_id
            )
        )
    )
    assert "approved" in actions


def test_a_monzo_relink_keeps_the_accounts_and_syncs_on_continue(
    client: TestClient, session: Session, users: dict[str, User], fake: FakeMonzo
) -> None:
    fake.approved = True
    fake.transactions = [tx(1)]
    started = client.post("/api/banks/link", json={"bank": "Monzo", "country": "GB"})
    state = parse_qs(urlparse(started.json()["url"]).query)["state"][0]
    back = client.get(f"/api/banks/callback?state={state}&code=good-code", follow_redirects=False)
    # Already approved (as on a phone, quickly): straight to choosing what it feeds.
    assert back.headers["location"].startswith("/banks?linked=")
    connection_id = int(back.headers["location"].split("=")[1])
    client.put(
        f"/api/banks/{connection_id}/accounts",
        json={"accounts": {"acc_personal": "person_1_monzo"}},
    )

    fake.approved = False
    started = client.post(
        "/api/banks/link",
        json={"bank": "Monzo", "country": "GB", "relink_connection_id": connection_id},
    )
    state = parse_qs(urlparse(started.json()["url"]).query)["state"][0]
    back = client.get(f"/api/banks/callback?state={state}&code=good-code", follow_redirects=False)
    assert back.headers["location"] == f"/banks?approve={connection_id}"
    waiting = next(c for c in client.get("/api/banks").json()["connections"])
    assert waiting["status"] == "pending"
    assert {a["account"] for a in waiting["accounts"]} == {"person_1_monzo", None}

    fake.approved = True
    went_on = client.post(f"/api/banks/{connection_id}/continue").json()
    assert went_on["map"] is False
    assert went_on["connection"]["last_run"]["trigger"] == "link"


def test_pot_moves_are_never_proposed_as_bills(
    session: Session, imported: Any, users: dict[str, User]
) -> None:
    from app.domain.models import Account

    account = session.scalars(select(Account).where(Account.key == "person_1_monzo")).one()
    for n, month in enumerate(range(5, 11)):
        session.add(
            BankTransaction(
                account_id=account.id,
                external_id=f"tx_pot_{n}",
                merchant_normalised="POT HOLIDAY",
                booked_date=date(2026, month, 1),
                amount_pence=-20000,
                description="Pot: Holiday",
                counterparty="Pot: Holiday",
                bank_category="pot_transfer",
                status="booked",
            )
        )
    session.flush()
    recurring.detect(session, date(2026, 10, 2))
    from app.domain.models import RecurringCandidate

    assert session.scalars(select(RecurringCandidate)).all() == []


def test_the_server_says_why_monzo_isnt_offered(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert "BAWBEE_MONZO_CLIENT_ID" in (Settings().monzo_problem or "")
    secret = tmp_path / "monzo_client_secret"
    settings = Settings(monzo_client_id="oauth2client_1", monzo_client_secret_file=secret)
    assert "isn't there" in (settings.monzo_problem or "")
    assert settings.monzo_enabled is False
    secret.write_text("  \n", encoding="utf-8")
    assert "is empty" in (settings.monzo_problem or "")
    secret.write_text("mnzconf.secret\n", encoding="utf-8")
    assert settings.monzo_problem is None
    assert settings.monzo_secret == "mnzconf.secret"
    assert ("Monzo", "monzo") in {(b.name, b.provider) for b in banks.available_banks(settings)}


def test_bawbee_providers_reports_each_route(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    cli.main(["providers"])
    out = capsys.readouterr().out
    assert "encryption key: ok" in out
    assert "monzo:          ok" in out
    assert "enable banking: BAWBEE_ENABLE_BANKING_APP_ID isn't set." in out
