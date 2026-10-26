"""The approval inbox (PLAN.md §8.4 step 4, decision 19).

Approving a price change writes a new dated line version on live from the day the new
amount was seen. What-ifs that override that line then get a choice each: keep their
amount, take the new live one, or set another. Those choices wait on the item (its
`pending` list) until each is made, so a refresh or a second approval can't lose them.

Every answer can be undone while nothing has been built on it: an approval until a what-if
has been decided or edited against the new price, "not a change" and snooze at any time.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import must_get, utcnow
from app.domain import audit, editing
from app.domain.models import (
    Account,
    BankTransaction,
    BudgetLine,
    BudgetLineVersion,
    BudgetVersion,
    CategoryRule,
    FollowUp,
    InboxItem,
    Override,
    PaydayTransfer,
    RecurringCandidate,
    Renewal,
    User,
)
from app.domain.versions import VersionError, current_line_version
from app.sync import review

Decision = Literal["keep", "take", "set"]


class InboxError(ValueError):
    pass


@dataclass(frozen=True)
class OverridingWhatIf:
    version_id: int
    name: str
    override_pence: int | None


def open_items(session: Session, today: date) -> list[InboxItem]:
    items = session.scalars(
        select(InboxItem).where(InboxItem.status.in_(("open", "snoozed"))).order_by(InboxItem.id)
    ).all()
    return [i for i in items if i.status == "open" or (i.snoozed_until or today) <= today]


def awaiting_what_ifs(session: Session) -> list[InboxItem]:
    """Approved price changes with what-if choices still to make."""
    items = session.scalars(
        select(InboxItem).where(InboxItem.status == "approved").order_by(InboxItem.id)
    ).all()
    return [i for i in items if i.payload_json.get("pending")]


def pending_what_ifs(session: Session, item: InboxItem) -> list[OverridingWhatIf]:
    waiting = set(item.payload_json.get("pending", []))
    if not waiting or item.line_id is None:
        return []
    return [o for o in overriding_what_ifs(session, item.line_id) if o.version_id in waiting]


def overriding_what_ifs(session: Session, line_id: int) -> list[OverridingWhatIf]:
    """Draft what-ifs that set their own amount for this line."""
    rows = session.execute(
        select(Override, BudgetVersion)
        .join(BudgetVersion, BudgetVersion.id == Override.version_id)
        .where(
            Override.line_id == line_id,
            Override.action == "modify",
            BudgetVersion.type == "what_if",
            BudgetVersion.status == "draft",
        )
        .order_by(BudgetVersion.id)
    ).all()
    return [
        OverridingWhatIf(v.id, v.name, o.fields_json.get("amount_pence"))
        for o, v in rows
        if "amount_pence" in o.fields_json
    ]


def _resolve(item: InboxItem, status: str, actor: User | None) -> None:
    item.status = status
    item.resolved_by = actor.id if actor else None
    item.resolved_at = utcnow()


def approve_price_change(
    session: Session, item: InboxItem, actor: User | None, effective_from: date | None = None
) -> list[OverridingWhatIf]:
    if item.type != "price_change" or item.status not in ("open", "snoozed"):
        raise InboxError("That item has already been dealt with")
    if item.line_id is None:
        raise InboxError("That price change has no budget line")
    payload = item.payload_json
    line = must_get(session, BudgetLine, item.line_id)
    when = effective_from or date.fromisoformat(payload["seen_on"])
    changes: dict[str, object] = {"amount_pence": payload["new_pence"]}
    if "components" in payload:
        changes["components"] = payload["components"]
    try:
        made = editing.update_live_line(session, line, changes, when, actor)
    except VersionError as exc:
        raise InboxError(str(exc)) from exc
    offered = overriding_what_ifs(session, line.id)
    _resolve(item, "approved", actor)
    item.payload_json = payload | {
        "effective_from": when.isoformat(),
        "line_version_id": made.id if made else None,
        "offered": [o.version_id for o in offered],
        "pending": [o.version_id for o in offered],
    }
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "approve", None, item.payload_json)
    return offered


def decide_what_if(
    session: Session,
    item: InboxItem,
    version: BudgetVersion,
    decision: Decision,
    actor: User | None,
    amount_pence: int | None = None,
) -> None:
    """After approval: what this what-if does about the new live price (PLAN.md §5.3)."""
    if item.line_id is None or item.status != "approved":
        raise InboxError("Approve the price change first")
    when = date.fromisoformat(item.payload_json.get("effective_from", item.payload_json["seen_on"]))
    override = next((o for o in version.overrides if o.line_id == item.line_id), None)
    if override is None:
        raise InboxError(f"{version.name!r} doesn't change this line")
    if decision == "set" and (amount_pence is None or amount_pence < 0):
        raise InboxError("Give the amount this what-if should use")
    if decision == "keep":
        # Acknowledged: its own amount stands, and it's no longer flagged as a conflict.
        current = current_line_version(session, item.line_id, when)
        override.based_on_version_id = current.id if current else override.based_on_version_id
        session.flush()
        audit.record(session, actor, "override", override.id, "keep_after_price_change")
    elif decision == "take":
        fields = {
            k: v for k, v in override.fields_json.items() if k not in ("amount_pence", "components")
        }
        if fields:
            before = dict(override.fields_json)
            override.fields_json = fields
            current = current_line_version(session, item.line_id, when)
            override.based_on_version_id = current.id if current else None
            session.flush()
            audit.record(session, actor, "override", override.id, "take_live_price", before, fields)
        else:
            editing.revert_override(session, override, actor)
    else:
        editing.override_line(
            session, version, item.line_id, {"amount_pence": amount_pence}, when, actor
        )
    pending = [v for v in item.payload_json.get("pending", []) if v != version.id]
    item.payload_json = item.payload_json | {"pending": pending}
    session.flush()


def reject(session: Session, item: InboxItem, actor: User | None) -> None:
    """ "Not a change": a one-off, or the bank text matched the wrong bill."""
    if item.status not in ("open", "snoozed"):
        raise InboxError("That item has already been dealt with")
    _resolve(item, "rejected", actor)
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "reject")


def snooze(
    session: Session, item: InboxItem, actor: User | None, until: date | None, today: date
) -> None:
    if item.status not in ("open", "snoozed"):
        raise InboxError("That item has already been dealt with")
    item.status = "snoozed"
    item.snoozed_until = until or today + timedelta(days=7)
    session.flush()
    audit.record(
        session, actor, "inbox_item", item.id, "snooze", None, item.snoozed_until.isoformat()
    )


def undo(session: Session, item: InboxItem, actor: User | None) -> None:
    """Put an answered item back in the inbox, and whatever the answer changed back out."""
    if item.status in ("rejected", "snoozed"):
        _undo_rejection(session, item)
        item.status = "open"
        item.snoozed_until = None
    elif item.status == "approved" and item.type == "new_recurring":
        _undo_claim(session, item)
        item.status = "open"
    elif item.status == "approved" and item.type == "missed_payment":
        _undo_stop(session, item)
        item.status = "open"
    elif item.status == "approved" and item.type == "still_paying":
        item.status = "open"
    elif item.status == "approved" and item.type == "salary_mismatch":
        _undo_salary(session, item, actor)
        item.status = "open"
    elif item.status == "approved" and item.type == "renewal_due":
        _undo_renewal(session, item)
        item.status = "open"
    elif item.status == "approved" and item.type in ("remortgage_window", "agreement_ending"):
        item.status = "open"
    elif item.status == "approved":
        payload = item.payload_json
        if set(payload.get("pending", [])) != set(payload.get("offered", [])):
            raise InboxError("A what-if has already been decided on the new price")
        made = session.get(BudgetLineVersion, payload.get("line_version_id") or 0)
        if made is None:
            raise InboxError("That approval can't be undone any more")
        later = session.scalars(
            select(BudgetLineVersion).where(
                BudgetLineVersion.line_id == made.line_id,
                BudgetLineVersion.effective_from > made.effective_from,
            )
        ).first()
        built_on = session.scalars(
            select(Override).where(Override.based_on_version_id == made.id)
        ).first()
        if later is not None or built_on is not None:
            raise InboxError("The live line has changed again since; edit it in the budget")
        previous = session.scalars(
            select(BudgetLineVersion).where(
                BudgetLineVersion.line_id == made.line_id,
                BudgetLineVersion.effective_to == made.effective_from,
                BudgetLineVersion.id != made.id,
            )
        ).first()
        if previous is not None:
            previous.effective_to = made.effective_to
        session.delete(made)
        item.status = "open"
        item.payload_json = {
            k: v
            for k, v in payload.items()
            if k not in ("effective_from", "line_version_id", "offered", "pending")
        }
    else:
        raise InboxError("That item is still open")
    item.resolved_by = None
    item.resolved_at = None
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "undo")


# ---- new recurring payments, missed payments, still paying (Phase 5) ---------------------

CADENCE_FREQUENCY = {
    "weekly": "weekly",
    "monthly": "monthly",
    "quarterly": "quarterly",
    "annual": "annual",
}


def _open(item: InboxItem, kind: str) -> None:
    if item.type != kind:
        raise InboxError("That isn't the right kind of inbox item for this")
    if item.status not in ("open", "snoozed"):
        raise InboxError("That item has already been dealt with")


def accept_recurring(
    session: Session, item: InboxItem, actor: User | None, values: dict[str, object]
) -> BudgetLine:
    """Add a newly spotted regular payment to the live budget, and match its payments."""
    _open(item, "new_recurring")
    payload = item.payload_json
    candidate = must_get(session, RecurringCandidate, payload["candidate_id"])
    first_seen = date.fromisoformat(payload["first_seen"])
    line_values = {
        "kind": "bill",
        "account": payload["account"],
        "amount_pence": payload["amount_pence"],
        "frequency": CADENCE_FREQUENCY[payload["cadence"]],
        "day_of_month": payload.get("day_of_month"),
        "name": payload["merchant"],
    } | {k: v for k, v in values.items() if v is not None}
    try:
        line = editing.add_live_line(session, line_values, first_seen.replace(day=1), actor)
    except (VersionError, KeyError) as exc:
        raise InboxError(str(exc)) from exc
    # From now on this payee is that line: the payments seen so far, and the next ones.
    done = _claim(session, candidate, line.id, payload["account"], actor)
    _resolve(item, "approved", actor)
    item.payload_json = payload | {"line_id": line.id, "created_line": True, **done}
    session.flush()
    audit.record(
        session,
        actor,
        "inbox_item",
        item.id,
        "accept_recurring",
        None,
        {"line_id": line.id, **line_values},
    )
    return line


def _claim(
    session: Session,
    candidate: RecurringCandidate,
    line_id: int,
    account_key: str,
    actor: User | None,
) -> dict[str, Any]:
    """This payee on this account is that line: past payments and the next ones. Returns
    what it did, so it can be undone."""
    rule = CategoryRule(
        priority=100,
        match_json={"merchant": candidate.merchant_normalised, "account": account_key},
        set_line_id=line_id,
        created_by=actor.id if actor else None,
    )
    session.add(rule)
    claimed: list[int] = []
    for txn in session.scalars(
        select(BankTransaction).where(
            BankTransaction.account_id == candidate.account_id,
            BankTransaction.merchant_normalised == candidate.merchant_normalised,
            BankTransaction.matched_line_id.is_(None),
        )
    ):
        txn.matched_line_id = line_id
        txn.match_method = "rule"
        claimed.append(txn.id)
    candidate.status = "accepted"
    candidate.line_id = line_id
    session.flush()
    return {"rule_id": rule.id, "claimed": claimed}


def _line(session: Session, line_id: int) -> BudgetLine:
    line = session.get(BudgetLine, line_id)
    if line is None:
        raise InboxError("There's no such budget line")
    return line


def link_recurring(
    session: Session, item: InboxItem, actor: User | None, line_id: int
) -> BudgetLine:
    """It's a bill the budget already has under another name: match its payments to it."""
    _open(item, "new_recurring")
    candidate = must_get(session, RecurringCandidate, item.payload_json["candidate_id"])
    line = _line(session, line_id)
    done = _claim(session, candidate, line.id, item.payload_json["account"], actor)
    _resolve(item, "approved", actor)
    item.payload_json = item.payload_json | {"line_id": line.id, "linked": True, **done}
    session.flush()
    audit.record(
        session, actor, "inbox_item", item.id, "link_recurring", None, {"line_id": line.id}
    )
    return line


