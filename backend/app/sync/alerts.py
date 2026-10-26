"""What the bank data says needs a look, and telling Home Assistant (PLAN.md §8.4, Phase 5).

After each sync (and each morning) this:

- detects recurring payments, proposing the ones the budget doesn't know (recurring.py)
- raises a missed payment when a bill that's been paid from the bank before hasn't
  appeared by its day + 5
- raises "still paying" when a bill with a "cancel" follow-up is still being taken
- raises the planning reminders: goals, renewals, the remortgage window, agreements
  ending and pay that doesn't match the take-home (plan_alerts.py)
- tells Home Assistant: new inbox items in one batched notification; each bank link that's
  ending in its own, addressed to its owner; a what-if whose planned start has arrived; and
  Joint Billing projected to dip below the cushion before the next payday. Each is sent once.

Nothing here changes the budget. It all lands in the inbox for a person to answer.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import notify
from app.config import Settings
from app.domain.models import (
    Account,
    BankAccountLink,
    BankTransaction,
    BudgetLine,
    BudgetVersion,
    FollowUp,
    InboxItem,
    Notification,
    Person,
    RecurringCandidate,
    User,
)
from app.domain.payday import PaydayError
from app.domain.projection import month_projection
from app.domain.versions import live_version
from app.engine.money import round_pence
from app.sync import banks, matching, plan_alerts, recurring
from app.sync.merchants import Haystack, tokens
from app.sync.recurring import DetectionSummary

MISSED_GRACE = timedelta(days=5)
STILL_PAYING_WINDOW = timedelta(days=45)
BATCH_LINES = 6


def pounds(pence: int) -> str:
    """£1,192.46 from pence, exactly (no floats)."""
    sign = "−" if pence < 0 else ""  # noqa: RUF001  (a true minus, as in the app)
    whole, part = divmod(abs(pence), 100)
    return f"{sign}£{whole:,}.{part:02d}"


def _month(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    return first, date(first.year + first.month // 12, first.month % 12 + 1, 1)


def _exists(session: Session, key: str) -> bool:
    return (
        session.scalars(select(InboxItem.id).where(InboxItem.dedupe_key == key)).first() is not None
    )


# ---- missed payments ---------------------------------------------------------------------


def _covered_through(session: Session, account_id: int) -> date | None:
    """The last day this account's bank data reaches (a sync or the latest import)."""
    synced = session.scalar(
        select(func.max(BankAccountLink.synced_through)).where(
            BankAccountLink.account_id == account_id
        )
    )
    latest = session.scalar(
        select(func.max(BankTransaction.booked_date)).where(
            BankTransaction.account_id == account_id
        )
    )
    known = [d for d in (synced, latest) if d is not None]
    return max(known) if known else None


def check_missed(session: Session, today: date) -> int:
    """A monthly bill paid from the bank before, not seen by its day + 5 this month.

    The day is the budget's, or for a bill with no set day, the day the bank history shows
    it's usually paid."""
    raised = 0
    first, after = _month(today)
    accounts = {a.key: a.id for a in session.scalars(select(Account))}
    usual = {
        c.line_id: c.day_of_month
        for c in session.scalars(
            select(RecurringCandidate).where(RecurringCandidate.cadence == "monthly")
        )
        if c.line_id is not None and c.day_of_month is not None
    }
    for line in matching.bill_lines(session, first):
        day_of_month = line.day_of_month or usual.get(line.line_id)
        if line.frequency != "monthly" or day_of_month is None:
            continue
        account_id = accounts.get(line.account)
        if account_id is None:
            continue
        last_day = (after - timedelta(days=1)).day
        due = first.replace(day=min(day_of_month, last_day))
        if today < due + MISSED_GRACE:
            continue
        covered = _covered_through(session, account_id)
        if covered is None or covered < due + MISSED_GRACE:
            continue  # the bank data doesn't reach that far yet: too soon to say
        paid = session.scalars(
            select(BankTransaction)
            .where(
                BankTransaction.matched_line_id == line.line_id,
                BankTransaction.booked_date < after,
            )
            .order_by(BankTransaction.booked_date.desc())
            .limit(1)
        ).first()
        if paid is None or paid.booked_date < first - timedelta(days=95):
            continue  # never seen paid from the bank (lately): it may go another way
        if paid.booked_date >= first:
            continue  # paid this month
        key = f"missed:{line.line_id}:{first:%Y-%m}"
        if _exists(session, key):
            continue
        session.add(
            InboxItem(
                type="missed_payment",
                dedupe_key=key,
                line_id=line.line_id,
                payload_json={
                    "label": line.label,
                    "account": line.account,
                    "month": first.isoformat(),
                    "due_on": due.isoformat(),
                    "amount_pence": line.amount_pence,
                    "last_paid_on": paid.booked_date.isoformat(),
                    "last_paid_pence": -paid.amount_pence,
                },
            )
        )
        session.flush()
        raised += 1
    # A late payment turns up: the missed item answers itself.
    for item in session.scalars(
        select(InboxItem).where(
            InboxItem.type == "missed_payment", InboxItem.status.in_(("open", "snoozed"))
        )
    ).all():
        month_first = date.fromisoformat(item.payload_json["month"])
        month_after = _month(month_first)[1]
        late = session.scalars(
            select(BankTransaction.id).where(
                BankTransaction.matched_line_id == item.line_id,
                BankTransaction.booked_date >= month_first,
                BankTransaction.booked_date < month_after,
            )
        ).first()
        if late is not None:
            item.status = "rejected"
            item.payload_json = item.payload_json | {"paid_late": True}
    session.flush()
    return raised


