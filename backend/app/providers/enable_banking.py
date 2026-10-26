"""Enable Banking (PLAN.md §8.1, §8.3): the primary route for Monzo and RBS.

Every call carries a short-lived JWT signed RS256 with the application's private key, with
`kid` the application id. Linking: POST /auth gives the bank's sign-in URL; the bank sends
the person back with a `code`; POST /sessions turns it into a session and its accounts.
Data: GET /accounts/{uid}/transactions (paged by continuation_key) and /balances.

Read-only: only the account information calls exist here. Never add a payment call.
"""

import hashlib
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
import jwt

from app.providers.base import (
    ConsentExpired,
    ProviderAccount,
    ProviderBalance,
    ProviderError,
    ProviderTransaction,
    RateLimited,
)

# Errors that mean the consent is gone and only a re-link will help.
_GONE = {
    "EXPIRED_SESSION",
    "REVOKED_SESSION",
    "CLOSED_SESSION",
    "SESSION_DOES_NOT_EXIST",
    "WRONG_SESSION_STATUS",
    "ACCOUNT_DOES_NOT_EXIST",
}
# Preferred balance, most useful first: interim available, closing available, expected.
_BALANCES = ("ITAV", "CLAV", "XPCD", "ITBD", "CLBD", "OPBD")
_TOKEN_SECONDS = 3600