def ignore_recurring(session: Session, item: InboxItem, actor: User | None) -> None:
    """Not a bill to budget for: never propose this payee on this account again."""
    _open(item, "new_recurring")
    candidate = must_get(session, RecurringCandidate, item.payload_json["candidate_id"])
    candidate.status = "ignored"
    _resolve(item, "rejected", actor)
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "ignore_recurring")


def link_payee(
    session: Session, candidate: RecurringCandidate, line_id: int, actor: User | None
) -> BudgetLine:
    """From Regular payments: this payee is that line, whether it's new, set aside, or
    matched to the wrong line. Its payments move over, and a rule keeps the next ones there."""
    line = _line(session, line_id)
    item = session.scalars(
        select(InboxItem).where(InboxItem.dedupe_key == f"new_recurring:{candidate.id}")
    ).first()
    if item is not None and item.status in ("open", "snoozed"):
        return link_recurring(session, item, actor, line_id)
    account_key = candidate.account.key
    merchant = candidate.merchant_normalised
    before = {"line_id": candidate.line_id, "status": candidate.status}
    for old in session.scalars(select(CategoryRule)).all():
        if (old.match_json.get("merchant"), old.match_json.get("account")) == (
            merchant,
            account_key,
        ):
            audit.record(session, actor, "category_rule", old.id, "delete", old.match_json, None)
            session.delete(old)
    session.flush()
    session.add(
        CategoryRule(
            priority=100,
            match_json={"merchant": merchant, "account": account_key},
            set_line_id=line.id,
            created_by=actor.id if actor else None,
        )
    )
    moved = 0
    for txn in session.scalars(
        select(BankTransaction).where(
            BankTransaction.account_id == candidate.account_id,
            BankTransaction.merchant_normalised == merchant,
            BankTransaction.amount_pence < 0,
            BankTransaction.transfer_pair_id.is_(None),
        )
    ):
        if txn.match_method == "transfer" or txn.matched_line_id == line.id:
            continue
        txn.matched_line_id = line.id
        txn.match_method = "rule"
        moved += 1
    candidate.status = "accepted"
    candidate.line_id = line.id
    session.flush()
    audit.record(
        session,
        actor,
        "recurring_candidate",
        candidate.id,
        "link",
        before,
        {"line_id": line.id, "moved": moved},
    )
    return line


