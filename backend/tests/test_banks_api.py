"""The Banks API: password confirmation, linking through the demo bank, and ownership."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crypto
from app.config import Settings
from app.domain.models import AuthSession, BankConnection, User
from app.providers.base import ProviderAccount
from app.sync import banks
from tests.conftest import PASSWORD, login


@pytest.fixture
def settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        static_dir=tmp_path,
        cookie_secure=False,
        public_url="http://testserver",
        fernet_key=crypto.new_key(),
        banks_demo=True,
        manual_sync_minutes=30,
    )


def age_sessions(session: Session, hours: int) -> None:
    for auth in session.scalars(select(AuthSession)):
        auth.created_at = auth.created_at - timedelta(hours=hours)
        auth.confirmed_at = None
    session.commit()


def link(client: TestClient) -> int:
    started = client.post("/api/banks/link", json={"bank": "Demo Bank", "country": "GB"})
    assert started.status_code == 200, started.text
    url = started.json()["url"]
    assert url.startswith("http://testserver/api/banks/callback?state=")
    back = client.get(url.removeprefix("http://testserver"), follow_redirects=False)
    assert back.status_code == 303
    assert back.headers["location"].startswith("/banks?linked=")
    return int(back.headers["location"].split("=")[1])


def test_linking_needs_a_recent_password(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    age_sessions(session, 13)
    refused = client.post("/api/banks/link", json={"bank": "Demo Bank", "country": "GB"})
    assert refused.status_code == 428
    wrong = client.post("/api/auth/confirm", json={"password": "not it at all"})
    assert wrong.status_code == 400
    assert client.post("/api/auth/confirm", json={"password": PASSWORD}).status_code == 204
    assert (
        client.post("/api/banks/link", json={"bank": "Demo Bank", "country": "GB"}).status_code
        == 200
    )


def test_link_map_sync_and_disconnect(
    client: TestClient, app: FastAPI, session: Session, users: dict[str, User]
) -> None:
    overview = client.get("/api/banks").json()
    assert overview["can_link"] is True
    assert overview["providers"]["demo"] is True
    assert {"Demo Bank"} <= {b["name"] for b in client.get("/api/banks/institutions").json()}

    connection_id = link(client)
    mapped = client.put(
        f"/api/banks/{connection_id}/accounts",
        json={"accounts": {"demo-current": "person_1_monzo", "demo-joint": None}},
    )
    assert mapped.status_code == 200, mapped.text
    view = mapped.json()
    assert view["owner"] == "Person 1"
    assert view["can_manage"] is True
    current = next(a for a in view["accounts"] if a["uid"] == "demo-current")
    assert current["account"] == "person_1_monzo"
    assert current["synced_through"] is not None
    assert view["last_run"]["status"] == "ok"
    assert view["last_run"]["new"] > 0
    assert "person_1_monzo" not in {a["key"] for a in client.get("/api/banks").json()["unlinked"]}

    # Sync now works once, then waits: banks limit how often they can be asked.
    assert client.post(f"/api/banks/{connection_id}/sync").status_code == 200
    again = client.post(f"/api/banks/{connection_id}/sync")
    assert again.status_code == 422
    assert "try again after" in again.json()["detail"]

    # Person 2 can see it, but it's Person 1's to change.
    person_2 = login(app, "person_2")
    seen = next(
        c for c in person_2.get("/api/banks").json()["connections"] if c["id"] == connection_id
    )
    assert seen["can_manage"] is False
    assert person_2.delete(f"/api/banks/{connection_id}").status_code == 403

    gone = client.delete(f"/api/banks/{connection_id}", params={"delete_transactions": True})
    assert gone.status_code == 200
    assert gone.json()["transactions_deleted"] > 0
    assert client.get("/api/banks").json()["connections"] == []


def test_a_cancelled_bank_sign_in_lands_back_on_banks(
    client: TestClient, users: dict[str, User]
) -> None:
    back = client.get("/api/banks/callback?error=access_denied", follow_redirects=False)
    assert back.status_code == 303
    assert back.headers["location"].startswith("/banks?error=")
    reused = client.get("/api/banks/callback?state=nope&code=demo", follow_redirects=False)
    assert "error=" in reused.headers["location"]


def test_a_starling_token_is_checked_then_stored_encrypted(
    client: TestClient, session: Session, users: dict[str, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeStarling:
        def __init__(self, token: str, base_url: str) -> None:
            if token != "t" * 64:
                raise banks.BankError("Starling refused the token.")

        def list_accounts(self) -> list[ProviderAccount]:
            return [
                ProviderAccount(
                    "acc-1/cat-1", "Joint", identification_hash="starling:acc-1", detail="Joint"
                )
            ]

    monkeypatch.setattr(banks, "StarlingProvider", FakeStarling)
    refused = client.post("/api/banks/starling", json={"token": "x" * 64})
    assert refused.status_code == 422
    made = client.post("/api/banks/starling", json={"token": "t" * 64})
    assert made.status_code == 201, made.text
    connection = session.get(BankConnection, made.json()["id"])
    assert connection is not None
    assert "t" * 64 not in connection.secret_encrypted
    assert made.json()["accounts"][0]["name"] == "Joint"


def test_ending_consents_are_the_owners_to_deal_with(
    app: FastAPI, session: Session, users: dict[str, User]
) -> None:
    from datetime import date

    from app.db import utcnow
    from app.domain.models import InboxItem

    person_2 = login(app, "person_2")
    connection_id = link(person_2)
    person_2.put(
        f"/api/banks/{connection_id}/accounts", json={"accounts": {"demo-current": "person_2_rbs"}}
    )
    connection = session.get(BankConnection, connection_id)
    assert connection is not None
    connection.consent_expires_at = utcnow() + timedelta(days=13)
    banks.check_consents(session)
    session.commit()

    mine = person_2.get("/api/inbox").json()
    assert [i["kind"] for i in mine["items"]] == ["consent_expiring"]
    person_1 = login(app, "person_1")
    theirs = person_1.get("/api/inbox").json()
    assert theirs["items"] == []
    assert person_1.get("/api/inbox/count").json()["count"] == 0
    assert [(c["owner"], c["bank"]) for c in theirs["others_consents"]] == [
        ("Person 2", "Demo Bank")
    ]

    item_id = mine["items"][0]["id"]
    assert person_1.post(f"/api/inbox/{item_id}/snooze", json={}).status_code == 403
    assert person_2.post(f"/api/inbox/{item_id}/snooze", json={}).status_code == 204
    item = session.get(InboxItem, item_id)
    session.refresh(item)
    assert item is not None
    assert item.snoozed_until is not None
    ends = date.fromisoformat(item.payload_json["expires_on"])
    assert item.snoozed_until <= ends - timedelta(days=2)
    # Undo puts it back.
    assert person_2.post(f"/api/inbox/{item_id}/undo").status_code == 204

    # Too close to the end to put off any longer.
    connection.consent_expires_at = utcnow() + timedelta(days=5)
    item.payload_json = item.payload_json | {
        "expires_on": (date.today() + timedelta(days=5)).isoformat()
    }
    session.commit()
    too_late = person_2.post(f"/api/inbox/{item_id}/snooze", json={})
    assert too_late.status_code == 422
    assert "too soon" in too_late.json()["detail"]


def test_the_bank_can_hand_back_to_a_browser_that_isnt_signed_in(
    app: FastAPI, users: dict[str, User]
) -> None:
    anonymous = TestClient(app)
    back = anonymous.get("/api/banks/callback?state=abc&code=xyz", follow_redirects=False)
    assert back.status_code == 303
    assert back.headers["location"] == "/login?next=/api/banks/callback%3Fstate%3Dabc%26code%3Dxyz"


def test_a_relink_with_the_same_accounts_skips_the_mapping(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    connection_id = link(client)
    client.put(
        f"/api/banks/{connection_id}/accounts",
        json={"accounts": {"demo-current": "person_1_monzo"}},
    )
    view = next(
        c for c in client.get("/api/banks").json()["connections"] if c["id"] == connection_id
    )
    assert view["transactions"] > 0
    assert view["first_transaction"] is not None
    started = client.post(
        "/api/banks/link",
        json={"bank": "Demo Bank", "country": "GB", "relink_connection_id": connection_id},
    )
    back = client.get(
        started.json()["url"].removeprefix("http://testserver"), follow_redirects=False
    )
    assert back.headers["location"] == f"/banks?relinked={connection_id}"
    # After a Sync now, the next one waits.
    assert client.post(f"/api/banks/{connection_id}/sync").status_code == 200
    view = next(
        c for c in client.get("/api/banks").json()["connections"] if c["id"] == connection_id
    )
    assert view["next_sync_now_at"] is not None
    assert client.get("/api/banks").json()["confirmed"] is True
