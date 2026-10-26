"""Bank CSV parsing: pure, no database. Every file here is synthetic."""

from datetime import date

import pytest

from app.providers.csv_import import ColumnMap, CsvError, detect, parse, parse_money
from app.sync.merchants import Haystack, normalise, tokens
from tests import bank_fixtures as bf


@pytest.mark.parametrize(
    ("text", "pence"),
    [
        ("12.99", 1299),
        ("-12.99", -1299),
        ("£1,234.56", 123456),
        ("(12.99)", -1299),
        ("12.99-", -1299),
        ("+4.5", 450),
        ("0", 0),
        (" -0.01 ", -1),
    ],
)
def test_money_is_exact_pence(text: str, pence: int) -> None:
    assert parse_money(text) == pence


@pytest.mark.parametrize("text", ["", "abc", "1.234", "12.999"])
def test_money_rejects_what_isnt_an_amount(text: str) -> None:
    with pytest.raises(CsvError):
        parse_money(text)


def test_each_bank_is_recognised_by_its_headers() -> None:
    for fmt, text in bf.month_files().values():
        assert parse(text).format == fmt
    assert detect(["Date", "Memo", "Amount"]) is None
    with pytest.raises(CsvError, match="Choose which column"):
        parse("Date,Memo,Amount\n01/11/2026,Thing,-1.00\n")


def test_starling_rows() -> None:
    result = parse(bf.starling_csv(bf.JOINT_BILLING, bf.GROCERY_CATEGORIES))
    assert not result.errors
    assert len(result.rows) == len(bf.JOINT_BILLING)
    sky = next(r for r in result.rows if r.counterparty == "SKY DIGITAL")
    assert (sky.booked_date, sky.amount_pence, sky.description) == (
        date(2026, 11, 1),
        -10850,
        "SKY0123456",
    )
    assert sky.balance_pence is not None
    tesco = next(r for r in result.rows if (r.counterparty or "").startswith("TESCO"))
    assert tesco.category == "GROCERIES"


def test_rbs_dates_blank_first_line_and_bom() -> None:
    result = parse("﻿" + bf.rbs_csv(bf.PERSON_2_RBS))
    assert result.format == "rbs"
    assert [r.booked_date for r in result.rows][:2] == [date(2026, 10, 28), date(2026, 10, 30)]
    assert result.rows[0].amount_pence == 152375
    assert result.rows[1].amount_pence == -70000


def test_monzo_keeps_the_bank_transaction_id() -> None:
    result = parse(bf.monzo_csv(bf.PERSON_1_MONZO))
    assert result.rows[0].external_id == "tx_0000Synthetic0000"
    assert result.rows[0].counterparty == "EXAMPLE LTD SALARY"


def test_rows_without_an_id_hash_stably_and_twins_stay_apart() -> None:
    text = bf.rbs_csv(
        [(date(2026, 11, 6), "COSTA COFFEE", -385), (date(2026, 11, 6), "COSTA COFFEE", -385)]
    )
    first, again = parse(text), parse(text)
    ids = [r.external_id for r in first.rows]
    assert ids == [r.external_id for r in again.rows]
    assert len(set(ids)) == 2
    assert all(i.startswith("h:") for i in ids)


def test_a_mapped_file_with_money_out_and_in_columns() -> None:
    text = (
        "When,Payee,Paid out,Paid in\n"
        "2026-11-03,DOORSTEP MILK,32.00,\n"
        "2026-11-04,REFUND,,5.00\n"
        "not a date,BROKEN,1.00,\n"
    )
    mapping = ColumnMap(
        date="When",
        description="Payee",
        money_out="Paid out",
        money_in="Paid in",
        date_format="%Y-%m-%d",
    )
    result = parse(text, mapping)
    assert result.format == "mapped"
    assert [(r.booked_date, r.amount_pence) for r in result.rows] == [
        (date(2026, 11, 3), -3200),
        (date(2026, 11, 4), 500),
    ]
    assert result.errors == [(4, "Not a date: 'not a date'")]


def test_a_mapping_naming_a_missing_column_says_which() -> None:
    with pytest.raises(CsvError, match="'Amount'"):
        parse(
            "Date,Payee\n01/11/2026,X\n",
            ColumnMap(date="Date", description="Payee", amount="Amount"),
        )
    with pytest.raises(CsvError, match="empty"):
        parse("\n\n")


# ---- merchants ----------------------------------------------------------------------------


def test_merchant_names_drop_codes_and_references() -> None:
    # Initials stay in capitals (EE, BP, NHS); small words go small.
    assert normalise("PETS AT HOME", None, []) == "Pets at Home"
    assert normalise("SOC OF ENGINEERS", None, []) == "Soc of Engineers"
    assert normalise("EE LIMITED", None, []) == "EE"
    assert normalise("1ST CENTRAL INS", None, []) == "1st Central Ins"
    assert normalise("DD SKY DIGITAL 4471029 GB", None, []) == "SKY Digital"
    assert normalise("SKY0123456", "SKY DIGITAL", []) == "SKY Digital"
    assert normalise("CARD PAYMENT TO TESCO STORES 3021 ON 02/11", None, []) == "Tesco Stores"
    assert (
        normalise("BLACK HORSE LTD 0042", None, [("BLACK HORSE", "Car finance")]) == "Car finance"
    )


def test_short_tokens_must_be_whole_words() -> None:
    hay = Haystack("EE LIMITED", "07700900123")
    assert hay.has("ee")
    assert not Haystack("BANK FEES").has("ee")
    assert Haystack("1ST CENTRAL INS").has_phrase("1stCentral")
    assert tokens("Phones") == set()
    assert tokens("Ultimate Pet Partners") == {"ultimate", "pet", "partners"}