# ---- a missed payment that was paid: the matcher didn't see it ---------------------------

PAID_EARLY = timedelta(days=10)  # a bill can go out the month before (a weekend, a holiday)
PAID_LATE = timedelta(days=10)
PAYMENT_OPTIONS = 25


def payment_options(session: Session, item: InboxItem) -> list[BankTransaction]:
    """Money out on the bill's account around its month that could be the payment: the
    closest to the usual amount first."""
    if item.type != "missed_payment":
        raise InboxError("That isn't a missed payment")
    payload = item.payload_json
    account = session.scalars(select(Account).where(Account.key == payload["account"])).first()
    if account is None:
        return []
    first = date.fromisoformat(payload["month"])
    after = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    due = date.fromisoformat(payload.get("due_on") or payload["month"])
    usual = payload.get("last_paid_pence") or payload.get("amount_pence") or 0
    txns = [
        t
        for t in session.scalars(
            select(BankTransaction).where(
                BankTransaction.account_id == account.id,
                BankTransaction.amount_pence < 0,
                BankTransaction.booked_date >= first - PAID_EARLY,
                BankTransaction.booked_date < after + PAID_LATE,
                BankTransaction.transfer_pair_id.is_(None),
            )
        )
        if t.match_method != "transfer" and t.bank_category != "pot_transfer"
    ]
    txns.sort(key=lambda t: (abs(-t.amount_pence - usual), abs((t.booked_date - due).days)))
    return txns[:PAYMENT_OPTIONS]


