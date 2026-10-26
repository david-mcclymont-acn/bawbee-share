"""Bank connections: link, map, sync, consent and disconnect (PLAN.md §8.1-8.3, §13).

- Each connection belongs to the person who linked it. Both of us can see it and sync it,
  but only its owner can re-link or disconnect it, so Person 2 re-links their own RBS.
- The joint account is linked once (decision 21). If it turns up on the other person's
  connection too, its identification hash gives it away and it can't be mapped twice.
- The bank covers an account from the day after its last CSV row, decided when it's mapped.
- Each sync re-fetches the last week, so pending payments are seen as they book; storage is
  idempotent (see ingest.ingest_bank), so re-fetching adds nothing twice.
- Consents ending within 14 days raise an inbox item for the owner to re-link.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import crypto
from app.config import Settings
from app.db import utcnow
from app.domain import audit
from app.domain.auth import as_utc
from app.domain.models import (
    Account,
    BankAccountLink,
    BankConnection,
    BankLinkFlow,
    BankTransaction,
    InboxItem,
    SyncRun,
    User,
)
from app.providers import demo, monzo
from app.providers.base import (
    BankProvider,
    ConsentExpired,
    ProviderAccount,
    ProviderError,
    RateLimited,
)
from app.providers.enable_banking import (
    EnableBankingClient,
    EnableBankingSession,
    accounts_from_session,
)
from app.providers.starling_pat import StarlingProvider
from app.sync import ingest, matching

logger = logging.getLogger("bawbee.banks")
UK = ZoneInfo("Europe/London")

CONSENT_WARNING = timedelta(days=14)
DEFAULT_CONSENT_DAYS = 90  # when the bank doesn't say how long it allows
MAX_CONSENT_DAYS = 180
OVERLAP = timedelta(days=7)  # each sync re-fetches the last week
FLOW_MINUTES = 30


class BankError(ValueError):
    """Something a person did that can't be done; the message says why."""


class NotOwner(BankError):
    pass


@dataclass(frozen=True)
class LinkResult:
    connection: BankConnection
    relinked: bool
    # A re-link that brought bank accounts we haven't mapped yet (so ask which they feed).
    new_accounts: bool
    # A Monzo sign-in waiting to be approved in the Monzo app.
    pending: bool = False


@dataclass(frozen=True)
class Bank:
    name: str
    country: str
    provider: str  # enable_banking | monzo | demo
    max_consent_days: int | None = None
    beta: bool = False


# ---- providers ---------------------------------------------------------------------------


def eb_client(settings: Settings) -> EnableBankingClient:
    if not settings.enable_banking_enabled or settings.enable_banking_key_file is None:
        raise BankError("Enable Banking isn't set up on this server (see docs/providers.md).")
    try:
        key = settings.enable_banking_key_file.read_bytes()
    except OSError as exc:
        raise BankError("The Enable Banking private key can't be read.") from exc
    return EnableBankingClient(
        settings.enable_banking_app_id or "", key, settings.enable_banking_api
    )


def monzo_http(settings: Settings) -> httpx.Client:
    return httpx.Client(base_url=settings.monzo_api, timeout=30)


def monzo_for(settings: Settings, connection: BankConnection) -> monzo.MonzoProvider:
    """Monzo with this connection's tokens; a refreshed pair is saved straight back."""
    if not settings.monzo_enabled:
        raise BankError("Monzo isn't set up on this server (see docs/providers.md).")

    def save(tokens: dict[str, Any]) -> None:
        connection.secret_encrypted = crypto.encrypt(settings, monzo.dump(tokens))

    return monzo.MonzoProvider(
        settings.monzo_client_id or "",
        settings.monzo_secret or "",
        monzo.load(crypto.decrypt(settings, connection.secret_encrypted)),
        on_tokens=save,
        http=monzo_http(settings),
    )


