"""Bringing bank data into one account: idempotent, then matched (PLAN.md §8.3).

Each row is identified by the bank's id where it gives one, or a stable hash, so importing
the same file twice (or overlapping exports, or a sync that re-fetches the last week) adds
nothing the second time. Once an account is linked to a bank, the bank covers it from the
day after its last CSV row; CSV rows for days the bank has covered are skipped.
"""

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.models import (
    Account,
    BankAccountLink,
    BankConnection,
    BankTransaction,
    Category,
    ImportBatch,
    InboxItem,
    MerchantAlias,
    PaydayTransfer,
    User,
)
from app.providers.base import ProviderTransaction
from app.providers.csv_import import ColumnMap, Format, ParseResult, parse, sha256
from app.sync import matching
from app.sync.merchants import normalise


@dataclass
class PreviewRow:
    line_number: int
    booked_date: date
    amount_pence: int
    description: str
    counterparty: str | None
    merchant: str
    duplicate: bool


@dataclass
class Preview:
    format: str
    headers: list[str]
    rows: list[PreviewRow]
    total: int
    new: int
    duplicates: int
    first_date: date | None
    last_date: date | None
    errors: list[tuple[int, str]] = field(default_factory=list)
    bank_covered: int = 0  # rows for days this account's bank link already covers


@dataclass
class ImportSummary:
    batch_id: int
    format: str
    total: int
    new: int
    duplicates: int
    errors: list[tuple[int, str]]
    bills_matched: int
    transfers_matched: int
    payday_ticked: int
    rule_matched: int
    price_changes: int
    unmatched: int
    coverage: list[tuple[int, int, int, int]]  # (year, month, matched bills, bill lines)
    bank_covered: int = 0


def aliases(session: Session) -> list[tuple[str, str]]:
    return [(a.pattern, a.merchant) for a in session.scalars(select(MerchantAlias))]


def bank_spans(session: Session, account: Account) -> list[tuple[date | None, date]]:
    """The days a bank link has covered for this account: (from, through) per link."""
    return [
        (link.synced_from, link.synced_through)
        for link in session.scalars(
            select(BankAccountLink).where(
                BankAccountLink.account_id == account.id,
                BankAccountLink.synced_through.is_not(None),
            )
        )
        if link.synced_through is not None
    ]


def _bank_covers(spans: list[tuple[date | None, date]], day: date) -> bool:
    return any((start is None or day >= start) and day <= end for start, end in spans)