def missed_paid(
    session: Session, item: InboxItem, actor: User | None, txn: BankTransaction, always: bool
) -> None:
    """It was paid: this is the payment. It's matched to the bill (with a rule for the
    payee's next ones, if asked), and the item is answered."""
    _open(item, "missed_payment")
    if item.line_id is None:
        raise InboxError("That missed payment has no budget line")
    if txn.amount_pence >= 0:
        raise InboxError("That's money in, not a payment")
    before = [txn.matched_line_id, txn.match_method]
    payee = [
        t
        for t in session.scalars(
            select(BankTransaction).where(
                BankTransaction.account_id == txn.account_id,
                BankTransaction.merchant_normalised == txn.merchant_normalised,
                BankTransaction.id != txn.id,
            )
        )
    ]
    others = {t.id: [t.matched_line_id, t.match_method] for t in payee}
    try:
        result = review.match_manually(
            session,
            txn,
            actor,
            line_id=item.line_id,
            category=txn.category.name if txn.category else None,
            always=always,
        )
    except review.ReviewError as exc:
        raise InboxError(str(exc)) from exc
    # What "always" moved, so undo can put it back.
    moved = {
        str(t.id): others[t.id]
        for t in payee
        if [t.matched_line_id, t.match_method] != others[t.id]
    }
    _resolve(item, "rejected", actor)
    item.transaction_id = txn.id
    item.payload_json = item.payload_json | {
        "paid_with": txn.id,
        "paid_before": before,
        "rule_id": result.rule_id,
        "also_before": moved,
    }
    session.flush()
    audit.record(
        session,
        actor,
        "inbox_item",
        item.id,
        "missed_paid",
        None,
        {"transaction_id": txn.id, "rule_id": result.rule_id},
    )


