"""Starling personal access token (PLAN.md §8.1): Person 1 Starling and the joint account.

Make the token in the Starling developer portal with read scopes only (account:read,
account-identifier:read, balance:read, transaction:read, space:read). It doesn't expire, but
it can be revoked, which shows up here as a refused call.

Starling keeps a feed per category: the main balance, and one per Space. Bills Manager is a
spending Space that pays Direct Debits and standing orders itself, so those payments are
only in its feed, never the main one. Each sync reads the main feed and every spending
Space's. Money moving between the main balance and a Space is ours, labelled
`pot_transfer` like a Monzo pot, and both sides are kept, so the account still adds up.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
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
# Feed statuses that are money that moved (or is moving); the rest never happened.
_BOOKED = {"SETTLED", "REFUNDED"}
_PENDING = {"PENDING"}


def _stamp(d: date, end: bool = False) -> str:
    moment = datetime.combine(d + timedelta(days=1) if end else d, time(0), tzinfo=UK)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _uk_day(text: str) -> date:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UK).date()


def parse_feed_item(
    raw: dict[str, Any], space: str | None = None, spaces: dict[str, str] | None = None
) -> ProviderTransaction | None:
    """One feed item. `space` is the Space's category when it came from a Space's feed (its
    ids are kept apart from the main feed's: a move between them shows in both); `spaces`
    names every category of ours, so a move between them is labelled as one."""
    status = raw.get("status")
    kind: Literal["pending", "booked"]
    if status in _BOOKED:
        kind = "booked"
    elif status in _PENDING:
        kind = "pending"
    else:
        return None
    pence = int(raw["amount"]["minorUnits"])
    if raw.get("direction") == "OUT":
        pence = -pence
    when = raw.get("settlementTime") or raw.get("transactionTime")
    if not when:
        return None
    counterparty = raw.get("counterPartyName")
    category = raw.get("spendingCategory")
    other = str(raw.get("counterPartyUid") or "")
    if raw.get("counterPartyType") == "CATEGORY" or other in (spaces or {}):
        # Our own money moving between the main balance and a Space.
        counterparty = f"Space: {(spaces or {}).get(other) or counterparty or 'a Space'}"
        category = "pot_transfer"
    uid = str(raw["feedItemUid"])
    return ProviderTransaction(
        external_id=f"{space}:{uid}" if space else uid,
        booked_date=_uk_day(str(when)),
        amount_pence=pence,
        description=str(raw.get("reference") or counterparty or ""),
        counterparty=counterparty,
        status=kind,
        bank_category=category,
        raw=raw,
    )


class StarlingProvider:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.starlingbank.com",
        http: httpx.Client | None = None,
    ) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=30)
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            response = self._http.get(path, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ProviderError("Couldn't reach Starling. Try again shortly.") from exc
        if response.status_code in (401, 403):
            raise ConsentExpired(
                "Starling refused the token. It may have been revoked: add a new one."
            )
        if response.status_code == 429:
            raise RateLimited("Starling asked us to slow down. The next sync will catch up.")
        if not response.is_success:
            raise ProviderError(f"Starling said {response.status_code}")
        data: dict[str, Any] = response.json()
        return data

    def list_accounts(self) -> list[ProviderAccount]:
        out = []
        for acc in self._get("/api/v2/accounts").get("accounts", []):
            kind = str(acc.get("accountType") or "")
            out.append(
                ProviderAccount(
                    # The feed is per category; the default category holds the main balance.
                    uid=f"{acc['accountUid']}/{acc['defaultCategory']}",
                    name=str(acc.get("name") or kind.title() or "Account"),
                    currency=str(acc.get("currency") or "GBP"),
                    identification_hash=f"starling:{acc['accountUid']}",
                    detail=kind.replace("_", " ").title() or None,
                )
            )
        return out

    def fetch_transactions(
        self, account: ProviderAccount, since: date | None, until: date
    ) -> list[ProviderTransaction]:
        account_uid, main = account.uid.split("/", 1)
        start = since or until - timedelta(days=365)
        window = {
            "minTransactionTimestamp": _stamp(start),
            "maxTransactionTimestamp": _stamp(until, end=True),
        }

        def feed(category: str) -> list[dict[str, Any]]:
            path = f"/api/v2/feed/account/{account_uid}/category/{category}/transactions-between"
            items: list[dict[str, Any]] = self._get(path, window).get("feedItems", [])
            return items

        main_items = feed(main)
        spaces = self._spaces(account_uid)
        names = {main: "Main balance"} | spaces
        rows = [parse_feed_item(i, None, names) for i in main_items]
        for space in spaces:
            rows += [parse_feed_item(i, space, names) for i in feed(space)]
        return [t for t in rows if t]

    def _spaces(self, account_uid: str) -> dict[str, str]:
        """Spending Spaces (Bills Manager, card Spaces) by category: payments are made from
        them. Savings Spaces only ever take money in from the main balance, which the main
        feed already shows, so they're left out."""
        try:
            data = self._get(f"/api/v2/account/{account_uid}/spaces")
        except ConsentExpired:
            # A token made without space:read: the main feed still works.
            return {}
        return {
            str(s["spaceUid"]): str(s.get("name") or "a Space")
            for s in data.get("spendingSpaces", [])
            if s.get("spaceUid")
        }

    def fetch_balance(self, account: ProviderAccount) -> ProviderBalance | None:
        account_uid = account.uid.split("/", 1)[0]
        data = self._get(f"/api/v2/accounts/{account_uid}/balance")
        money = data.get("effectiveBalance") or data.get("clearedBalance")
        if not money:
            return None
        return ProviderBalance(int(money["minorUnits"]), datetime.now(UTC))