# ---- still paying for something marked to cancel -----------------------------------------


def _payee_matches(line: BudgetLine, txn: BankTransaction) -> bool:
    hay = Haystack(txn.counterparty, txn.description, txn.merchant_normalised)
    if line.provider and hay.has_phrase(line.provider):
        return True
    words = tokens(line.name)
    return hay.has_phrase(line.name) or (bool(words) and all(hay.has(w) for w in words))


def check_still_paying(session: Session, today: date) -> tuple[int, set[tuple[int, str]]]:
    """A "cancel" follow-up whose bill is still being taken. Returns the count raised and
    the (account, payee) pairs it covers, which recurring detection then leaves alone."""
    raised = 0
    covered: set[tuple[int, str]] = set()
    since = today - STILL_PAYING_WINDOW
    for follow_up in session.scalars(
        select(FollowUp).where(FollowUp.reason == "cancel", FollowUp.status == "open")
    ).all():
        line = session.get(BudgetLine, follow_up.line_id) if follow_up.line_id else None
        if line is None:
            continue
        taken = [
            t
            for t in session.scalars(
                select(BankTransaction).where(
                    BankTransaction.amount_pence < 0, BankTransaction.booked_date >= since
                )
            )
            if _payee_matches(line, t)
        ]
        if not taken:
            continue
        covered |= {(t.account_id, t.merchant_normalised) for t in taken}
        latest = max(taken, key=lambda t: t.booked_date)
        key = f"still_paying:{follow_up.id}:{latest.booked_date:%Y-%m}"
        if _exists(session, key):
            continue
        account = session.get(Account, latest.account_id)
        session.add(
            InboxItem(
                type="still_paying",
                dedupe_key=key,
                line_id=line.id,
                transaction_id=latest.id,
                payload_json={
                    "follow_up_id": follow_up.id,
                    "label": f"{line.name} ({line.provider})" if line.provider else line.name,
                    "note": follow_up.note,
                    "account": account.key if account else None,
                    "paid_on": latest.booked_date.isoformat(),
                    "amount_pence": -latest.amount_pence,
                    "payments": len(taken),
                    "taken_pence": -sum(t.amount_pence for t in taken),
                },
            )
        )
        session.flush()
        raised += 1
    return raised, covered


# ---- Home Assistant -----------------------------------------------------------------------


def _sent(session: Session, key: str) -> bool:
    return (
        session.scalars(select(Notification.id).where(Notification.key == key)).first() is not None
    )


def _record(session: Session, key: str, kind: str, error: str | None) -> None:
    session.add(Notification(key=key, kind=kind, ok=error is None, error=error))
    session.flush()


def _line(item: InboxItem) -> str | None:
    p = item.payload_json
    if item.type == "price_change":
        return f"{p.get('label')} went from {pounds(p['old_pence'])} to {pounds(p['new_pence'])}"
    if item.type == "new_recurring":
        amount = pounds(p["amount_pence"])
        return f"new regular payment to {p.get('merchant')}, {amount} {p.get('cadence')}"
    if item.type == "missed_payment":
        return f"{p.get('label')} hasn't been paid this month (due {p.get('due_on')})"
    if item.type == "still_paying":
        amount = pounds(p["amount_pence"])
        return f"{p.get('label')} is still being paid ({amount}), though marked to cancel"
    if item.type == "goal_adjust":
        return (
            f"{p.get('name')} needs {pounds(p['needed_pence'])} a month, "
            f"not {pounds(p['current_pence'])}"
        )
    if item.type == "renewal_due":
        return f"{p.get('name')} renews on {p.get('renews_on')}: time to shop around"
    if item.type == "remortgage_window":
        fix_end = str(p.get("fix_end", ""))[:7]
        return f"the {p.get('lender')} fix ends {fix_end}: time to look at remortgaging"
    if item.type == "agreement_ending":
        return f"{p.get('label')} ends {p.get('end_month', '')[:7]}"
    if item.type == "salary_mismatch":
        return (
            f"{p.get('name')}'s pay landed at {pounds(p['landed_pence'])}, "
            f"not {pounds(p['expected_pence'])}"
        )
    return None


def _person(session: Session, user_id: int | None) -> str | None:
    user = session.get(User, user_id) if user_id else None
    person = session.get(Person, user.person_id) if user and user.person_id else None
    return person.key if person else None