def _undo_paid(session: Session, item: InboxItem) -> None:
    payload = item.payload_json
    txn = session.get(BankTransaction, payload["paid_with"])
    if txn is not None and txn.matched_line_id == item.line_id:
        txn.matched_line_id, txn.match_method = payload.get("paid_before") or [None, None]
    rule = session.get(CategoryRule, payload.get("rule_id") or 0)
    if rule is not None:
        session.delete(rule)
    for txn_id, (line_id, method) in (payload.get("also_before") or {}).items():
        other = session.get(BankTransaction, int(txn_id))
        if other is not None:
            other.matched_line_id, other.match_method = line_id, method
    item.transaction_id = None
    item.payload_json = {
        k: v
        for k, v in payload.items()
        if k not in ("paid_with", "paid_before", "rule_id", "also_before")
    }


def missed_stopped(session: Session, item: InboxItem, actor: User | None) -> None:
    """The bill has stopped: take it out of the live budget from that month."""
    _open(item, "missed_payment")
    line = must_get(session, BudgetLine, item.line_id)
    month = date.fromisoformat(item.payload_json["month"])
    current = current_line_version(session, line.id, month)
    if current is None:
        raise InboxError(f"{line.name!r} isn't in the live budget in that month")
    before = current.effective_to
    try:
        editing.end_live_line(session, line, month, actor)
    except VersionError as exc:
        raise InboxError(str(exc)) from exc
    _resolve(item, "approved", actor)
    item.payload_json = item.payload_json | {
        "ended_version_id": current.id,
        "previous_effective_to": before.isoformat() if before else None,
    }
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "missed_stopped")


