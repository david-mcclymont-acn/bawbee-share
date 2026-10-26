"""A synthetic month of bank exports (late October to November 2026) for the live budget.

Made-up data in each bank's real export format: never real statements. Most payments use
the text a bank would show; a few are deliberately hard (the payee's name isn't the budget
line's: Anthropic for Claude AI, Black Horse for the car), so the 80% target is honest.
"""

import csv
import io
from datetime import date

Row = tuple[date, str, int]  # (date, bank text, pence; money out negative)

NOV = 11
OCT = 10

JOINT_BILLING: list[Row] = [
    (date(2026, OCT, 30), "P ONE|JOINT BILLING", 267000),
    (date(2026, OCT, 30), "P TWO|BILLS", 70000),
    (date(2026, NOV, 1), "SKY DIGITAL|SKY0123456", -10850),  # Sky has gone up: £104.75 -> £108.50
    (date(2026, NOV, 1), "TV LICENCE MBP|TVL 4421", -1500),
    (date(2026, NOV, 1), "EXAMPLETOWN COUNCIL|CTAX 99102", -25980),
    (date(2026, NOV, 1), "NORTHFIELD B S|MORTGAGE 776512", -119246),
    (date(2026, NOV, 3), "DOORSTEP MILK|ROUND 12", -3200),
    (date(2026, NOV, 5), "GIG CLUB LTD|MEMBERSHIP", -3800),
    (date(2026, NOV, 6), "1ST CENTRAL INS|POLICY 88213", -4127),
    (date(2026, NOV, 8), "PAYPAL *SPOTIFY|35314369001", -1799),
    (date(2026, NOV, 9), "PINK RIBBON FOUNDATION|DONATION", -1000),
    (date(2026, NOV, 10), "ROVER PHYSIO|SESSIONS", -12500),
    (date(2026, NOV, 12), "VODAFONE LIMITED|7741203", -2612),
    (date(2026, NOV, 13), "HILL AND WARD|FACTOR 2211", -2100),
    (date(2026, NOV, 14), "EE LIMITED|07700900123", -4012),
    (date(2026, NOV, 15), "AGEAS INSURANCE|HOME 55121", -3368),
    (date(2026, NOV, 18), "AVIVA LIFE|POLICY A", -4236),
    (date(2026, NOV, 18), "AVIVA LIFE|POLICY B", -3678),
    (date(2026, NOV, 20), "A REID WINDOWS|WINDOWS", -1200),
    (date(2026, NOV, 22), "GLOW CLINIC|CARD", -2900),
    (date(2026, NOV, 24), "ULTIMATE PET PARTNE|DOG 1", -6512),
    (date(2026, NOV, 24), "ULTIMATE PET PARTNE|DOG 2", -7362),
    (date(2026, NOV, 25), "OCTOPUS ENERGY|A-3F21C9", -16244),
    # Everyday spending that isn't a bill.
    (date(2026, NOV, 2), "TESCO STORES 3021|CARD 4421", -8412),
    (date(2026, NOV, 9), "ALDI 123|CARD 4421", -4230),
    (date(2026, NOV, 16), "SAINSBURYS|CARD 4421", -6000),
    (date(2026, NOV, 19), "PETS AT HOME|CARD 4421", -2899),
]

PERSON_1_STARLING: list[Row] = [
    (date(2026, OCT, 30), "P ONE|SUBS", 13500),
    (date(2026, NOV, 1), "PATREON* MEMBERSHIP|PATREON", -350),
    (date(2026, NOV, 2), "GOOGLE *WORKSPACE|G.CO/HELPPAY", -1299),
    (date(2026, NOV, 4), "SIMPLISAFE|MONITORING", -2799),
    (date(2026, NOV, 7), "PREMIER SPORTS|SUBSCRIPTION", -1599),
    (date(2026, NOV, 11), "APPLE.COM/BILL|ITUNES.COM", -999),
    (date(2026, NOV, 11), "APPLE.COM/BILL|ITUNES.COM", -299),
    (date(2026, NOV, 11), "APPLE.COM/BILL|ITUNES.COM", -899),
    (date(2026, NOV, 16), "CHILDRENS HOSPICE|DONATION", -1200),
    (date(2026, NOV, 21), "ANTHROPIC|CLAUDE.AI", -1800),
    (date(2026, NOV, 26), "NARWHAL APP|SUBSCRIPTION", -399),
    (date(2026, NOV, 28), "NABU CASA INC|HOME ASSISTANT", -658),
    (date(2026, NOV, 30), "GOOGLE *GOOGLE ONE|G.CO/HELPPAY", -799),
]