def provider_for(
    settings: Settings, connection: BankConnection, psu_headers: dict[str, str] | None = None
) -> BankProvider:
    if connection.provider == "monzo":
        return monzo_for(settings, connection)
    secret = crypto.decrypt(settings, connection.secret_encrypted)
    if connection.provider == "enable_banking":
        return EnableBankingSession(eb_client(settings), secret, psu_headers)
    if connection.provider == "starling_pat":
        return StarlingProvider(secret, settings.starling_api)
    if connection.provider == "demo" and settings.banks_demo:
        return demo.DemoProvider()
    raise BankError("This connection's provider isn't available on this server.")


def available_banks(settings: Settings) -> list[Bank]:
    banks: list[Bank] = []
    if settings.monzo_enabled:
        banks.append(Bank("Monzo", "GB", "monzo"))
    if settings.banks_demo:
        banks.append(Bank(demo.BANK_NAME, "GB", "demo", DEFAULT_CONSENT_DAYS))
    if settings.enable_banking_enabled:
        for a in eb_client(settings).aspsps("GB"):
            seconds = a.get("maximum_consent_validity")
            banks.append(
                Bank(
                    name=str(a["name"]),
                    country=str(a.get("country") or "GB"),
                    provider="enable_banking",
                    max_consent_days=int(seconds) // 86400 if seconds else None,
                    beta=bool(a.get("beta")),
                )
            )
    return banks


# ---- ownership ---------------------------------------------------------------------------


def _owned(connection: BankConnection, user: User) -> None:
    if connection.owner_user_id != user.id:
        raise NotOwner(
            f"Only {connection.owner.display_name} can change this link: it's their bank login."
        )


def _resolve_consent_items(session: Session, connection_id: int, user: User | None) -> None:
    for item in session.scalars(
        select(InboxItem).where(
            InboxItem.type == "consent_expiring",
            InboxItem.status.in_(("open", "snoozed")),
        )
    ):
        if item.payload_json.get("connection_id") == connection_id:
            item.status = "approved"
            item.resolved_by = user.id if user else None
            item.resolved_at = utcnow()


# ---- linking -------------------------------------------------------------------------------


def start_link(
    session: Session,
    settings: Settings,
    user: User,
    bank: Bank,
    relink: BankConnection | None = None,
) -> str:
    """Begin a bank sign-in; returns the URL to send the browser to."""
    if not crypto.can_encrypt(settings):
        raise BankError("This server has no encryption key for bank links yet (see docs).")
    if relink is not None:
        _owned(relink, user)
    state = secrets.token_urlsafe(24)
    session.add(
        BankLinkFlow(
            state=state,
            user_id=user.id,
            provider=bank.provider,
            bank_name=bank.name,
            bank_country=bank.country,
            relink_connection_id=relink.id if relink else None,
        )
    )
    session.flush()
    audit.record(
        session,
        user,
        "bank_connection",
        relink.id if relink else None,
        "link_start",
        None,
        {"bank": bank.name, "provider": bank.provider},
    )
    if bank.provider == "demo":
        if not settings.banks_demo:
            raise BankError("The demo bank is switched off.")
        return f"{settings.bank_redirect_url}?state={state}&code=demo"
    if bank.provider == "monzo":
        if not settings.monzo_enabled:
            raise BankError("Monzo isn't set up on this server (see docs/providers.md).")
        return monzo.authorize_url(
            settings.monzo_client_id or "", settings.bank_redirect_url, state
        )
    days = min(bank.max_consent_days or DEFAULT_CONSENT_DAYS, MAX_CONSENT_DAYS)
    return eb_client(settings).start_auth(
        bank.name, bank.country, settings.bank_redirect_url, state, utcnow() + timedelta(days=days)
    )


def _flow(session: Session, user: User, state: str) -> BankLinkFlow:
    flow = session.scalars(select(BankLinkFlow).where(BankLinkFlow.state == state)).first()
    if flow is None or flow.used_at is not None:
        raise BankError("That bank sign-in has already been used or never started. Try again.")
    if flow.user_id != user.id:
        raise BankError("That bank sign-in was started by someone else.")
    if utcnow() - as_utc(flow.created_at) > timedelta(minutes=FLOW_MINUTES):
        raise BankError("That bank sign-in took too long. Start it again.")
    flow.used_at = utcnow()
    return flow