def still_paying_keep(session: Session, item: InboxItem, actor: User | None) -> None:
    """Decided to keep it after all: the "cancel" follow-up is put away."""
    _open(item, "still_paying")
    follow_up = session.get(FollowUp, item.payload_json.get("follow_up_id"))
    if follow_up is not None:
        item.payload_json = item.payload_json | {"follow_up_was": follow_up.status}
        follow_up.status = "dismissed"
    _resolve(item, "rejected", actor)
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "keep_paying")


def still_paying_cancelled(session: Session, item: InboxItem, actor: User | None) -> None:
    """Cancelled now. The follow-up stays open, so a payment next month is caught again."""
    _open(item, "still_paying")
    _resolve(item, "approved", actor)
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "cancelled_now")


def _undo_rejection(session: Session, item: InboxItem) -> None:
    payload = item.payload_json
    if item.type == "new_recurring" and item.status == "rejected":
        candidate = session.get(RecurringCandidate, payload.get("candidate_id"))
        if candidate is not None and candidate.status == "ignored":
            candidate.status = "new"
    elif item.type == "missed_payment" and "paid_with" in payload:
        _undo_paid(session, item)
    elif item.type == "still_paying" and "follow_up_was" in payload:
        follow_up = session.get(FollowUp, payload.get("follow_up_id"))
        if follow_up is not None:
            follow_up.status = payload["follow_up_was"]
        item.payload_json = {k: v for k, v in payload.items() if k != "follow_up_was"}


def _undo_claim(session: Session, item: InboxItem) -> None:
    """Take back "add it to the budget" or "it's that line": the rule, the matches, and a
    line that was made for it, as long as nothing else has come to depend on that line."""
    payload = item.payload_json
    line_id = payload.get("line_id")
    claimed = set(payload.get("claimed", []))
    if payload.get("created_line") and line_id is not None:
        others = session.scalars(
            select(BankTransaction.id).where(BankTransaction.matched_line_id == line_id)
        ).all()
        used = (
            any(t not in claimed for t in others)
            or session.scalars(select(Override.id).where(Override.line_id == line_id)).first()
            or session.scalars(
                select(PaydayTransfer.id).where(PaydayTransfer.line_id == line_id)
            ).first()
            or session.scalars(
                select(InboxItem.id).where(InboxItem.line_id == line_id, InboxItem.id != item.id)
            ).first()
            or len(
                session.scalars(
                    select(BudgetLineVersion.id).where(BudgetLineVersion.line_id == line_id)
                ).all()
            )
            > 1
        )
        if used:
            raise InboxError("That line has been used since; stop it from the Budget instead")
    rule = session.get(CategoryRule, payload.get("rule_id"))
    if rule is not None:
        session.delete(rule)
    for txn in session.scalars(select(BankTransaction).where(BankTransaction.id.in_(claimed))):
        if txn.matched_line_id == line_id and txn.match_method == "rule":
            txn.matched_line_id = None
            txn.match_method = None
    candidate = session.get(RecurringCandidate, payload.get("candidate_id"))
    if candidate is not None:
        candidate.status = "new"
        candidate.line_id = None
    if payload.get("created_line") and line_id is not None:
        session.flush()
        for version in session.scalars(
            select(BudgetLineVersion).where(BudgetLineVersion.line_id == line_id)
        ):
            session.delete(version)
        session.flush()
        line = session.get(BudgetLine, line_id)
        if line is not None:
            session.delete(line)
    item.payload_json = {
        k: v
        for k, v in payload.items()
        if k not in ("line_id", "created_line", "linked", "rule_id", "claimed")
    }


