"""Transactions, CSV import, matching rules and actual vs planned (PLAN.md Â§8, Phase 3)."""

from dataclasses import asdict
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.api.deps import SessionDep, UserDep, current_session
from app.api.versions import today
from app.domain import audit
from app.domain.actuals import export_coverage, month_actuals
from app.domain.models import (
    Account,
    BankTransaction,
    CategoryRule,
    InboxItem,
    MerchantAlias,
    RecurringCandidate,
)
from app.domain.versions import live_version
from app.providers.csv_import import ColumnMap, CsvError
from app.sync import alerts, inbox, ingest, matching, review

router = APIRouter(prefix="/api", tags=["transactions"], dependencies=[Depends(current_session)])

MAX_CSV_BYTES = 5_000_000


class Mapping(BaseModel):
    date: str
    description: str
    amount: str | None = None
    money_out: str | None = None
    money_in: str | None = None
    counterparty: str | None = None
    date_format: str = "%d/%m/%Y"


class CsvIn(BaseModel):
    text: str = Field(max_length=MAX_CSV_BYTES)
    filename: str | None = None
    account: str | None = None
    format: Literal["monzo", "starling", "rbs"] | None = None
    mapping: Mapping | None = None


class TransactionOut(BaseModel):
    id: int
    account: str
    booked_date: date
    amount_pence: int
    description: str
    counterparty: str | None
    merchant: str
    category: str | None
    line_id: int | None
    line_name: str | None
    match_method: str | None
    status: Literal["pending", "booked"] = "booked"
    source: Literal["bank", "csv"] = "csv"


class MatchIn(BaseModel):
    line_id: int | None = None
    category: str | None = None
    always: bool = False


class AliasIn(BaseModel):
    pattern: str = Field(min_length=3, max_length=200)
    merchant: str = Field(min_length=1, max_length=200)


def _account(session: SessionDep, key: str | None) -> Account:
    if not key:
        raise HTTPException(422, "Choose which account this file is from")
    account = session.scalars(select(Account).where(Account.key == key)).first()
    if account is None:
        raise HTTPException(404, "No such account")
    return account


def _mapping(body: CsvIn) -> ColumnMap | Literal["monzo", "starling", "rbs"] | None:
    if body.mapping is not None:
        return ColumnMap(**body.mapping.model_dump())
    return body.format


def txn_out(t: BankTransaction) -> TransactionOut:
    line = t.matched_line
    return TransactionOut(
        id=t.id,
        account=t.account.key,
        booked_date=t.booked_date,
        amount_pence=t.amount_pence,
        description=t.description,
        counterparty=t.counterparty,
        merchant=t.merchant_normalised,
        category=t.category.name if t.category else None,
        line_id=t.matched_line_id,
        line_name=(f"{line.name} ({line.provider})" if line.provider else line.name)
        if line
        else None,
        match_method=t.match_method,
        status=t.status,
        source="bank" if t.connection_id else "csv",
    )


@router.post("/imports/preview")
def preview_csv(body: CsvIn, session: SessionDep) -> dict[str, Any]:
    account = _account(session, body.account) if body.account else None
    try:
        result = ingest.preview(session, account, body.text, _mapping(body))
    except CsvError as exc:
        raise HTTPException(422, str(exc)) from exc
    data = asdict(result)
    if account is not None:
        data["last_format"] = ingest.last_format_for(session, account)
    return data


@router.post("/imports", status_code=201)
def import_csv(body: CsvIn, user: UserDep, session: SessionDep) -> dict[str, Any]:
    account = _account(session, body.account)
    try:
        summary = ingest.import_csv(
            session, account, body.text, body.filename, _mapping(body), user
        )
    except CsvError as exc:
        raise HTTPException(422, str(exc)) from exc
    # What the new rows show straight away (Home Assistant hears after the next sync).
    found = alerts.look(session, today())
    session.commit()
    return asdict(summary) | {
        "regular_proposed": found.recurring.proposed,
        "missed": found.missed,
        "still_paying": found.still_paying,
    }


@router.get("/transactions", response_model=list[TransactionOut])
def transactions(
    session: SessionDep,
    account: str | None = None,
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    # matched: to a bill or a transfer; categorised: everyday spending with a category;
    # needs: money out that's neither, so someone should look at it.
    status: Literal["matched", "categorised", "needs"] | None = None,
    q: str | None = None,
    limit: int = Query(500, le=2000),
) -> list[TransactionOut]:
    query = (
        select(BankTransaction)
        .join(Account)
        .order_by(BankTransaction.booked_date.desc(), BankTransaction.id.desc())
    )
    if account:
        query = query.where(Account.key == account)
    if month:
        y, m = (int(x) for x in month.split("-"))
        query = query.where(
            BankTransaction.booked_date >= date(y, m, 1),
            BankTransaction.booked_date < date(y + m // 12, m % 12 + 1, 1),
        )
    unmatched = (
        BankTransaction.matched_line_id.is_(None),
        BankTransaction.transfer_pair_id.is_(None),
        or_(BankTransaction.match_method.is_(None), BankTransaction.match_method != "transfer"),
    )
    if status == "matched":
        query = query.where(
            or_(
                BankTransaction.matched_line_id.is_not(None),
                BankTransaction.transfer_pair_id.is_not(None),
                BankTransaction.match_method == "transfer",
            )
        )
    elif status == "categorised":
        query = query.where(*unmatched, BankTransaction.category_id.is_not(None))
    elif status == "needs":
        query = query.where(
            *unmatched,
            BankTransaction.category_id.is_(None),
            BankTransaction.amount_pence < 0,
            # Our own money moving into a Monzo pot or Starling Space isn't spending.
            or_(
                BankTransaction.bank_category.is_(None),
                BankTransaction.bank_category != "pot_transfer",
            ),
        )
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(
                BankTransaction.description.ilike(like),
                BankTransaction.counterparty.ilike(like),
                BankTransaction.merchant_normalised.ilike(like),
            )
        )
    return [txn_out(t) for t in session.scalars(query.limit(limit))]