@dataclass
class AlertSummary:
    still_paying: int = 0
    missed: int = 0
    recurring: DetectionSummary = field(default_factory=DetectionSummary)
    consents: int = 0
    plan: plan_alerts.PlanAlerts = field(default_factory=plan_alerts.PlanAlerts)
    notified: list[str] = field(default_factory=list)


def notify_ha(session: Session, settings: Settings, today: date, **http: Any) -> list[str]:
    """Send what Home Assistant hasn't heard yet. Returns the notification keys sent."""
    if not settings.ha_webhook_url:
        return []
    sent: list[str] = []

    # 1. New inbox items, batched into one notification.
    fresh = [
        i
        for i in session.scalars(
            select(InboxItem).where(InboxItem.status == "open").order_by(InboxItem.id)
        )
        if i.type != "consent_expiring" and not _sent(session, f"inbox:{i.id}")
    ]
    lines = [text for text in (_line(i) for i in fresh) if text]
    if fresh:
        shown = lines[:BATCH_LINES]
        more = len(fresh) - len(shown)
        message = "; ".join(shown) + (f"; and {more} more" if more > 0 else "")
        error = notify.send(
            settings,
            "inbox",
            f"Bawbee: {len(fresh)} new in the inbox",
            message or "Open the inbox",
            data={"count": len(fresh), "items": [i.id for i in fresh]},
            **http,
        )
        for item in fresh:
            _record(session, f"inbox:{item.id}", "inbox", error)
        sent.append("inbox")

    # 2. Each ending bank link, to its owner.
    for item in session.scalars(
        select(InboxItem).where(InboxItem.type == "consent_expiring", InboxItem.status == "open")
    ):
        key = f"inbox:{item.id}"
        if _sent(session, key):
            continue
        p = item.payload_json
        ended = p.get("expired")
        error = notify.send(
            settings,
            "consent",
            f"Bawbee: your {p.get('bank')} link {'has ended' if ended else 'is ending'}",
            "Syncing has stopped. Re-link it from Banks."
            if ended
            else f"It ends on {p.get('expires_on')}. Re-link it from Banks; it takes a minute.",
            person=_person(session, p.get("owner_user_id")),
            data={
                "bank": p.get("bank"),
                "expires_on": p.get("expires_on"),
                "owner": p.get("owner"),
            },
            **http,
        )
        _record(session, key, "consent", error)
        sent.append(key)

    # 3. A what-if whose planned start has arrived (never promoted automatically, §5.4).
    for version in session.scalars(
        select(BudgetVersion).where(
            BudgetVersion.type == "what_if",
            BudgetVersion.status == "draft",
            BudgetVersion.planned_start.is_not(None),
        )
    ):
        start = version.planned_start
        if start is None or start > today:
            continue
        key = f"promotion_due:{version.id}:{start.isoformat()}"
        if _sent(session, key):
            continue
        error = notify.send(
            settings,
            "promotion_due",
            f"Bawbee: time for {version.name}?",
            f"{version.name} was planned to start on {start.isoformat()}. "
            "Promote it to live from Versions when that stage arrives.",
            data={"version_id": version.id},
            **http,
        )
        _record(session, key, "promotion_due", error)
        sent.append(key)

    # 4. Joint Billing projected below the cushion before the next payday (§4.7).
    try:
        projected = month_projection(session, live_version(session), today.year, today.month)
    except (PaydayError, LookupError):
        projected = None
    if projected is not None:
        low = round_pence(projected.month.minimum)
        if low < settings.ha_low_balance_pence:
            day = projected.month.minimum_day
            key = f"low_balance:{projected.bill_month:%Y-%m}"
            if not _sent(session, key):
                error = notify.send(
                    settings,
                    "low_balance",
                    "Bawbee: Joint Billing will run low",
                    f"It's projected to reach {pounds(low)} on {day.isoformat()}, "
                    f"under the {pounds(settings.ha_low_balance_pence)} cushion, "
                    "before the next payday.",
                    data={"minimum_pence": low, "minimum_day": day.isoformat()},
                    **http,
                )
                _record(session, key, "low_balance", error)
                sent.append(key)
    session.flush()
    return sent


def look(session: Session, today: date) -> AlertSummary:
    """Look through the bank data for what needs a look; nothing is sent anywhere."""
    summary = AlertSummary()
    summary.still_paying, covered = check_still_paying(session, today)
    summary.recurring = recurring.detect(session, today, skip=covered)
    summary.missed = check_missed(session, today)
    return summary


def run(session: Session, settings: Settings, today: date, **http: Any) -> AlertSummary:
    """The whole pass after a sync: look, check consents, then tell Home Assistant."""
    summary = look(session, today)
    summary.consents = banks.check_consents(session)
    summary.plan = plan_alerts.run(session, today)
    summary.notified = notify_ha(session, settings, today, **http)
    return summary
