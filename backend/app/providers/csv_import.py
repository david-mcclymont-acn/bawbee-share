"""Bank CSV exports -> normalised rows (PLAN.md §8.1: build CSV first; it's also the fallback).

Pure parsing, no database. Monzo, Starling and RBS exports are recognised by their headers;
anything else can be read with a ColumnMap the user sets up once.
"""

import csv
import hashlib
import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

Format = Literal["monzo", "starling", "rbs", "mapped"]


@dataclass(frozen=True)
class ColumnMap:
    """Where each piece of a row lives, by header name."""

    date: str
    description: str
    amount: str | None = None  # one signed column (money out negative)...
    money_out: str | None = None  # ...or separate out / in columns
    money_in: str | None = None
    counterparty: str | None = None
    transaction_id: str | None = None
    balance: str | None = None
    category: str | None = None
    date_format: str = "%d/%m/%Y"


MONZO = ColumnMap(
    date="Date",
    description="Description",
    amount="Amount",
    counterparty="Name",
    transaction_id="Transaction ID",
    category="Category",
)
STARLING = ColumnMap(
    date="Date",
    description="Reference",
    amount="Amount (GBP)",
    counterparty="Counter Party",
    balance="Balance (GBP)",
    category="Spending Category",
)
RBS = ColumnMap(
    date="Date",
    description="Description",
    amount="Value",
    balance="Balance",
    date_format="%d %b %Y",
)
KNOWN: dict[Format, ColumnMap] = {"monzo": MONZO, "starling": STARLING, "rbs": RBS}

# Headers that only that bank's export has, so detection doesn't guess.
_SIGNATURES: dict[Format, set[str]] = {
    "monzo": {"Transaction ID", "Name", "Money Out", "Money In"},
    "starling": {"Counter Party", "Reference", "Amount (GBP)"},
    "rbs": {"Account Name", "Account Number", "Value"},
}


@dataclass(frozen=True)
class ParsedRow:
    line_number: int
    booked_date: date
    amount_pence: int
    description: str
    counterparty: str | None
    external_id: str
    balance_pence: int | None
    category: str | None
    raw: dict[str, str]


@dataclass
class ParseResult:
    format: Format
    headers: list[str]
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)


class CsvError(ValueError):
    pass


def parse_money(text: str) -> int:
    """'£1,234.56', '-12.99', '(12.99)', '12.99-' -> pence, exactly (no floats)."""
    cleaned = text.strip().replace("£", "").replace(",", "").replace(" ", "")
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative, cleaned = True, cleaned[1:-1]
    if cleaned.endswith("-"):
        negative, cleaned = True, cleaned[:-1]
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CsvError(f"Not an amount: {text!r}") from exc
    pence = value * 100
    if pence != pence.to_integral_value():
        raise CsvError(f"Amount has more than two decimal places: {text!r}")
    return -int(pence) if negative else int(pence)


def _parse_date(text: str, fmt: str) -> date:
    text = text.strip()
    for candidate in (fmt, "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, candidate).date()
        except ValueError:
            continue
    raise CsvError(f"Not a date: {text!r}")


def _rows(text: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    text = text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    headers: list[str] = []
    rows: list[tuple[int, dict[str, str]]] = []
    for number, cells in enumerate(reader, start=1):
        if not any(c.strip() for c in cells):
            continue  # RBS exports start with a blank line
        if not headers:
            headers = [c.strip() for c in cells]
            continue
        rows.append(
            (
                number,
                {h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(headers)},
            )
        )
    if not headers:
        raise CsvError("The file is empty.")
    return headers, rows


def detect(headers: Iterable[str]) -> Format | None:
    present = set(headers)
    for name, signature in _SIGNATURES.items():
        if signature <= present:
            return name
    return None


def _external_id(row: ParsedRow, given: str | None, occurrence: int) -> str:
    if given:
        return given
    # No bank id: the same payment exported twice must hash the same, and two identical
    # coffees on one day must not collapse into one, hence the occurrence counter.
    key = "|".join(
        [
            row.booked_date.isoformat(),
            str(row.amount_pence),
            row.description,
            row.counterparty or "",
            "" if row.balance_pence is None else str(row.balance_pence),
            str(occurrence),
        ]
    )
    return "h:" + hashlib.sha256(key.encode()).hexdigest()[:40]


def parse(text: str, mapping: Format | ColumnMap | None = None) -> ParseResult:
    headers, raw_rows = _rows(text)
    if mapping is None:
        found = detect(headers)
        if found is None:
            raise CsvError(
                "This file's columns don't match a Monzo, Starling or RBS export. "
                "Choose which column holds what."
            )
        mapping = found
    fmt: Format = mapping if isinstance(mapping, str) else "mapped"
    columns = KNOWN[mapping] if isinstance(mapping, str) else mapping
    needed = [columns.date, columns.description]
    needed += (
        [columns.amount]
        if columns.amount
        else [c for c in (columns.money_out, columns.money_in) if c]
    )
    missing = [c for c in needed if c not in headers]
    if missing:
        raise CsvError(f"The file has no {', '.join(repr(m) for m in missing)} column.")
    if not columns.amount and not (columns.money_out or columns.money_in):
        raise CsvError("Choose the amount column, or the money out and money in columns.")

    result = ParseResult(format=fmt, headers=headers)
    seen: dict[tuple[str, int, str, str], int] = {}
    for number, raw in raw_rows:
        try:
            when = _parse_date(raw[columns.date], columns.date_format)
            if columns.amount:
                pence = parse_money(raw[columns.amount])
            else:
                out = raw.get(columns.money_out or "", "").strip()
                inn = raw.get(columns.money_in or "", "").strip()
                pence = (parse_money(inn) if inn else 0) - (abs(parse_money(out)) if out else 0)
            description = raw[columns.description] or raw.get(columns.counterparty or "", "")
            counterparty = raw.get(columns.counterparty) if columns.counterparty else None
            balance_text = raw.get(columns.balance or "", "") if columns.balance else ""
            balance = parse_money(balance_text) if balance_text else None
        except (CsvError, KeyError) as exc:
            result.errors.append((number, str(exc)))
            continue
        row = ParsedRow(
            line_number=number,
            booked_date=when,
            amount_pence=pence,
            description=description,
            counterparty=counterparty or None,
            external_id="",
            balance_pence=balance,
            category=(raw.get(columns.category) or None) if columns.category else None,
            raw=raw,
        )
        signature = (when.isoformat(), pence, description, counterparty or "")
        occurrence = seen.get(signature, 0)
        seen[signature] = occurrence + 1
        given = raw.get(columns.transaction_id) if columns.transaction_id else None
        result.rows.append(
            ParsedRow(**{**row.__dict__, "external_id": _external_id(row, given, occurrence)})
        )
    return result


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
