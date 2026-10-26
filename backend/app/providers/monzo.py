"""Monzo Developer API (PLAN.md §8.1, decision 29): Person 1's Monzo, which Enable Banking lacks.

Monzo's API is for personal use: a confidential OAuth client made in Monzo's developer
portal, used only by the people it's set up for. Linking:

1. The browser goes to auth.monzo.com and comes back with a `code`.
2. The code is exchanged for an access token (about six hours) and a refresh token.
3. Nothing can be read until the account holder approves access in the Monzo app.
4. For five minutes after that approval the whole history can be read; after that, only
   the last 90 days. So the first sync happens straight away.

Each refresh replaces both tokens, so the new pair is handed back to be saved at once.
Read-only: the pot deposit and withdraw calls exist in Monzo's API and are never used here.
"""

import contextlib
import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from app.providers.base import (
    ConsentExpired,
    ProviderAccount,
    ProviderBalance,
    ProviderError,
    ProviderTransaction,
    RateLimited,
)

UK = ZoneInfo("Europe/London")
AUTH_URL = "https://auth.monzo.com/"
PAGE = 100
REFRESH_EARLY = 120  # seconds before expiry to refresh


class AwaitingApproval(ProviderError):
    """Signed in, but access hasn't been approved in the Monzo app yet."""


def authorize_url(client_id: str, redirect_url: str, state: str) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_url,
        "response_type": "code",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(query)}"


def _tokens(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_at": int(time.time()) + int(body.get("expires_in") or 21600),
        "user_id": body.get("user_id"),
    }


def exchange_code(
    client_id: str,
    client_secret: str,
    redirect_url: str,
    code: str,
    http: httpx.Client,
) -> dict[str, Any]:
    """The code from the callback, for a token pair (as the JSON kept, encrypted)."""
    response = http.post(
        "/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_url,
            "code": code,
        },
    )
    if not response.is_success:
        raise ProviderError("Monzo didn't accept that sign-in. Try linking again.")
    return _tokens(response.json())


def _day(stamp: str) -> date:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UK).date()


def parse_transaction(
    raw: dict[str, Any], pots: dict[str, str] | None = None
) -> ProviderTransaction | None:
    if raw.get("decline_reason"):
        return None  # declined: no money moved
    settled = raw.get("settled") or ""
    status: Literal["pending", "booked"] = "booked" if settled else "pending"
    merchant = raw.get("merchant")
    merchant_name = merchant.get("name") if isinstance(merchant, dict) else None
    counterparty = (raw.get("counterparty") or {}).get("name")
    pot_id = (raw.get("metadata") or {}).get("pot_id")
    category = raw.get("category")
    description = str(raw.get("description") or "")
    if pot_id:
        # Money moved to or from one of our own pots: labelled, and kept out of the
        # recurring-payment proposals (see sync/recurring.py).
        counterparty = f"Pot: {(pots or {}).get(pot_id, 'a pot')}"
        description = counterparty
        category = "pot_transfer"
    return ProviderTransaction(
        external_id=str(raw["id"]),
        booked_date=_day(settled or raw["created"]),
        value_date=_day(raw["created"]),
        amount_pence=int(raw["amount"]),
        description=description or merchant_name or counterparty or "",
        counterparty=merchant_name or counterparty,
        status=status,
        bank_category=category,
        raw=raw,
    )


