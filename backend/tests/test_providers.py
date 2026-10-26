"""Bank providers against recorded (made-up) responses: no network, no real accounts."""

import json
from datetime import date
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.providers.base import ConsentExpired, ProviderAccount, ProviderError, RateLimited
from app.providers.demo import CURRENT, DemoProvider
from app.providers.enable_banking import (
    EnableBankingClient,
    EnableBankingSession,
    accounts_from_session,
    parse_transaction,
)
from app.providers.starling_pat import StarlingProvider, parse_feed_item

APP_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def eb(key: rsa.RSAPrivateKey, handler: Any) -> EnableBankingClient:
    http = httpx.Client(
        base_url="https://api.enablebanking.com", transport=httpx.MockTransport(handler)
    )
    return EnableBankingClient(APP_ID, pem(key), http=http)


def txn(**fields: Any) -> dict[str, Any]:
    base = {
        "transaction_id": "t1",
        "transaction_amount": {"amount": "108.50", "currency": "GBP"},
        "credit_debit_indicator": "DBIT",
        "status": "BOOK",
        "booking_date": "2026-11-01",
        "creditor": {"name": "SKY DIGITAL"},
        "remittance_information": ["SKY0123456"],
    }
    return base | fields


# ---- Enable Banking -----------------------------------------------------------------------


def test_every_call_is_signed_rs256_with_the_app_id(key: rsa.RSAPrivateKey) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"aspsps": [{"name": "Monzo", "country": "GB"}]})

    banks = eb(key, handler).aspsps("GB")
    assert banks == [{"name": "Monzo", "country": "GB"}]
    token = seen[0].headers["Authorization"].removeprefix("Bearer ")
    header = jwt.get_unverified_header(token)
    assert (header["alg"], header["kid"]) == ("RS256", APP_ID)
    claims = jwt.decode(
        token, key.public_key(), algorithms=["RS256"], audience="api.enablebanking.com"
    )
    assert claims["iss"] == "enablebanking.com"
    assert claims["exp"] - claims["iat"] <= 86400
    assert seen[0].url.params["country"] == "GB"


def test_linking_asks_for_account_information_only(key: rsa.RSAPrivateKey) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json={"url": "https://bank.example/auth", "authorization_id": "a"}
        )

    from datetime import UTC, datetime

    url = eb(key, handler).start_auth(
        "RBS",
        "GB",
        "https://bawbee.example/api/banks/callback",
        "state1",
        datetime(2027, 1, 19, tzinfo=UTC),
    )
    assert url == "https://bank.example/auth"
    body = bodies[0]
    assert body["aspsp"] == {"name": "RBS", "country": "GB"}
    assert body["access"] == {"valid_until": "2027-01-19T00:00:00+00:00"}
    assert body["psu_type"] == "personal"
    assert "payment" not in json.dumps(body).lower()


def test_transactions_are_exact_pence_with_money_out_negative() -> None:
    sky = parse_transaction(txn())
    assert sky is not None
    assert (sky.amount_pence, sky.status, sky.counterparty, sky.description) == (
        -10850,
        "booked",
        "SKY DIGITAL",
        "SKY0123456",
    )
    salary = parse_transaction(
        txn(credit_debit_indicator="CRDT", debtor={"name": "EXAMPLE LTD"}, creditor=None)
    )
    assert salary is not None
    assert (salary.amount_pence, salary.counterparty) == (10850, "EXAMPLE LTD")
    pending = parse_transaction(txn(status="PDNG"))
    assert pending is not None
    assert pending.status == "pending"
    assert parse_transaction(txn(status="CNCL")) is None
    assert parse_transaction(txn(status="RJCT")) is None
    with pytest.raises(ProviderError):
        parse_transaction(txn(transaction_amount={"amount": "1.234", "currency": "GBP"}))


def test_rows_without_a_bank_id_hash_stably_and_twins_stay_apart() -> None:
    coffee = txn(transaction_id=None, entry_reference=None, remittance_information=["COSTA"])
    a, b = parse_transaction(coffee, 0), parse_transaction(coffee, 1)
    again = parse_transaction(coffee, 0)
    assert a
    assert b
    assert again
    assert a.external_id.startswith("eb:")
    assert a.external_id == again.external_id != b.external_id


def test_accounts_carry_a_stable_hash_and_a_short_detail() -> None:
    accounts = accounts_from_session(
        {
            "session_id": "s1",
            "accounts": [
                {
                    "uid": "u1",
                    "name": "Current Account",
                    "currency": "GBP",
                    "account_id": {"other": {"identification": "60161331926819"}},
                    "identification_hash": "h1",
                }
            ],
        }
    )
    assert accounts == [
        ProviderAccount(
            uid="u1",
            name="Current Account",
            currency="GBP",
            identification_hash="h1",
            detail="…6819",
        )
    ]


