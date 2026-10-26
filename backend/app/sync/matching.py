"""Matching bank transactions to the budget (PLAN.md §8.3, §8.4).

1. Transfers between our own accounts pair up (equal and opposite within ±2 days), match
   their transfer line, and tick the payday checklist.
2. "Always match this payee" rules apply next.
3. Remaining payments are scored against the live budget's bills on that account by payee
   name, amount and day of month, best pairs first; composite bills take one payment per
   component.
4. A matched bill whose amount differs from the budget, by even a penny, raises a price
   change in the inbox. Nothing changes the budget until someone approves it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain import payday as payday_domain
from app.domain.models import (
    Account,
    BankTransaction,
    BudgetLineVersion,
    CategoryRule,
    InboxItem,
)
from app.domain.versions import ResolvedLine, calculate, live_version, resolve
from app.sync.merchants import Haystack, tokens

TRANSFER_WINDOW = timedelta(days=2)
PAYDAY_WINDOW = timedelta(days=3)
MATCH_THRESHOLD = 0.55


@dataclass
class MatchSummary:
    bills_matched: int = 0
    transfers_matched: int = 0
    payday_ticked: int = 0
    rule_matched: int = 0
    price_changes: int = 0
    unmatched: int = 0
    inbox_item_ids: list[int] = field(default_factory=list)


# ---- scoring ------------------------------------------------------------------------------


def _targets(line: ResolvedLine) -> list[int]:
    """The amounts a payment for this line might be: the whole, or one component."""
    amounts = [c["amount_pence"] for c in line.components if c.get("amount_pence")]
    whole = line.amount_pence or 0
    return [*amounts, whole] if amounts else [whole]


def _name_score(line: ResolvedLine, hay: Haystack) -> float:
    if line.provider and (
        hay.has_phrase(line.provider) or any(hay.has(t) for t in tokens(line.provider))
    ):
        return 1.0
    if hay.has_phrase(line.name) or any(hay.has(t) for t in tokens(line.name)):
        return 0.7
    return 0.0


def _amount_score(line: ResolvedLine, paid: int) -> float:
    targets = _targets(line)
    if paid in targets:
        return 1.0
    expected = line.amount_pence or 0
    if expected and abs(paid - expected) <= expected * 0.15:
        return 0.7
    if expected and abs(paid - expected) <= expected * 0.5:
        return 0.2
    return 0.0


def _day_score(line: ResolvedLine, when: date) -> float:
    if line.day_of_month is None:
        return 0.6
    gap = abs(when.day - line.day_of_month)
    gap = min(gap, 31 - gap)  # 30th vs 1st is one day apart, across the month end
    return 1.0 if gap <= 3 else 0.5 if gap <= 7 else 0.1


def score(line: ResolvedLine, txn: BankTransaction) -> float:
    paid = -txn.amount_pence
    hay = Haystack(txn.counterparty, txn.description, txn.merchant_normalised)
    name = _name_score(line, hay)
    amount = _amount_score(line, paid)
    day = _day_score(line, txn.booked_date)
    if name == 0 and not (amount == 1.0 and day >= 0.5):
        return 0.0
    return 0.5 * name + 0.35 * amount + 0.15 * day


# ---- helpers --------------------------------------------------------------------------------


def _month(d: date) -> tuple[int, int]:
    return d.year, d.month


def budget_start(session: Session) -> date | None:
    """The first day the live budget has lines for (when the seed was imported)."""
    return session.scalar(select(func.min(BudgetLineVersion.effective_from)))


def as_budgeted(session: Session, when: date) -> date:
    """History from before the budget existed is matched against the budget as it first
    stood: the bills were the same, even if nobody had written them down yet."""
    start = budget_start(session)
    return start if start is not None and when < start else when


def bill_lines(session: Session, when: date) -> list[ResolvedLine]:
    resolved = resolve(session, live_version(session), as_budgeted(session, when))
    return [ln for ln in resolved.lines if ln.kind == "bill" and (ln.amount_pence or 0) > 0]


def _capacity(line: ResolvedLine) -> int:
    return max(1, len([c for c in line.components if c.get("amount_pence")]))


def _already_matched(session: Session, line_id: int, year: int, month: int) -> int:
    first = date(year, month, 1)
    last = date(year + month // 12, month % 12 + 1, 1)
    return len(
        session.scalars(
            select(BankTransaction.id).where(
                BankTransaction.matched_line_id == line_id,
                BankTransaction.booked_date >= first,
                BankTransaction.booked_date < last,
            )
        ).all()
    )


def _raise(session: Session, summary: MatchSummary, **kwargs: Any) -> None:
    """Add an inbox item unless the same proposal is already there."""
    key = kwargs["dedupe_key"]
    if session.scalars(select(InboxItem).where(InboxItem.dedupe_key == key)).first():
        return
    item = InboxItem(**kwargs)
    session.add(item)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        return
    summary.inbox_item_ids.append(item.id)
    if item.type == "price_change":
        summary.price_changes += 1


def check_price(
    session: Session, txn: BankTransaction, line: ResolvedLine, summary: MatchSummary
) -> None:
    """A matched bill paid at a different amount: propose the new price (decision 19)."""
    start = budget_start(session)
    if start is not None and txn.booked_date < start:
        return  # before the budget existed there was no price to differ from
    paid = -txn.amount_pence
    if paid in _targets(line):
        return
    payload: dict[str, Any] = {
        "label": line.label,
        "account": line.account,
        "old_pence": line.amount_pence,
        "new_pence": paid,
        "seen_on": txn.booked_date.isoformat(),
        "frequency": line.frequency,
    }
    parts = [c for c in line.components if c.get("amount_pence")]
    if parts:
        # One policy of several changed: replace the nearest component.
        nearest = min(range(len(parts)), key=lambda i: abs(parts[i]["amount_pence"] - paid))
        new_parts = [dict(c) for c in parts]
        new_parts[nearest]["amount_pence"] = paid
        payload |= {
            "components": new_parts,
            "new_pence": sum(c["amount_pence"] for c in new_parts),
            "component_old_pence": parts[nearest]["amount_pence"],
            "component_new_pence": paid,
        }
    _raise(
        session,
        summary,
        type="price_change",
        dedupe_key=f"price_change:line:{line.line_id}:{payload['new_pence']}",
        line_id=line.line_id,
        transaction_id=txn.id,
        payload_json=payload,
    )


# ---- transfers and payday --------------------------------------------------------------------


def _tick_payday(session: Session, txn: BankTransaction, line_id: int, amount: int) -> bool:
    """Tick the payday transfer this bank transfer carried out, if there's one waiting."""
    start = budget_start(session)
    if start is not None and txn.booked_date < start - PAYDAY_WINDOW:
        return False  # paydays from before the budget had no checklist
    for back in (0, 1):
        year, month = _month(txn.booked_date.replace(day=1) - timedelta(days=28 * back))
        key = payday_domain.month_key(year, month)
        try:
            when = payday_domain.transfer_date(session, key)
        except payday_domain.PaydayError:
            return False
        if abs(when - txn.booked_date) > PAYDAY_WINDOW:
            continue
        run = payday_domain.start_run(session, key, None)
        for planned in run.transfers:
            effective = (
                planned.suggested_pence if planned.actual_pence is None else planned.actual_pence
            )
            if planned.line_id == line_id and effective == amount and planned.done_at is None:
                planned.done_at = datetime.combine(txn.booked_date, time(12), tzinfo=UTC)
                planned.done_by = None
                planned.done_via = "bank"
                planned.matched_transaction_id = txn.id
                return True
    return False


