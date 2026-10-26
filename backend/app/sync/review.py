"""People correcting the matcher: match by hand, "always match this payee", aliases."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import must_get
from app.domain import audit
from app.domain.models import (
    Account,
    BankTransaction,
    BudgetLine,
    Category,
    CategoryRule,
    MerchantAlias,
    User,
)
from app.sync import matching
from app.sync.ingest import aliases
from app.sync.merchants import normalise


class ReviewError(ValueError):
    pass


@dataclass
class ReviewResult:
    rule_id: int | None
    also_matched: int
    price_changes: int


def _category_id(session: Session, name: str | None) -> int | None:
    if not name:
        return None
    category = session.scalars(select(Category).where(Category.name == name)).first()
    if category is None:
        category = Category(name=name)
        session.add(category)
        session.flush()
    return category.id


def match_manually(
    session: Session,
    txn: BankTransaction,
    actor: User | None,
    *,
    line_id: int | None,
    category: str | None,
    always: bool,
) -> ReviewResult:
    if line_id is not None and session.get(BudgetLine, line_id) is None:
        raise ReviewError("No such budget line")
    before = {"line_id": txn.matched_line_id, "category_id": txn.category_id}
    txn.matched_line_id = line_id
    # "manual" also when unmatching, so automatic matching leaves it alone from now on.
    txn.match_method = "manual"
    txn.category_id = _category_id(session, category)
    summary = matching.MatchSummary()
    line = next(
        (ln for ln in matching.bill_lines(session, txn.booked_date) if ln.line_id == line_id), None
    )
    if line is not None and txn.amount_pence < 0:
        matching.check_price(session, txn, line, summary)

    rule_id = None
    also = 0
    if always:
        account = must_get(session, Account, txn.account_id)
        rule = CategoryRule(
            priority=100,
            match_json={"merchant": txn.merchant_normalised, "account": account.key},
            set_line_id=line_id,
            set_category_id=txn.category_id,
            created_by=actor.id if actor else None,
        )
        session.add(rule)
        session.flush()
        rule_id = rule.id
        others = session.scalars(
            select(BankTransaction).where(
                BankTransaction.account_id == txn.account_id,
                BankTransaction.merchant_normalised == txn.merchant_normalised,
                BankTransaction.id != txn.id,
                BankTransaction.match_method.is_(None) | (BankTransaction.match_method == "auto"),
            )
        ).all()
        for other in others:
            other.matched_line_id = None
            other.match_method = None
        matching.apply_rules(session, list(others), summary)
        also = summary.rule_matched
        matching.match_bills(session, [o for o in others if o.matched_line_id], summary)
    session.flush()
    audit.record(
        session,
        actor,
        "bank_transaction",
        txn.id,
        "match",
        before,
        {"line_id": line_id, "category_id": txn.category_id, "rule_id": rule_id},
    )
    return ReviewResult(rule_id, also, summary.price_changes)


def add_alias(session: Session, pattern: str, merchant: str, actor: User | None) -> int:
    """Teach the matcher a payee name; re-match what's still unmatched. Returns new matches."""
    pattern, merchant = pattern.strip(), merchant.strip()
    if len(pattern) < 3 or not merchant:
        raise ReviewError("Give at least three characters of bank text, and a merchant name")
    existing = session.scalars(
        select(MerchantAlias).where(MerchantAlias.pattern == pattern)
    ).first()
    if existing:
        existing.merchant = merchant
    else:
        session.add(
            MerchantAlias(
                pattern=pattern, merchant=merchant, created_by=actor.id if actor else None
            )
        )
    session.flush()
    known = aliases(session)
    touched = [
        t
        for t in session.scalars(
            select(BankTransaction).where(BankTransaction.match_method.is_(None))
        )
        if pattern.upper() in f"{t.counterparty or ''} {t.description}".upper()
    ]
    for txn in touched:
        txn.merchant_normalised = normalise(txn.description, txn.counterparty, known)
    summary = matching.run(session, touched)
    audit.record(
        session,
        actor,
        "merchant_alias",
        None,
        "save",
        None,
        {"pattern": pattern, "merchant": merchant},
    )
    return summary.bills_matched + summary.rule_matched + summary.transfers_matched