def _undo_stop(session: Session, item: InboxItem) -> None:
    """Take back "it's stopped": the line runs on as before."""
    payload = item.payload_json
    version = session.get(BudgetLineVersion, payload.get("ended_version_id"))
    month = date.fromisoformat(payload["month"])
    if version is None or version.effective_to != month:
        raise InboxError("That line has changed since; restart it from the Budget instead")
    previous = payload.get("previous_effective_to")
    version.effective_to = date.fromisoformat(previous) if previous else None
    item.payload_json = {
        k: v for k, v in payload.items() if k not in ("ended_version_id", "previous_effective_to")
    }


# ---- planning reminders (Phases 6 and 7) -------------------------------------------------


def take_goal_amount(session: Session, item: InboxItem, actor: User | None, today: date) -> None:
    """A savings goal's monthly amount: the live line moves to it from this month."""
    _open(item, "goal_adjust")
    if item.line_id is None:
        raise InboxError("That goal has no budget line")
    line = must_get(session, BudgetLine, item.line_id)
    when = today.replace(day=1)
    try:
        made = editing.update_live_line(
            session, line, {"amount_pence": item.payload_json["needed_pence"]}, when, actor
        )
    except VersionError as exc:
        raise InboxError(str(exc)) from exc
    _resolve(item, "approved", actor)
    item.payload_json = item.payload_json | {
        "effective_from": when.isoformat(),
        "line_version_id": made.id if made else None,
        "offered": [],
        "pending": [],
    }
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "approve", None, item.payload_json)


def use_landed_pay(session: Session, item: InboxItem, actor: User | None, today: date) -> None:
    """Pay that didn't match: the live budget's take-home becomes what landed."""
    from app.domain.takehome import latest_sources
    from app.domain.versions import live_version, resolve

    _open(item, "salary_mismatch")
    payload = item.payload_json
    source = next((s for s in latest_sources(session) if s.person.key == payload["person"]), None)
    if source is None:
        raise InboxError("There's no income set up for them")
    live = live_version(session)
    before = next(
        (
            i.net_monthly_pence
            for i in resolve(session, live, today).incomes
            if i.person == payload["person"]
        ),
        None,
    )
    editing.set_net_income(session, live, source, payload["landed_pence"], actor)
    _resolve(item, "approved", actor)
    item.payload_json = payload | {"previous_net_pence": before}
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "approve", None, item.payload_json)


def _undo_salary(session: Session, item: InboxItem, actor: User | None) -> None:
    from app.domain.takehome import latest_sources
    from app.domain.versions import live_version

    payload = item.payload_json
    source = next((s for s in latest_sources(session) if s.person.key == payload["person"]), None)
    if source is not None:
        editing.set_net_income(
            session, live_version(session), source, payload.get("previous_net_pence"), actor
        )
    item.payload_json = {k: v for k, v in payload.items() if k != "previous_net_pence"}


def done(session: Session, item: InboxItem, actor: User | None) -> None:
    """A reminder dealt with. A renewal moves on to next year's date."""
    if item.type not in ("renewal_due", "remortgage_window", "agreement_ending"):
        raise InboxError("That isn't a reminder")
    if item.status not in ("open", "snoozed"):
        raise InboxError("That item has already been dealt with")
    if item.type == "renewal_due":
        renewal = session.get(Renewal, item.payload_json.get("renewal_id") or 0)
        if renewal is not None:
            item.payload_json = item.payload_json | {
                "previous_renews_on": renewal.renews_on.isoformat()
            }
            renewal.renews_on = _next_year(renewal.renews_on)
    _resolve(item, "approved", actor)
    session.flush()
    audit.record(session, actor, "inbox_item", item.id, "done", None, item.payload_json)


def _next_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year + 1, day=28)


def _undo_renewal(session: Session, item: InboxItem) -> None:
    payload = item.payload_json
    renewal = session.get(Renewal, payload.get("renewal_id") or 0)
    if renewal is not None and payload.get("previous_renews_on"):
        renewal.renews_on = date.fromisoformat(payload["previous_renews_on"])
    item.payload_json = {k: v for k, v in payload.items() if k != "previous_renews_on"}