def _existing_ids(session: Session, account: Account, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    return set(
        session.scalars(
            select(BankTransaction.external_id).where(
                BankTransaction.account_id == account.id, BankTransaction.external_id.in_(ids)
            )
        )
    )


def preview(
    session: Session, account: Account | None, text: str, mapping: Format | ColumnMap | None
) -> Preview:
    result = parse(text, mapping)
    known = aliases(session)
    existing = (
        _existing_ids(session, account, [r.external_id for r in result.rows]) if account else set()
    )
    spans = bank_spans(session, account) if account else []
    covered = {r.line_number for r in result.rows if _bank_covers(spans, r.booked_date)}
    rows = [
        PreviewRow(
            line_number=r.line_number,
            booked_date=r.booked_date,
            amount_pence=r.amount_pence,
            description=r.description,
            counterparty=r.counterparty,
            merchant=normalise(r.description, r.counterparty, known),
            duplicate=r.external_id in existing or r.line_number in covered,
        )
        for r in result.rows
    ]
    dates = [r.booked_date for r in result.rows]
    duplicates = sum(1 for r in rows if r.duplicate)
    return Preview(
        format=result.format,
        headers=result.headers,
        rows=rows[:12],
        total=len(rows),
        new=len(rows) - duplicates,
        duplicates=duplicates,
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        errors=result.errors,
        bank_covered=len(covered),
    )


def _store(
    session: Session, account: Account, result: ParseResult, batch: ImportBatch
) -> tuple[list[BankTransaction], int, int]:
    known = aliases(session)
    categories = {c.name.casefold(): c.id for c in session.scalars(select(Category))}
    existing = _existing_ids(session, account, [r.external_id for r in result.rows])
    spans = bank_spans(session, account)
    created: list[BankTransaction] = []
    duplicates = covered = 0
    for row in result.rows:
        if _bank_covers(spans, row.booked_date):
            covered += 1  # the bank link already brought this day in
            continue
        if row.external_id in existing:
            duplicates += 1
            continue
        existing.add(row.external_id)
        txn = BankTransaction(
            account_id=account.id,
            external_id=row.external_id,
            booked_date=row.booked_date,
            status="booked",
            amount_pence=row.amount_pence,
            description=row.description,
            counterparty=row.counterparty,
            merchant_normalised=normalise(row.description, row.counterparty, known),
            bank_category=row.category,
            # A bank category with the same name as one of ours ("Groceries") is a fair start.
            category_id=categories.get((row.category or "").casefold()),
            import_batch_id=batch.id,
            raw_json=row.raw,
        )
        session.add(txn)
        created.append(txn)
    session.flush()
    return created, duplicates, covered


def import_csv(
    session: Session,
    account: Account,
    text: str,
    filename: str | None,
    mapping: Format | ColumnMap | None,
    actor: User | None,
) -> ImportSummary:
    result = parse(text, mapping)
    batch = ImportBatch(
        account_id=account.id,
        source="csv",
        format=result.format,
        filename=filename,
        file_sha256=sha256(text),
        rows_total=len(result.rows),
        created_by=actor.id if actor else None,
    )
    session.add(batch)
    session.flush()
    created, duplicates, covered = _store(session, account, result, batch)
    batch.rows_new, batch.rows_duplicate = len(created), duplicates + covered
    summary = matching.run(session, created)

    months = sorted({(t.booked_date.year, t.booked_date.month) for t in created})
    coverage = [(y, m, *matching.bill_coverage(session, y, m)) for y, m in months]
    audit.record(
        session,
        actor,
        "import_batch",
        batch.id,
        "import",
        None,
        {
            "account": account.key,
            "filename": filename,
            "new": len(created),
            "duplicates": duplicates,
            "bank_covered": covered,
            **asdict(summary),
        },
    )
    return ImportSummary(
        batch_id=batch.id,
        format=result.format,
        total=len(result.rows),
        new=len(created),
        duplicates=duplicates,
        errors=result.errors,
        bills_matched=summary.bills_matched,
        transfers_matched=summary.transfers_matched,
        payday_ticked=summary.payday_ticked,
        rule_matched=summary.rule_matched,
        price_changes=summary.price_changes,
        unmatched=summary.unmatched,
        coverage=coverage,
        bank_covered=covered,
    )


def last_format_for(session: Session, account: Account) -> str | None:
    """The format this account's last import used, so the next one is preselected."""
    return session.scalar(
        select(ImportBatch.format)
        .where(ImportBatch.account_id == account.id)
        .order_by(ImportBatch.id.desc())
        .limit(1)
    )


def count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(BankTransaction)) or 0


# ---- from a bank connection ------------------------------------------------------------

# A pending payment can book a few days later, under a new id.
PENDING_SETTLE_DAYS = 5


@dataclass
class BankIngest:
    created: list[BankTransaction]
    updated: int
    removed: int


def forget(session: Session, txns: list[BankTransaction]) -> None:
    """Clear what points at these rows, so they can be deleted cleanly."""
    ids = [t.id for t in txns]
    if not ids:
        return
    for planned in session.scalars(
        select(PaydayTransfer).where(PaydayTransfer.matched_transaction_id.in_(ids))
    ):
        planned.matched_transaction_id = None
    for item in session.scalars(select(InboxItem).where(InboxItem.transaction_id.in_(ids))):
        item.transaction_id = None
    for other in session.scalars(
        select(BankTransaction).where(BankTransaction.transfer_pair_id.in_(ids))
    ):
        other.transfer_pair_id = None
        if other.match_method == "transfer" and other.matched_line_id is None:
            other.match_method = None
    for t in txns:
        t.transfer_pair_id = None
    session.flush()