def _attach(
    session: Session,
    settings: Settings,
    user: User,
    *,
    provider: str,
    bank_name: str,
    bank_country: str,
    secret: str,
    expires: datetime | None,
    accounts: list[ProviderAccount],
    replace: BankConnection | None,
    pending: bool = False,
) -> BankConnection:
    """Create a connection, or put a re-link's new session onto the old one."""
    if replace is None:
        connection = BankConnection(
            owner_user_id=user.id,
            provider=provider,
            bank_name=bank_name,
            bank_country=bank_country,
            secret_encrypted=crypto.encrypt(settings, secret),
            consent_expires_at=expires,
            status="pending" if pending else "active",
        )
        session.add(connection)
        session.flush()
        for acc in accounts:
            connection.accounts.append(_link_row(acc))
        action = "link"
    else:
        connection = replace
        old_secret = crypto.decrypt(settings, connection.secret_encrypted)
        if connection.provider == "enable_banking" and old_secret != secret:
            try:
                eb_client(settings).delete_session(old_secret)
            except (ProviderError, BankError):
                logger.info("old Enable Banking session %s already gone", connection.id)
        connection.secret_encrypted = crypto.encrypt(settings, secret)
        connection.consent_expires_at = expires
        connection.status = "pending" if pending else "active"
        connection.last_error = None
        # Same account, new session: carry the mapping over by its identification hash.
        by_hash = {link.identification_hash: link for link in connection.accounts}
        offered = set()
        for acc in accounts:
            link = by_hash.get(acc.identification_hash) if acc.identification_hash else None
            if link is None:
                link = next((x for x in connection.accounts if x.external_uid == acc.uid), None)
            if link is None:
                connection.accounts.append(_link_row(acc))
            else:
                link.external_uid, link.name, link.detail = acc.uid, acc.name, acc.detail
            offered.add(acc.uid)
        for link in list(connection.accounts):
            if not pending and link.external_uid not in offered and link.id is not None:
                connection.accounts.remove(link)  # the bank no longer gives this account
        _resolve_consent_items(session, connection.id, user)
        action = "relink"
    session.flush()
    audit.record(
        session,
        user,
        "bank_connection",
        connection.id,
        action,
        None,
        {"bank": bank_name, "accounts": [a.name for a in accounts]},
    )
    return connection


def _link_row(acc: ProviderAccount) -> BankAccountLink:
    return BankAccountLink(
        external_uid=acc.uid,
        identification_hash=acc.identification_hash,
        name=acc.name,
        detail=acc.detail,
        currency=acc.currency,
    )


def complete_link(
    session: Session, settings: Settings, user: User, state: str, code: str
) -> LinkResult:
    """The bank sent the browser back with a code: make the session and its accounts."""
    flow = _flow(session, user, state)
    replace = session.get(BankConnection, flow.relink_connection_id or 0)
    pending = False
    if flow.provider == "demo":
        if not settings.banks_demo:
            raise BankError("The demo bank is switched off.")
        secret, accounts = f"demo:{state}", demo.DemoProvider().list_accounts()
        expires: datetime | None = utcnow() + timedelta(days=DEFAULT_CONSENT_DAYS)
    elif flow.provider == "monzo":
        if not settings.monzo_enabled:
            raise BankError("Monzo isn't set up on this server (see docs/providers.md).")
        tokens = monzo.exchange_code(
            settings.monzo_client_id or "",
            settings.monzo_secret or "",
            settings.bank_redirect_url,
            code,
            monzo_http(settings),
        )
        secret, expires = monzo.dump(tokens), None  # Monzo gives no consent end date
        try:
            accounts = monzo.MonzoProvider(
                settings.monzo_client_id or "",
                settings.monzo_secret or "",
                tokens,
                http=monzo_http(settings),
            ).list_accounts()
        except monzo.AwaitingApproval:
            accounts, pending = [], True
    else:
        data = eb_client(settings).create_session(code)
        secret, accounts = str(data["session_id"]), accounts_from_session(data)
        until = (data.get("access") or {}).get("valid_until")
        expires = datetime.fromisoformat(str(until).replace("Z", "+00:00")) if until else None
    before = {link.external_uid for link in replace.accounts} if replace else set()
    connection = _attach(
        session,
        settings,
        user,
        provider=flow.provider,
        bank_name=flow.bank_name,
        bank_country=flow.bank_country,
        secret=secret,
        expires=expires,
        accounts=accounts,
        replace=replace,
        pending=pending,
    )
    fresh = {link.external_uid for link in connection.accounts} - before
    return LinkResult(
        connection, replace is not None, replace is None or bool(fresh), pending=pending
    )