def test_transactions_page_through_continuation_keys(key: rsa.RSAPrivateKey) -> None:
    calls: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params)
        if "continuation_key" not in request.url.params:
            return httpx.Response(200, json={"transactions": [txn()], "continuation_key": "next"})
        return httpx.Response(
            200, json={"transactions": [txn(transaction_id="t2")], "continuation_key": None}
        )

    session = EnableBankingSession(eb(key, handler), "s1")
    rows = session.fetch_transactions(
        ProviderAccount("u1", "Current"), date(2026, 11, 1), date(2026, 11, 30)
    )
    assert [r.external_id for r in rows] == ["t1", "t2"]
    assert calls[0]["date_from"] == "2026-11-01"
    assert calls[1]["continuation_key"] == "next"
    # No start date: ask the bank for as much history as it gives.
    calls.clear()
    session.fetch_transactions(ProviderAccount("u1", "Current"), None, date(2026, 11, 30))
    assert calls[0]["strategy"] == "longest"


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"message": "Unauthorised"}, ConsentExpired),
        (422, {"error": "EXPIRED_SESSION", "message": "Session expired"}, ConsentExpired),
        (429, {"error": "ASPSP_RATE_LIMIT_EXCEEDED", "message": "limit"}, RateLimited),
        (500, {"message": "Bank down"}, ProviderError),
    ],
)
def test_failures_say_what_to_do(
    key: rsa.RSAPrivateKey, status: int, body: dict[str, Any], error: type
) -> None:
    client = eb(key, lambda _r: httpx.Response(status, json=body))
    with pytest.raises(error):
        EnableBankingSession(client, "s1").fetch_balance(ProviderAccount("u1", "Current"))


def test_the_most_useful_balance_is_used(key: rsa.RSAPrivateKey) -> None:
    body = {
        "balances": [
            {"balance_type": "CLBD", "balance_amount": {"amount": "90.00", "currency": "GBP"}},
            {
                "balance_type": "ITAV",
                "balance_amount": {"amount": "1234.56", "currency": "GBP"},
                "last_change_date_time": "2026-11-02T06:00:00Z",
            },
        ]
    }
    balance = EnableBankingSession(
        eb(key, lambda _r: httpx.Response(200, json=body)), "s1"
    ).fetch_balance(ProviderAccount("u1", "Current"))
    assert balance is not None
    assert balance.amount_pence == 123456


# ---- Starling ---------------------------------------------------------------------------


def starling(handler: Any) -> StarlingProvider:
    http = httpx.Client(
        base_url="https://api.starlingbank.com", transport=httpx.MockTransport(handler)
    )
    return StarlingProvider("x" * 64, http=http)


def feed(**fields: Any) -> dict[str, Any]:
    base = {
        "feedItemUid": "f1",
        "amount": {"currency": "GBP", "minorUnits": 10850},
        "direction": "OUT",
        "status": "SETTLED",
        "transactionTime": "2026-11-01T08:00:00.000Z",
        "settlementTime": "2026-11-01T08:00:01.000Z",
        "counterPartyName": "Sky",
        "reference": "SKY0123456",
        "spendingCategory": "BILLS_AND_SERVICES",
    }
    return base | fields


def test_starling_accounts_feed_by_their_default_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer " + "x" * 64
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "accountUid": "acc-1",
                        "accountType": "PRIMARY",
                        "defaultCategory": "cat-1",
                        "currency": "GBP",
                        "name": "Personal",
                    }
                ]
            },
        )

    (account,) = starling(handler).list_accounts()
    assert (account.uid, account.identification_hash, account.detail) == (
        "acc-1/cat-1",
        "starling:acc-1",
        "Primary",
    )


def test_starling_feed_items() -> None:
    sky = parse_feed_item(feed())
    assert sky is not None
    assert (sky.external_id, sky.amount_pence, sky.status) == (
        "f1",
        -10850,
        "booked",
    )
    assert sky.bank_category == "BILLS_AND_SERVICES"
    pending = parse_feed_item(feed(status="PENDING", settlementTime=None))
    assert pending is not None
    assert pending.status == "pending"
    assert parse_feed_item(feed(status="DECLINED")) is None
    assert parse_feed_item(feed(status="UPCOMING")) is None
    paid_in = parse_feed_item(feed(direction="IN"))
    assert paid_in is not None
    assert paid_in.amount_pence == 10850
    # A late-evening payment in summer belongs to the next UK day.
    summer = parse_feed_item(feed(settlementTime="2026-07-31T23:30:00.000Z"))
    assert summer is not None
    assert summer.booked_date == date(2026, 8, 1)