def ingest_bank(
    session: Session,
    account: Account,
    connection: BankConnection,
    rows: list[ProviderTransaction],
    window_start: date | None,
    floor: date | None,
) -> BankIngest:
    """Store what a sync fetched: new rows in, pending rows updated as they book, and
    pending rows the bank no longer shows taken out. Nothing before `floor` (the day the
    bank took over from CSV) is stored."""
    rows = [r for r in rows if floor is None or r.booked_date >= floor]
    fetched = {r.external_id for r in rows}
    known = aliases(session)
    categories = {c.name.casefold(): c.id for c in session.scalars(select(Category))}
    existing = (
        {
            t.external_id: t
            for t in session.scalars(
                select(BankTransaction).where(
                    BankTransaction.account_id == account.id,
                    BankTransaction.external_id.in_(fetched),
                )
            )
        }
        if fetched
        else {}
    )
    pending_query = select(BankTransaction).where(
        BankTransaction.account_id == account.id,
        BankTransaction.connection_id == connection.id,
        BankTransaction.status == "pending",
    )
    if window_start is not None:
        pending_query = pending_query.where(
            BankTransaction.booked_date >= window_start - timedelta(days=PENDING_SETTLE_DAYS)
        )
    pending = [p for p in session.scalars(pending_query) if p.external_id not in fetched]

    created: list[BankTransaction] = []
    updated = 0

    def _refresh(txn: BankTransaction, row: ProviderTransaction) -> None:
        txn.status = row.status
        txn.booked_date = row.booked_date
        txn.value_date = row.value_date
        txn.amount_pence = row.amount_pence
        txn.description = row.description
        txn.counterparty = row.counterparty
        txn.raw_json = row.raw

    for row in rows:
        txn = existing.get(row.external_id)
        if txn is not None:
            if row.bank_category == "pot_transfer" and txn.bank_category != "pot_transfer":
                # Stored before the bank's own-money moves were told apart (Starling Spaces).
                txn.bank_category, txn.counterparty = row.bank_category, row.counterparty
                updated += 1
            if txn.status == "pending":
                changed = (txn.status, txn.amount_pence, txn.booked_date) != (
                    row.status,
                    row.amount_pence,
                    row.booked_date,
                )
                _refresh(txn, row)
                updated += int(changed)
            continue
        if row.status == "booked":
            # A pending row of ours that has now booked under the bank's final id.
            twin = next(
                (
                    p
                    for p in pending
                    if p.amount_pence == row.amount_pence
                    and abs((p.booked_date - row.booked_date).days) <= PENDING_SETTLE_DAYS
                ),
                None,
            )
            if twin is not None:
                pending.remove(twin)
                twin.external_id = row.external_id
                _refresh(twin, row)
                updated += 1
                continue
        txn = BankTransaction(
            account_id=account.id,
            external_id=row.external_id,
            booked_date=row.booked_date,
            value_date=row.value_date,
            status=row.status,
            amount_pence=row.amount_pence,
            description=row.description,
            counterparty=row.counterparty,
            merchant_normalised=normalise(row.description, row.counterparty, known),
            bank_category=row.bank_category,
            category_id=categories.get((row.bank_category or "").casefold()),
            connection_id=connection.id,
            raw_json=row.raw,
        )
        session.add(txn)
        existing[row.external_id] = txn
        created.append(txn)

    # Pending rows in the fetched window that the bank no longer shows: they dropped, or
    # booked with a different amount (and came in above as a new row).
    gone = [p for p in pending if window_start is None or p.booked_date >= window_start]
    session.flush()
    forget(session, gone)
    for p in gone:
        session.delete(p)
    session.flush()
    return BankIngest(created, updated, len(gone))