def continue_link(
    session: Session, settings: Settings, connection: BankConnection, user: User
) -> LinkResult:
    """After approving access in the Monzo app: fetch the accounts and carry on."""
    _owned(connection, user)
    if connection.provider != "monzo" or connection.status != "pending":
        raise BankError("That link isn't waiting for approval.")
    try:
        accounts = monzo_for(settings, connection).list_accounts()
    except monzo.AwaitingApproval as exc:
        raise BankError(
            "Monzo hasn't been approved yet. Open the Monzo app, allow access, then try again."
        ) from exc
    before = {link.external_uid for link in connection.accounts}
    by_hash = {link.identification_hash: link for link in connection.accounts}
    for acc in accounts:
        link = by_hash.get(acc.identification_hash)
        if link is None:
            connection.accounts.append(_link_row(acc))
        else:
            link.external_uid, link.name, link.detail = acc.uid, acc.name, acc.detail
    connection.status = "active"
    connection.last_error = None
    session.flush()
    audit.record(
        session,
        user,
        "bank_connection",
        connection.id,
        "approved",
        None,
        {"accounts": [a.name for a in accounts]},
    )
    fresh = {link.external_uid for link in connection.accounts} - before
    return LinkResult(connection, bool(before), not before or bool(fresh))


def add_starling(
    session: Session,
    settings: Settings,
    user: User,
    token: str,
    replace: BankConnection | None = None,
) -> BankConnection:
    """Link (or replace) a Starling personal access token, checking it works first."""
    if not crypto.can_encrypt(settings):
        raise BankError("This server has no encryption key for bank links yet (see docs).")
    if replace is not None:
        _owned(replace, user)
    token = token.strip()
    if len(token) < 20:
        raise BankError("That doesn't look like a Starling access token.")
    accounts = StarlingProvider(token, settings.starling_api).list_accounts()
    return _attach(
        session,
        settings,
        user,
        provider="starling_pat",
        bank_name="Starling",
        bank_country="GB",
        secret=token,
        expires=None,
        accounts=accounts,
        replace=replace,
    )


# ---- mapping bank accounts to ours -------------------------------------------------------


def claimed_elsewhere(session: Session, link: BankAccountLink) -> BankAccountLink | None:
    """The same bank account already feeding one of ours through another connection."""
    if not link.identification_hash:
        return None
    return session.scalars(
        select(BankAccountLink).where(
            BankAccountLink.identification_hash == link.identification_hash,
            BankAccountLink.connection_id != link.connection_id,
            BankAccountLink.account_id.is_not(None),
        )
    ).first()


def _last_csv_day(session: Session, account: Account) -> date | None:
    return session.scalar(
        select(func.max(BankTransaction.booked_date)).where(
            BankTransaction.account_id == account.id,
            BankTransaction.connection_id.is_(None),
        )
    )