PERSON_2_RBS: list[Row] = [
    (date(2026, OCT, 28), "NHS SCOTLAND SALARY", 152375),
    (date(2026, OCT, 30), "TO JOINT BILLING", -70000),
    (date(2026, NOV, 2), "BLACK HORSE LTD 0042", -37186),
    (date(2026, NOV, 5), "SOC OF ENGINEERS", -1895),
    (date(2026, NOV, 8), "GAP INS DIRECT 88123", -1299),
    (date(2026, NOV, 12), "BP EXAMPLETOWN", -4500),
]

PERSON_1_MONZO: list[Row] = [
    (date(2026, OCT, 30), "EXAMPLE LTD SALARY", 421580),
    (date(2026, OCT, 30), "Joint Billing", -267000),
    (date(2026, OCT, 30), "Person 1 Starling", -13500),
    (date(2026, OCT, 30), "Savings", -40000),
    (date(2026, NOV, 6), "Costa Coffee", -385),
    (date(2026, NOV, 6), "Costa Coffee", -385),
]


def _pounds(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    return f"{sign}{abs(pence) // 100}.{abs(pence) % 100:02d}"


def starling_csv(rows: list[Row], categories: dict[str, str] | None = None) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "Date",
            "Counter Party",
            "Reference",
            "Type",
            "Amount (GBP)",
            "Balance (GBP)",
            "Spending Category",
            "Notes",
        ]
    )
    balance = 50000
    for when, text, pence in rows:
        who, _, ref = text.partition("|")
        balance += pence
        category = (categories or {}).get(who.split()[0], "GENERAL")
        kind = (
            "DIRECT DEBIT"
            if pence < 0 and "CARD" not in ref
            else "CARD"
            if pence < 0
            else "FASTER PAYMENT"
        )
        w.writerow(
            [
                when.strftime("%d/%m/%Y"),
                who,
                ref,
                kind,
                _pounds(pence),
                _pounds(balance),
                category,
                "",
            ]
        )
    return out.getvalue()


def rbs_csv(rows: list[Row]) -> str:
    out = io.StringIO()
    out.write("\n")  # RBS exports start with a blank line
    w = csv.writer(out)
    w.writerow(
        ["Date", "Type", "Description", "Value", "Balance", "Account Name", "Account Number"]
    )
    balance = 20000
    for when, text, pence in rows:
        balance += pence
        kind = "D/D" if pence < 0 and "TO " not in text else "BAC"
        w.writerow(
            [
                when.strftime("%d %b %Y"),
                kind,
                f"'{text}" if text.startswith("=") else text,
                _pounds(pence),
                _pounds(balance),
                "MRS P TWO",
                "'831512-12345678",
            ]
        )
    return out.getvalue()


def monzo_csv(rows: list[Row]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "Transaction ID",
            "Date",
            "Time",
            "Type",
            "Name",
            "Emoji",
            "Category",
            "Amount",
            "Currency",
            "Local amount",
            "Local currency",
            "Notes and #tags",
            "Address",
            "Receipt",
            "Description",
            "Category split",
            "Money Out",
            "Money In",
        ]
    )
    for n, (when, text, pence) in enumerate(rows):
        w.writerow(
            [
                f"tx_0000Synthetic{n:04d}",
                when.strftime("%d/%m/%Y"),
                "12:00:00",
                "Faster payment" if abs(pence) > 10000 else "Card payment",
                text,
                "",
                "Transfers" if abs(pence) > 10000 else "Eating out",
                _pounds(pence),
                "GBP",
                _pounds(pence),
                "GBP",
                "",
                "",
                "",
                text,
                "",
                _pounds(pence) if pence < 0 else "",
                _pounds(pence) if pence > 0 else "",
            ]
        )
    return out.getvalue()


GROCERY_CATEGORIES = {"TESCO": "GROCERIES", "ALDI": "GROCERIES", "SAINSBURYS": "GROCERIES"}


def month_files() -> dict[str, tuple[str, str]]:
    """account key -> (format, csv text) for the whole synthetic month."""
    return {
        "joint_billing": ("starling", starling_csv(JOINT_BILLING, GROCERY_CATEGORIES)),
        "person_1_starling": ("starling", starling_csv(PERSON_1_STARLING)),
        "person_2_rbs": ("rbs", rbs_csv(PERSON_2_RBS)),
        "person_1_monzo": ("monzo", monzo_csv(PERSON_1_MONZO)),
    }


# ---- seven months of history, for recurring detection (Phase 5) ----------------------------

JITTER = (0, 1, -1, 2, 0, -2, 1)  # a payment drifts a day or two, as real ones do


def _month(start: date, n: int) -> date:
    index = start.year * 12 + start.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def _on(first: date, day: int, n: int) -> date:
    return first.replace(day=max(1, min(28, day + JITTER[n % len(JITTER)])))