def match_transfers(session: Session, txns: list[BankTransaction], summary: MatchSummary) -> None:
    accounts = {a.id: a.key for a in session.scalars(select(Account))}
    ids = {t.id for t in txns}
    candidates = session.scalars(
        select(BankTransaction).where(
            BankTransaction.transfer_pair_id.is_(None),
            BankTransaction.match_method.is_(None) | (BankTransaction.match_method == "transfer"),
        )
    ).all()
    # Pair equal-and-opposite movements between our own accounts.
    for out in [t for t in candidates if t.amount_pence < 0 and t.transfer_pair_id is None]:
        for inn in candidates:
            if (
                inn.amount_pence == -out.amount_pence
                and inn.account_id != out.account_id
                and inn.transfer_pair_id is None
                and abs(inn.booked_date - out.booked_date) <= TRANSFER_WINDOW
                and (out.id in ids or inn.id in ids)
            ):
                out.transfer_pair_id, inn.transfer_pair_id = inn.id, out.id
                inn.match_method = "transfer"
                out.match_method = "transfer"
                break

    # Outgoing transfers match their transfer line: by the pair's accounts if we have both
    # sides, else by an exact amount on a planned transfer around a payday.
    for out in [t for t in txns if t.amount_pence < 0 and t.matched_line_id is None]:
        lines = [
            ln
            for ln in resolve(
                session, live_version(session), as_budgeted(session, out.booked_date)
            ).lines
            if ln.kind == "transfer" and ln.account == accounts[out.account_id]
        ]
        if not lines:
            continue
        result = calculate(
            resolve(session, live_version(session), as_budgeted(session, out.booked_date))
        )
        amounts = {
            int(t.line_key.removeprefix("line:")): t.effective_pence for t in result.transfers
        }
        paid = -out.amount_pence
        to_key = None
        if out.transfer_pair_id:
            pair = session.get(BankTransaction, out.transfer_pair_id)
            to_key = accounts[pair.account_id] if pair else None
        fits = [
            ln
            for ln in lines
            if amounts.get(ln.line_id) == paid and (to_key is None or ln.to_account == to_key)
        ]
        if len(fits) != 1:
            continue
        out.matched_line_id = fits[0].line_id
        out.match_method = "transfer"
        summary.transfers_matched += 1
        if _tick_payday(session, out, fits[0].line_id, paid):
            summary.payday_ticked += 1