def set_mapping(
    session: Session, connection: BankConnection, user: User, mapping: dict[str, str | None]
) -> None:
    """Which of our accounts each bank account feeds (None: don't bring it in)."""
    _owned(connection, user)
    accounts = {a.key: a for a in session.scalars(select(Account))}
    for link in connection.accounts:
        if link.external_uid not in mapping:
            continue
        key = mapping[link.external_uid]
        if key is None:
            link.account = None
            continue
        account = accounts.get(key)
        if account is None:
            raise BankError(f"There's no account {key!r}.")
        other = claimed_elsewhere(session, link)
        if other is not None:
            raise BankError(
                f"{link.name} is already linked by {other.connection.owner.display_name}. "
                "A joint account is linked once."
            )
        feeding = session.scalars(
            select(BankAccountLink).where(
                BankAccountLink.account_id == account.id, BankAccountLink.id != link.id
            )
        ).first()
        if feeding is not None:
            raise BankError(f"{account.name} already comes from {feeding.connection.bank_name}.")
        if link.account_id != account.id:
            last = _last_csv_day(session, account)
            link.account = account  # the relationship, so a loaded link sees it at once
            link.synced_from = last + timedelta(days=1) if last else None
            link.synced_through = None
    session.flush()
    audit.record(
        session,
        user,
        "bank_connection",
        connection.id,
        "map_accounts",
        None,
        {link.name: link.account.key if link.account else None for link in connection.accounts},
    )


# ---- syncing ------------------------------------------------------------------------------


def uk_today() -> date:
    return datetime.now(UK).date()


def sync_connection(
    session: Session,
    settings: Settings,
    connection: BankConnection,
    trigger: str,
    *,
    psu_headers: dict[str, str] | None = None,
    today: date | None = None,
    provider: BankProvider | None = None,
    full: bool = False,
) -> SyncRun:
    """Fetch and store each mapped account's new rows. Normally from a week before the last
    sync; `full` goes back to the day the bank took over (or as far as it shares), to pick
    up what an earlier sync couldn't see. Storing is idempotent, so that's safe."""
    today = today or uk_today()
    run = SyncRun(connection_id=connection.id, trigger=trigger)
    session.add(run)
    session.flush()
    created: list[BankTransaction] = []
    try:
        bank = provider or provider_for(settings, connection, psu_headers)
        for link in connection.accounts:
            if link.account is None:
                continue
            since = link.synced_from
            if link.synced_through is not None and not full:
                resume = link.synced_through - OVERLAP
                since = max(since, resume) if since else resume
            handle = ProviderAccount(
                uid=link.external_uid,
                name=link.name,
                identification_hash=link.identification_hash,
            )
            rows = bank.fetch_transactions(handle, since, today)
            result = ingest.ingest_bank(
                session, link.account, connection, rows, since, link.synced_from
            )
            created += result.created
            run.rows_new += len(result.created)
            run.rows_updated += result.updated
            run.rows_removed += result.removed
            balance = bank.fetch_balance(handle)
            if balance is not None:
                link.balance_pence, link.balance_at = balance.amount_pence, balance.as_of
            link.synced_through = today
        matching.run(session, created)
        connection.status = "active"
        connection.last_error = None
        connection.last_sync_at = utcnow()
        run.status = "ok"
    except ConsentExpired as exc:
        connection.status = "expired"
        connection.last_error = str(exc)
        run.status, run.error = "error", str(exc)
        _raise_consent(session, connection, expired=True)
    except (RateLimited, ProviderError, BankError, crypto.SecretsUnavailable) as exc:
        connection.last_error = str(exc)
        run.status, run.error = "error", str(exc)
    run.finished_at = utcnow()
    session.flush()
    return run