# (bank text, day of month, pence by month index 0..6 as a function). May to November 2026.
# The §4.6 price changes appear part-way through, as they would in a bank's history; EE
# goes up after the budget starts (1 October), so matching proposes that one.
HISTORY_JOINT: list[tuple[str, int, object]] = [
    ("SKY DIGITAL|SKY0123456", 1, lambda m: 9800 if m < 3 else 10475),
    ("TV LICENCE MBP|TVL 4421", 1, lambda m: 1500),
    ("EXAMPLETOWN COUNCIL|CTAX 99102", 1, lambda m: 24350 if m < 2 else 25980),
    ("NORTHFIELD B S|MORTGAGE 776512", 1, lambda m: 119246),
    ("DOORSTEP MILK|ROUND 12", 3, lambda m: 3200 if m < 5 else None),  # stopped in October
    ("GIG CLUB LTD|MEMBERSHIP", 5, lambda m: 3800),
    ("1ST CENTRAL INS|POLICY 88213", 6, lambda m: 3912 if m < 4 else 4127),
    ("PAYPAL *SPOTIFY|35314369001", 8, lambda m: 1699 if m < 3 else 1799),
    ("PINK RIBBON FOUNDATION|DONATION", 9, lambda m: 1000),
    ("DOMESTIC & GENERAL|PLAN 4471", 10, lambda m: 1299),  # marked to cancel, still taken
    ("ROVER PHYSIO|SESSIONS", 10, lambda m: 12500),
    ("VODAFONE LIMITED|7741203", 12, lambda m: 4900 if m < 2 else 2612),
    ("HILL AND WARD|FACTOR 2211", 13, lambda m: 2100),
    ("EE LIMITED|07700900123", 14, lambda m: 4012 if m < 6 else 4175),
    ("AGEAS INSURANCE|HOME 55121", 15, lambda m: 3368),
    ("AVIVA LIFE|POLICY A", 18, lambda m: 4236),
    ("AVIVA LIFE|POLICY B", 18, lambda m: 3678),
    ("NETFLIX.COM|NETFLIX.COM", 20, lambda m: 1099),  # not in the budget
    ("A REID WINDOWS|WINDOWS", 20, lambda m: 1200),
    ("GLOW CLINIC|CARD", 22, lambda m: 2900),
    ("ULTIMATE PET PARTNE|DOG 1", 24, lambda m: 4827 if m < 3 else 6512),
    ("ULTIMATE PET PARTNE|DOG 2", 24, lambda m: 4828 if m < 3 else 7362),
    (
        "OCTOPUS ENERGY|A-3F21C9",
        25,
        lambda m: (16244, 18200, 17500, 16900, 18800, 20100, 16244)[m],
    ),  # varies with use
]
HISTORY_STARLING: list[tuple[str, int, object]] = [
    ("PATREON* MEMBERSHIP|PATREON", 1, lambda m: 350),
    ("GOOGLE *WORKSPACE|G.CO/HELPPAY", 2, lambda m: 1299),
    ("SIMPLISAFE|MONITORING", 4, lambda m: 2799),
    ("PREMIER SPORTS|SUBSCRIPTION", 7, lambda m: 1599),
    ("CHILDRENS HOSPICE|DONATION", 16, lambda m: 1200),
    ("ANTHROPIC|CLAUDE.AI", 21, lambda m: 1800),
    ("NABU CASA INC|HOME ASSISTANT", 26, lambda m: 658),
]
HISTORY_RBS: list[tuple[str, int, object]] = [
    ("BLACK HORSE LTD 0042", 2, lambda m: 37186),  # the car, under the lender's name
    ("SOC OF ENGINEERS", 5, lambda m: 1895),
    ("GAP INS DIRECT 88123", 8, lambda m: 1299),
]
HISTORY_START = date(2026, 5, 1)
HISTORY_TODAY = date(2026, 11, 28)


def _history_rows(payees: list[tuple[str, int, object]]) -> list[Row]:
    rows: list[Row] = []
    for n in range(7):
        first = _month(HISTORY_START, n)
        for text, day, amount in payees:
            pence = amount(n)  # type: ignore[operator]
            if pence is not None:
                rows.append((_on(first, day, n), text, -pence))
    return sorted(rows)


def history_files() -> dict[str, tuple[str, str]]:
    """Seven months (May to November 2026) from three accounts, in their banks' formats."""
    joint = _history_rows(HISTORY_JOINT)
    # The weekly shop: every Saturday, but never the same amount twice. Not a bill.
    day = date(2026, 5, 2)
    n = 0
    while day <= HISTORY_TODAY:
        joint.append((day, "TESCO STORES 3021|CARD 4421", -(6000 + (n * 3700) % 7000)))
        day = date.fromordinal(day.toordinal() + 7)
        n += 1
    return {
        "joint_billing": ("starling", starling_csv(sorted(joint), GROCERY_CATEGORIES)),
        "person_1_starling": ("starling", starling_csv(_history_rows(HISTORY_STARLING))),
        "person_2_rbs": ("rbs", rbs_csv(_history_rows(HISTORY_RBS))),
    }