def test_starling_asks_for_the_days_in_uk_time_and_refuses_cleanly() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"feedItems": [feed()]})

    rows = starling(handler).fetch_transactions(
        ProviderAccount("acc-1/cat-1", "Personal"), date(2026, 11, 1), date(2026, 11, 2)
    )
    assert len(rows) == 1
    assert seen[0].url.path == "/api/v2/feed/account/acc-1/category/cat-1/transactions-between"
    assert seen[0].url.params["minTransactionTimestamp"] == "2026-11-01T00:00:00.000Z"
    assert seen[0].url.params["maxTransactionTimestamp"] == "2026-11-03T00:00:00.000Z"
    with pytest.raises(ConsentExpired):
        starling(lambda _r: httpx.Response(403)).list_accounts()


def test_starling_reads_bills_paid_from_a_space() -> None:
    """Bills Manager pays Direct Debits from its own Space: they're only in its feed."""
    move = {"counterPartyType": "CATEGORY", "counterPartyUid": "bills", "reference": "Top up"}
    feeds = {
        "main": [
            feed(feedItemUid="card", counterPartyName="Tesco", counterPartyType="MERCHANT"),
            feed(feedItemUid="m1", counterPartyName="Bills", amount={"minorUnits": 50000}, **move),
        ],
        "bills": [
            feed(
                feedItemUid="m1",  # the same move, seen from the Space
                direction="IN",
                counterPartyName="Personal",
                counterPartyUid="main",
                counterPartyType="CATEGORY",
                amount={"minorUnits": 50000},
            ),
            feed(feedItemUid="dd", counterPartyName="EE", counterPartyType="PAYEE"),
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/account/acc-1/spaces":
            return httpx.Response(
                200,
                json={
                    "savingsGoals": [{"savingsGoalUid": "rainy", "name": "Rainy day"}],
                    "spendingSpaces": [{"spaceUid": "bills", "name": "Bills Manager"}],
                },
            )
        category = request.url.path.split("/category/")[1].split("/")[0]
        return httpx.Response(200, json={"feedItems": feeds[category]})

    rows = starling(handler).fetch_transactions(
        ProviderAccount("acc-1/main", "Joint"), date(2026, 10, 1), date(2026, 10, 23)
    )
    by_id = {r.external_id: r for r in rows}
    assert set(by_id) == {"card", "m1", "bills:m1", "bills:dd"}
    assert (by_id["bills:dd"].counterparty, by_id["bills:dd"].amount_pence) == ("EE", -10850)
    assert by_id["bills:dd"].bank_category == "BILLS_AND_SERVICES"
    # The move is ours both ways, and nets to nothing.
    assert {by_id["m1"].bank_category, by_id["bills:m1"].bank_category} == {"pot_transfer"}
    assert by_id["m1"].counterparty == "Space: Bills Manager"
    assert by_id["bills:m1"].counterparty == "Space: Main balance"
    assert by_id["m1"].amount_pence + by_id["bills:m1"].amount_pence == 0
    assert by_id["card"].bank_category == "BILLS_AND_SERVICES"


def test_starling_without_space_scope_still_reads_the_main_feed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/spaces"):
            return httpx.Response(403)
        return httpx.Response(200, json={"feedItems": [feed()]})

    rows = starling(handler).fetch_transactions(
        ProviderAccount("acc-1/cat-1", "Personal"), date(2026, 11, 1), date(2026, 11, 2)
    )
    assert [r.external_id for r in rows] == ["f1"]


# ---- the demo bank ---------------------------------------------------------------------


def test_demo_rows_are_stable_and_settle_under_new_ids() -> None:
    before = DemoProvider(today=date(2026, 11, 29)).fetch_transactions(
        CURRENT, date(2026, 11, 27), date(2026, 11, 29)
    )
    after = DemoProvider(today=date(2026, 12, 1)).fetch_transactions(
        CURRENT, date(2026, 11, 27), date(2026, 12, 1)
    )
    salary_then = next(r for r in before if r.amount_pence == 300000)
    salary_now = next(r for r in after if r.amount_pence == 300000)
    assert (salary_then.status, salary_now.status) == ("pending", "booked")
    assert salary_then.external_id != salary_now.external_id