def sync_now(
    session: Session,
    settings: Settings,
    connection: BankConnection,
    user: User,
    psu_headers: dict[str, str] | None = None,
) -> SyncRun:
    """A person asked for a sync: allowed once every few minutes per connection."""
    if connection.status == "pending":
        raise BankError("Approve access in the Monzo app first, then press Continue.")
    last = session.scalars(
        select(SyncRun)
        .where(SyncRun.connection_id == connection.id, SyncRun.trigger == "manual")
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    gap = timedelta(minutes=settings.manual_sync_minutes)
    if last is not None and utcnow() - as_utc(last.started_at) < gap:
        ready = (as_utc(last.started_at) + gap).astimezone(UK).strftime("%H:%M")
        raise BankError(f"Synced a moment ago. Banks limit syncs, so try again after {ready}.")
    run = sync_connection(session, settings, connection, "manual", psu_headers=psu_headers)
    audit.record(
        session,
        user,
        "bank_connection",
        connection.id,
        "sync_now",
        None,
        {"status": run.status, "new": run.rows_new},
    )
    return run


def sync_all(
    session: Session, settings: Settings, trigger: str = "schedule", full: bool = False
) -> list[SyncRun]:
    runs = []
    for connection in session.scalars(
        select(BankConnection).where(BankConnection.status.in_(("active", "error")))
    ).all():
        runs.append(sync_connection(session, settings, connection, trigger, full=full))
        session.commit()
    return runs


# ---- consent --------------------------------------------------------------------------------


SNOOZE_STOP = timedelta(days=7)  # no snoozing once the end is this close
SNOOZE_MARGIN = timedelta(days=2)  # a snooze always wakes this long before the end


def consent_snooze_until(item: InboxItem, user: User, today: date) -> date:
    """How long the owner may put off a consent reminder: a week, but never too close."""
    if item.payload_json.get("owner_user_id") != user.id:
        raise NotOwner(f"Only {item.payload_json.get('owner')} can put off their own bank link.")
    ends = item.payload_json.get("expires_on")
    if item.payload_json.get("expired") or not ends:
        raise BankError("This link has already ended: re-link it to carry on syncing.")
    end = date.fromisoformat(ends)
    if end - today <= SNOOZE_STOP:
        raise BankError("It ends too soon to put off. Re-link it now; it takes a minute.")
    return min(today + timedelta(days=7), end - SNOOZE_MARGIN)


def _raise_consent(session: Session, connection: BankConnection, expired: bool) -> bool:
    """One item per connection per consent end date; False if it's already been raised."""
    expires = as_utc(connection.consent_expires_at) if connection.consent_expires_at else None
    key = f"consent:{connection.id}:{expires.date().isoformat() if expires else 'now'}"
    if session.scalars(select(InboxItem).where(InboxItem.dedupe_key == key)).first():
        return False
    session.add(
        InboxItem(
            type="consent_expiring",
            dedupe_key=key,
            payload_json={
                "connection_id": connection.id,
                "bank": connection.bank_name,
                "owner": connection.owner.display_name,
                "owner_user_id": connection.owner_user_id,
                "expires_on": expires.astimezone(UK).date().isoformat() if expires else None,
                "expired": expired,
                "accounts": [
                    link.account.name for link in connection.accounts if link.account is not None
                ],
            },
        )
    )
    session.flush()
    return True


def check_consents(session: Session, now: datetime | None = None) -> int:
    """Raise an inbox item 14 days before each consent ends (and mark lapsed ones)."""
    now = now or utcnow()
    raised = 0
    for connection in session.scalars(
        select(BankConnection).where(BankConnection.consent_expires_at.is_not(None))
    ).all():
        expires = as_utc(connection.consent_expires_at)  # type: ignore[arg-type]
        if expires - now > CONSENT_WARNING:
            continue
        lapsed = expires <= now
        if lapsed and connection.status == "active":
            connection.status = "expired"
        raised += _raise_consent(session, connection, expired=lapsed)
    session.flush()
    return raised


# ---- disconnecting ----------------------------------------------------------------------


def disconnect(
    session: Session,
    settings: Settings,
    connection: BankConnection,
    user: User,
    delete_transactions: bool,
) -> int:
    """End the link: the session is deleted at the bank, and here. Returns rows removed."""
    _owned(connection, user)
    if connection.provider == "enable_banking":
        try:
            eb_client(settings).delete_session(
                crypto.decrypt(settings, connection.secret_encrypted)
            )
        except (ProviderError, BankError, crypto.SecretsUnavailable):
            logger.info("Enable Banking session for %s was already gone", connection.id)
    elif connection.provider == "monzo":
        try:
            monzo_for(settings, connection).logout()
        except (ProviderError, BankError, crypto.SecretsUnavailable):
            logger.info("Monzo sign-in for %s was already gone", connection.id)
    rows = session.scalars(
        select(BankTransaction).where(BankTransaction.connection_id == connection.id)
    ).all()
    if delete_transactions:
        ingest.forget(session, list(rows))
        for row in rows:
            session.delete(row)
    else:
        for row in rows:
            row.connection_id = None  # kept as history, like a CSV row
    for run in session.scalars(select(SyncRun).where(SyncRun.connection_id == connection.id)):
        session.delete(run)
    for flow in session.scalars(
        select(BankLinkFlow).where(BankLinkFlow.relink_connection_id == connection.id)
    ):
        flow.relink_connection_id = None
    _resolve_consent_items(session, connection.id, user)
    audit.record(
        session,
        user,
        "bank_connection",
        connection.id,
        "disconnect",
        {"bank": connection.bank_name, "accounts": [a.name for a in connection.accounts]},
        {"transactions_deleted": len(rows) if delete_transactions else 0},
    )
    session.delete(connection)
    session.flush()
    return len(rows) if delete_transactions else 0


def _claimed_by(session: Session, link: BankAccountLink) -> str | None:
    other = claimed_elsewhere(session, link)
    return other.connection.owner.display_name if other else None


def connection_view(
    session: Session, connection: BankConnection, user: User, manual_minutes: int = 30
) -> dict[str, Any]:
    """What the Banks screen shows for one connection."""
    expires = as_utc(connection.consent_expires_at) if connection.consent_expires_at else None
    last_run = session.scalars(
        select(SyncRun)
        .where(SyncRun.connection_id == connection.id)
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    rows = session.execute(
        select(func.count(), func.min(BankTransaction.booked_date)).where(
            BankTransaction.connection_id == connection.id
        )
    ).one()
    last_manual = session.scalars(
        select(SyncRun)
        .where(SyncRun.connection_id == connection.id, SyncRun.trigger == "manual")
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    next_manual = (
        as_utc(last_manual.started_at) + timedelta(minutes=manual_minutes)
        if last_manual is not None
        else None
    )
    return {
        "id": connection.id,
        "provider": connection.provider,
        "transactions": rows[0],
        "first_transaction": rows[1].isoformat() if rows[1] else None,
        # "Sync now" waits until then (banks limit how often they can be asked).
        "next_sync_now_at": next_manual.isoformat()
        if next_manual is not None and next_manual > utcnow()
        else None,
        "bank": connection.bank_name,
        "owner": connection.owner.display_name,
        "owner_user_id": connection.owner_user_id,
        "can_manage": connection.owner_user_id == user.id,
        "status": connection.status,
        "consent_expires_at": expires.isoformat() if expires else None,
        "last_sync_at": as_utc(connection.last_sync_at).isoformat()
        if connection.last_sync_at
        else None,
        "last_error": connection.last_error,
        "last_run": {
            "status": last_run.status,
            "new": last_run.rows_new,
            "updated": last_run.rows_updated,
            "removed": last_run.rows_removed,
            "at": as_utc(last_run.started_at).isoformat(),
            "trigger": last_run.trigger,
        }
        if last_run
        else None,
        "accounts": [
            {
                "uid": link.external_uid,
                "name": link.name,
                "detail": link.detail,
                "account": link.account.key if link.account else None,
                "synced_from": link.synced_from.isoformat() if link.synced_from else None,
                "synced_through": link.synced_through.isoformat() if link.synced_through else None,
                "balance_pence": link.balance_pence,
                "balance_at": as_utc(link.balance_at).isoformat() if link.balance_at else None,
                "claimed_by": _claimed_by(session, link),
            }
            for link in connection.accounts
        ],
    }