class MonzoProvider:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tokens: dict[str, Any],
        on_tokens: Callable[[dict[str, Any]], None] | None = None,
        base_url: str = "https://api.monzo.com",
        http: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.tokens = tokens
        self.on_tokens = on_tokens
        self._http = http or httpx.Client(base_url=base_url, timeout=30)
        self._pots: dict[str, dict[str, str]] = {}

    # ---- tokens ------------------------------------------------------------------------

    def _refresh(self) -> None:
        if not self.tokens.get("refresh_token"):
            raise ConsentExpired("The Monzo sign-in has ended. Re-link Monzo to carry on.")
        response = self._http.post(
            "/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.tokens["refresh_token"],
            },
        )
        if not response.is_success:
            raise ConsentExpired("The Monzo sign-in has ended. Re-link Monzo to carry on.")
        self.tokens = _tokens(response.json()) | {"user_id": self.tokens.get("user_id")}
        if self.on_tokens is not None:
            self.on_tokens(self.tokens)  # each refresh replaces the old pair: save it now

    def _get(self, path: str, params: Any = None, retried: bool = False) -> dict[str, Any]:
        if int(self.tokens.get("expires_at") or 0) - REFRESH_EARLY < time.time():
            self._refresh()
        try:
            response = self._http.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self.tokens['access_token']}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError("Couldn't reach Monzo. Try again shortly.") from exc
        if response.status_code == 401 and not retried:
            self._refresh()
            return self._get(path, params, retried=True)
        if response.status_code == 401:
            raise ConsentExpired("The Monzo sign-in has ended. Re-link Monzo to carry on.")
        if response.status_code == 403:
            raise AwaitingApproval(
                "Monzo is waiting for you to approve access: open the Monzo app and allow it."
            )
        if response.status_code == 429:
            raise RateLimited("Monzo asked us to slow down. The next sync will catch up.")
        if not response.is_success:
            raise ProviderError(f"Monzo said {response.status_code}")
        data: dict[str, Any] = response.json()
        return data

    # ---- data --------------------------------------------------------------------------

    def list_accounts(self) -> list[ProviderAccount]:
        out = []
        for acc in self._get("/accounts").get("accounts", []):
            if acc.get("closed"):
                continue
            kind = str(acc.get("type") or "")
            number = str(acc.get("account_number") or "")
            label = "Joint account" if kind == "uk_retail_joint" else "Personal account"
            out.append(
                ProviderAccount(
                    uid=str(acc["id"]),
                    name=f"Monzo {label.lower()}",
                    currency=str(acc.get("currency") or "GBP"),
                    identification_hash=f"monzo:{acc['id']}",
                    detail=f"{label} …{number[-4:]}" if len(number) >= 4 else label,
                )
            )
        return out

    def _pot_names(self, account_id: str) -> dict[str, str]:
        if account_id not in self._pots:
            try:
                pots = self._get("/pots", {"current_account_id": account_id}).get("pots", [])
            except ProviderError:
                pots = []
            self._pots[account_id] = {str(p["id"]): str(p.get("name") or "a pot") for p in pots}
        return self._pots[account_id]

    def fetch_transactions(
        self, account: ProviderAccount, since: date | None, until: date
    ) -> list[ProviderTransaction]:
        pots = self._pot_names(account.uid)
        before = datetime.combine(until + timedelta(days=1), datetime.min.time(), tzinfo=UK)
        cursor = (
            datetime.combine(since, datetime.min.time(), tzinfo=UK).astimezone(UTC).isoformat()
            if since
            else None
        )
        out: list[ProviderTransaction] = []
        for _ in range(500):  # a bound on paging
            params: list[tuple[str, str]] = [
                ("account_id", account.uid),
                ("limit", str(PAGE)),
                ("expand[]", "merchant"),
                ("before", before.astimezone(UTC).isoformat().replace("+00:00", "Z")),
            ]
            if cursor:
                params.append(("since", cursor.replace("+00:00", "Z")))
            page = self._get("/transactions", params).get("transactions", [])
            for raw in page:
                txn = parse_transaction(raw, pots)
                if txn is not None:
                    out.append(txn)
            if len(page) < PAGE:
                return out
            cursor = str(page[-1]["id"])  # the next page starts after the last one seen
        raise ProviderError("Monzo kept sending more pages; stopped.")

    def fetch_balance(self, account: ProviderAccount) -> ProviderBalance | None:
        data = self._get("/balance", {"account_id": account.uid})
        if "balance" not in data:
            return None
        return ProviderBalance(int(data["balance"]), datetime.now(UTC))

    def logout(self) -> None:
        with contextlib.suppress(httpx.HTTPError):
            self._http.post(
                "/oauth2/logout",
                headers={"Authorization": f"Bearer {self.tokens['access_token']}"},
            )


def dump(tokens: dict[str, Any]) -> str:
    return json.dumps(tokens)


def load(secret: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(secret)
    return data
