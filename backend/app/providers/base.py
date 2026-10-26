"""What every bank provider gives us (PLAN.md §8.3). Read-only: there is no payment call.

A provider is bound to one connection (an Enable Banking session, a Starling token) and
returns plain data; storing it, de-duplicating and matching are the sync layer's job.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol


class ProviderError(RuntimeError):
    """The bank or provider refused or failed. The message is safe to show a person."""


class ConsentExpired(ProviderError):
    """The consent has lapsed or was revoked at the bank: the owner needs to re-link."""


class RateLimited(ProviderError):
    """The bank's limit on syncs was hit; try again later."""


@dataclass(frozen=True)
class ProviderAccount:
    uid: str  # the provider's handle for the account on this connection
    name: str
    currency: str = "GBP"
    # Stable across sessions (and the same for the joint account on both people's links).
    identification_hash: str | None = None
    detail: str | None = None  # e.g. "Joint account", "…1234"


@dataclass(frozen=True)
class ProviderTransaction:
    external_id: str
    booked_date: date
    amount_pence: int  # money out negative
    description: str
    status: Literal["pending", "booked"] = "booked"
    counterparty: str | None = None
    value_date: date | None = None
    bank_category: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderBalance:
    amount_pence: int
    as_of: datetime


class BankProvider(Protocol):
    def list_accounts(self) -> list[ProviderAccount]: ...

    def fetch_transactions(
        self, account: ProviderAccount, since: date | None, until: date
    ) -> list[ProviderTransaction]: ...

    def fetch_balance(self, account: ProviderAccount) -> ProviderBalance | None: ...