@router.patch("/transactions/{transaction_id}")
def match_transaction(
    transaction_id: int, body: MatchIn, user: UserDep, session: SessionDep
) -> dict[str, Any]:
    txn = session.get(BankTransaction, transaction_id)
    if txn is None:
        raise HTTPException(404, "No such transaction")
    try:
        result = review.match_manually(
            session, txn, user, line_id=body.line_id, category=body.category, always=body.always
        )
    except review.ReviewError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return {"transaction": txn_out(txn).model_dump(mode="json"), **asdict(result)}


@router.get("/rules")
def rules(session: SessionDep) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "match": r.match_json,
            "line_id": r.set_line_id,
            "category_id": r.set_category_id,
        }
        for r in session.scalars(select(CategoryRule).order_by(CategoryRule.id))
    ]


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, user: UserDep, session: SessionDep) -> None:
    rule = session.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(404, "No such rule")
    audit.record(session, user, "category_rule", rule.id, "delete", rule.match_json, None)
    session.delete(rule)
    session.commit()


@router.get("/aliases")
def merchant_aliases(session: SessionDep) -> list[dict[str, Any]]:
    return [
        {"id": a.id, "pattern": a.pattern, "merchant": a.merchant}
        for a in session.scalars(select(MerchantAlias).order_by(MerchantAlias.merchant))
    ]


@router.post("/aliases", status_code=201)
def save_alias(body: AliasIn, user: UserDep, session: SessionDep) -> dict[str, int]:
    try:
        matched = review.add_alias(session, body.pattern, body.merchant, user)
    except review.ReviewError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return {"newly_matched": matched}


@router.get("/actuals")
def actuals(
    session: SessionDep, month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")
) -> dict[str, Any]:
    """Live budget, planned vs actual, for a month (default: this one), and the days each
    account's exports cover, so "not seen yet" can be read against them."""
    when = today()
    y, m = (int(x) for x in month.split("-")) if month else (when.year, when.month)
    return {
        "lines": [asdict(a) for a in month_actuals(session, live_version(session), y, m)],
        "covered": {
            key: {"from": lo, "to": hi} for key, (lo, hi) in export_coverage(session, y, m).items()
        },
    }


@router.get("/recurring")
def recurring_payments(session: SessionDep) -> list[dict[str, Any]]:
    """Regular payments found in the bank data: the bills we budget for, and the rest."""
    lines = {ln.line_id: ln for ln in matching.bill_lines(session, today())}
    rows = session.scalars(
        select(RecurringCandidate).order_by(RecurringCandidate.account_id, RecurringCandidate.id)
    ).all()
    return [
        {
            "id": c.id,
            "account": c.account.key,
            "payee": c.merchant_normalised,
            "cadence": c.cadence,
            "amount_pence": c.typical_amount_pence,
            "day_of_month": c.day_of_month,
            "occurrences": c.occurrences,
            "first_seen": c.first_seen.isoformat(),
            "last_seen": c.last_seen.isoformat(),
            "previous_amount_pence": c.previous_amount_pence,
            "price_changed_on": c.price_changed_on.isoformat() if c.price_changed_on else None,
            "status": c.status,
            "line_id": c.line_id,
            "line_label": lines[c.line_id].label if c.line_id in lines else None,
        }
        for c in rows
    ]


class PayeeLinkIn(BaseModel):
    line_id: int


@router.post("/recurring/{candidate_id}/link")
def link_payee(
    candidate_id: int, body: PayeeLinkIn, user: UserDep, session: SessionDep
) -> dict[str, int]:
    """A regular payee is that budget line: new, set aside, or matched to the wrong one."""
    candidate = session.get(RecurringCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "No such regular payment")
    try:
        line = inbox.link_payee(session, candidate, body.line_id, user)
    except inbox.InboxError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return {"line_id": line.id}


@router.post("/recurring/{candidate_id}/suggest", status_code=204)
def suggest_again(candidate_id: int, user: UserDep, session: SessionDep) -> None:
    """Something marked "not a bill" after all: back in the inbox to decide again."""
    candidate = session.get(RecurringCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "No such regular payment")
    if candidate.status != "ignored":
        raise HTTPException(422, "That one isn't set aside")
    candidate.status = "new"
    item = session.scalars(
        select(InboxItem).where(InboxItem.dedupe_key == f"new_recurring:{candidate.id}")
    ).first()
    if item is not None:
        item.status = "open"
        item.resolved_by = None
        item.resolved_at = None
    audit.record(session, user, "recurring_candidate", candidate.id, "suggest_again")
    session.commit()