class EnableBankingClient:
    def __init__(
        self,
        app_id: str,
        private_key: bytes,
        base_url: str = "https://api.enablebanking.com",
        http: httpx.Client | None = None,
    ) -> None:
        self.app_id = app_id
        self._key = private_key
        # An injected client (tests) brings its own base URL and transport.
        self._http = http or httpx.Client(base_url=base_url, timeout=30)

    def _token(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": "enablebanking.com",
                "aud": "api.enablebanking.com",
                "iat": now,
                "exp": now + _TOKEN_SECONDS,
            },
            self._key,
            algorithm="RS256",
            headers={"kid": self.app_id},
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        psu_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token()}", **(psu_headers or {})}
        try:
            response = self._http.request(method, path, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError("Couldn't reach Enable Banking. Try again shortly.") from exc
        if response.is_success:
            return response.json() if response.content else {}
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = str(body.get("error") or "")
        message = str(body.get("message") or f"Enable Banking said {response.status_code}")
        if error in _GONE or response.status_code == 401:
            raise ConsentExpired("The bank consent has ended. Re-link the account to carry on.")
        if response.status_code == 429 or error == "ASPSP_RATE_LIMIT_EXCEEDED":
            raise RateLimited("The bank's limit on syncs was reached. The next sync will catch up.")
        raise ProviderError(message)

    # ---- linking -----------------------------------------------------------------------

    def aspsps(self, country: str = "GB") -> list[dict[str, Any]]:
        data = self.request("GET", "/aspsps", params={"country": country, "psu_type": "personal"})
        return list(data.get("aspsps", []))

    def start_auth(
        self,
        bank_name: str,
        country: str,
        redirect_url: str,
        state: str,
        valid_until: datetime,
    ) -> str:
        data = self.request(
            "POST",
            "/auth",
            json={
                # Account information only: balances and transactions, never payments.
                "access": {"valid_until": valid_until.isoformat()},
                "aspsp": {"name": bank_name, "country": country},
                "state": state,
                "redirect_url": redirect_url,
                "psu_type": "personal",
            },
        )
        return str(data["url"])

    def create_session(self, code: str) -> dict[str, Any]:
        return self.request("POST", "/sessions", json={"code": code})

    def delete_session(self, session_id: str) -> None:
        self.request("DELETE", f"/sessions/{session_id}")


def _pence(amount: object) -> int:
    try:
        value = Decimal(str(amount)) * 100
    except InvalidOperation as exc:
        raise ProviderError(f"Enable Banking sent an amount that isn't one: {amount!r}") from exc
    if value != value.to_integral_value():
        raise ProviderError(f"Enable Banking sent fractions of a penny: {amount!r}")
    return int(value)


def _day(text: object) -> date | None:
    return date.fromisoformat(str(text)[:10]) if text else None


def accounts_from_session(session: dict[str, Any]) -> list[ProviderAccount]:
    """The accounts in a POST /sessions response."""
    out = []
    for acc in session.get("accounts", []):
        ids = acc.get("account_id") or {}
        number = ids.get("iban") or (ids.get("other") or {}).get("identification") or ""
        detail = f"…{number[-4:]}" if len(number) >= 4 else acc.get("product") or None
        out.append(
            ProviderAccount(
                uid=str(acc["uid"]),
                name=str(acc.get("name") or acc.get("product") or "Account"),
                currency=str(acc.get("currency") or "GBP"),
                identification_hash=acc.get("identification_hash"),
                detail=detail,
            )
        )
    return out


def parse_transaction(raw: dict[str, Any], occurrence: int = 0) -> ProviderTransaction | None:
    status = raw.get("status")
    kind: Literal["pending", "booked"]
    if status == "BOOK":
        kind = "booked"
    elif status in ("PDNG", "HOLD"):
        kind = "pending"
    else:
        return None  # cancelled, rejected, scheduled: not money that moved
    money = raw.get("transaction_amount") or {}
    pence = abs(_pence(money.get("amount", "0")))
    if raw.get("credit_debit_indicator") == "DBIT":
        pence = -pence
    when = _day(raw.get("booking_date")) or _day(raw.get("transaction_date"))
    when = when or _day(raw.get("value_date"))
    if when is None:
        return None
    party = raw.get("creditor") if pence < 0 else raw.get("debtor")
    counterparty = (party or {}).get("name")
    text = " ".join(str(r) for r in raw.get("remittance_information") or []).strip()
    description = text or counterparty or ""
    external = raw.get("transaction_id") or raw.get("entry_reference")
    if not external:
        key = f"{when}|{pence}|{description}|{counterparty}|{kind}|{occurrence}"
        external = "eb:" + hashlib.sha256(key.encode()).hexdigest()[:40]
    return ProviderTransaction(
        external_id=str(external),
        booked_date=when,
        value_date=_day(raw.get("value_date")),
        amount_pence=pence,
        description=description,
        counterparty=counterparty,
        status=kind,
        raw=raw,
    )


class EnableBankingSession:
    """One linked session, as a BankProvider."""

    def __init__(
        self,
        client: EnableBankingClient,
        session_id: str,
        psu_headers: dict[str, str] | None = None,
    ) -> None:
        self.client = client
        self.session_id = session_id
        # Sent when a person is present (Sync now), so the bank doesn't count it as unattended.
        self.psu_headers = psu_headers

    def list_accounts(self) -> list[ProviderAccount]:
        data = self.client.request("GET", f"/sessions/{self.session_id}")
        hashes = {a.get("uid"): a.get("identification_hash") for a in data.get("accounts_data", [])}
        return [
            ProviderAccount(uid=str(uid), name="Account", identification_hash=hashes.get(uid))
            for uid in data.get("accounts", [])
        ]

    def fetch_transactions(
        self, account: ProviderAccount, since: date | None, until: date
    ) -> list[ProviderTransaction]:
        params: dict[str, Any] = {"date_to": until.isoformat()}
        if since is not None:
            params["date_from"] = since.isoformat()
        else:
            params["strategy"] = "longest"  # as much history as the bank will give
        out: list[ProviderTransaction] = []
        seen: dict[tuple[str, str, str], int] = {}
        for _ in range(200):  # a bound on paging, in case a bank loops
            data = self.client.request(
                "GET",
                f"/accounts/{account.uid}/transactions",
                params=params,
                psu_headers=self.psu_headers,
            )
            for raw in data.get("transactions", []):
                # Two identical payments on one day with no bank id must stay two rows.
                key = (
                    str(raw.get("booking_date")),
                    str((raw.get("transaction_amount") or {}).get("amount")),
                    str(raw.get("remittance_information")),
                )
                occurrence = seen.get(key, 0)
                seen[key] = occurrence + 1
                txn = parse_transaction(raw, occurrence)
                if txn is not None:
                    out.append(txn)
            continuation = data.get("continuation_key")
            if not continuation:
                return out
            params = {**params, "continuation_key": continuation}
        raise ProviderError("The bank kept sending more pages; stopped after 200.")

    def fetch_balance(self, account: ProviderAccount) -> ProviderBalance | None:
        data = self.client.request(
            "GET", f"/accounts/{account.uid}/balances", psu_headers=self.psu_headers
        )
        balances = data.get("balances", [])
        for kind in _BALANCES:
            for b in balances:
                if b.get("balance_type") == kind:
                    stamp = b.get("last_change_date_time") or b.get("reference_date")
                    as_of = (
                        datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                        if stamp and "T" in str(stamp)
                        else datetime.now().astimezone()
                    )
                    return ProviderBalance(_pence(b["balance_amount"]["amount"]), as_of)
        return None