# ---- rules and bills -------------------------------------------------------------------------


def _rule_matches(rule: CategoryRule, txn: BankTransaction, account_key: str) -> bool:
    want = rule.match_json
    if "account" in want and want["account"] != account_key:
        return False
    if "merchant" in want and want["merchant"].casefold() != txn.merchant_normalised.casefold():
        return False
    if "contains" in want:
        text = f"{txn.counterparty or ''} {txn.description}".casefold()
        if want["contains"].casefold() not in text:
            return False
    return bool(want)


def apply_rules(session: Session, txns: list[BankTransaction], summary: MatchSummary) -> None:
    rules = session.scalars(
        select(CategoryRule).order_by(CategoryRule.priority.desc(), CategoryRule.id)
    ).all()
    accounts = {a.id: a.key for a in session.scalars(select(Account))}
    for txn in txns:
        if txn.match_method in ("manual", "transfer"):
            continue
        for rule in rules:
            if _rule_matches(rule, txn, accounts[txn.account_id]):
                if rule.set_category_id:
                    txn.category_id = rule.set_category_id
                if rule.set_line_id:
                    txn.matched_line_id = rule.set_line_id
                    txn.match_method = "rule"
                    summary.rule_matched += 1
                break


def match_bills(session: Session, txns: list[BankTransaction], summary: MatchSummary) -> None:
    accounts = {a.id: a.key for a in session.scalars(select(Account))}
    # Group by account and month: lines resolve per month, and each takes one payment
    # (or one per component) in a month.
    groups: dict[tuple[int, int, int], list[BankTransaction]] = defaultdict(list)
    for txn in txns:
        if txn.amount_pence < 0 and txn.matched_line_id is None and txn.match_method is None:
            groups[(txn.account_id, *_month(txn.booked_date))].append(txn)

    for (account_id, year, month), group in groups.items():
        lines = [
            ln
            for ln in bill_lines(session, date(year, month, 1))
            if ln.account == accounts[account_id]
        ]
        room = {
            ln.line_id: _capacity(ln) - _already_matched(session, ln.line_id, year, month)
            for ln in lines
        }
        pairs = sorted(
            ((score(ln, txn), ln, txn) for ln in lines for txn in group),
            key=lambda p: (-p[0], p[2].booked_date, p[2].id),
        )
        taken: set[int] = set()
        for value, line, txn in pairs:
            if value < MATCH_THRESHOLD or txn.id in taken or room[line.line_id] <= 0:
                continue
            txn.matched_line_id = line.line_id
            txn.match_method = "auto"
            taken.add(txn.id)
            room[line.line_id] -= 1
            summary.bills_matched += 1
            check_price(session, txn, line, summary)
        summary.unmatched += len([t for t in group if t.id not in taken])

    # Rule-matched and manually matched bills get the same price check.
    for txn in txns:
        if txn.match_method == "rule" and txn.matched_line_id:
            rule_line = next(
                (
                    ln
                    for ln in bill_lines(session, txn.booked_date)
                    if ln.line_id == txn.matched_line_id
                ),
                None,
            )
            if rule_line is not None:
                check_price(session, txn, rule_line, summary)


def run(session: Session, txns: list[BankTransaction]) -> MatchSummary:
    summary = MatchSummary()
    session.flush()
    match_transfers(session, txns, summary)
    apply_rules(session, txns, summary)
    match_bills(session, txns, summary)
    session.flush()
    return summary


def bill_coverage(session: Session, year: int, month: int) -> tuple[int, int]:
    """How many of this month's monthly bill lines have a matched payment: (matched, total)."""
    lines = [ln for ln in bill_lines(session, date(year, month, 1)) if ln.frequency == "monthly"]
    matched = sum(1 for ln in lines if _already_matched(session, ln.line_id, year, month) > 0)
    return matched, len(lines)
