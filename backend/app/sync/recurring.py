"""Recurring payments (PLAN.md §8.4 step 2): payees paid on a steady rhythm.

A payee counts as recurring with at least three payments from one account, on a steady
cadence (weekly, monthly ±3 days, quarterly, annual ±7 days), at a steady amount: within
15% throughout, or with a single step (a price change) and steady either side of it.

Payees whose payments are already matched to a live bill are "known": that's the check
that detection finds the bills we budget for, at the right cadence. The rest become a
"new recurring payment" in the inbox, unless someone has already said to ignore it.
"""

import itertools
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.models import Account, BankTransaction, InboxItem, RecurringCandidate
from app.domain.versions import ResolvedLine
from app.sync import matching

MIN_PAYMENTS = 3
AMOUNT_TOLERANCE = 0.15
# A payee seen more recently than this is current; older rhythms have stopped.
STALE_AFTER = {"weekly": 21, "monthly": 45, "quarterly": 120, "annual": 400}

# cadence: (typical gap in days, allowed drift either side)
CADENCES = {
    "weekly": (7, 1),
    "monthly": (30.4, 4),  # calendar months run 28-31 days, plus ±3 days of drift
    "quarterly": (91.3, 7),
    "annual": (365.25, 7),
}


@dataclass(frozen=True)
class Rhythm:
    cadence: str
    typical_pence: int  # positive: what's paid each time now
    day_of_month: int | None
    occurrences: int
    first_seen: date
    last_seen: date
    confidence: float
    previous_pence: int | None = None  # before a price change seen in the history
    changed_on: date | None = None


def _cadence(gaps: list[int]) -> tuple[str, float] | None:
    """The cadence the gaps fit, and the share of gaps that fit it."""
    best: tuple[str, float] | None = None
    for name, (typical, drift) in CADENCES.items():
        fits = sum(1 for g in gaps if abs(g - typical) <= drift)
        share = fits / len(gaps)
        if share >= 0.8 and (best is None or share > best[1]):
            best = (name, share)
    return best


def _step(amounts: list[int]) -> int | None:
    """Where the price changed (the first payment at the new price), when the payments are
    one exact price and then another, as fixed bills are. None if it never moved."""
    last = amounts[-1]
    cut = len(amounts)
    while cut > 0 and amounts[cut - 1] == last:
        cut -= 1
    return cut if cut > 0 and len(set(amounts[:cut])) == 1 else None


def _steady(amounts: list[int]) -> tuple[bool, int, int | None]:
    """Steady amounts (or one price change): steady, what's paid now, and where it changed."""

    def close(values: list[int]) -> bool:
        middle = median(values)
        return all(abs(v - middle) <= middle * AMOUNT_TOLERANCE for v in values)

    if close(amounts):
        return True, amounts[-1], _step(amounts)
    # A bigger step: steady before, steady after (pet insurance went up 72%).
    for cut in range(1, len(amounts)):
        if close(amounts[:cut]) and close(amounts[cut:]):
            return True, amounts[-1], _step(amounts) or cut
    return False, amounts[-1], None


def rhythm(payments: list[tuple[date, int]]) -> Rhythm | None:
    """The rhythm of one payee's payments from one account, if they have one."""
    # Several payments to one payee on one day are one bill paid in parts (two pet policies).
    by_day: dict[date, int] = defaultdict(int)
    for paid_on, pence in payments:
        by_day[paid_on] += pence
    payments = sorted(by_day.items())
    if len(payments) < MIN_PAYMENTS:
        return None
    dates = [d for d, _ in payments]
    gaps = [(b - a).days for a, b in itertools.pairwise(dates)]
    found = _cadence(gaps)
    if found is None:
        return None
    cadence, share = found
    steady, now, step = _steady([p for _, p in payments])
    if not steady:
        return None
    days = [d.day for d in dates]
    day = max(set(days), key=days.count) if cadence != "weekly" else None
    return Rhythm(
        cadence=cadence,
        typical_pence=now,
        day_of_month=day,
        occurrences=len(payments),
        first_seen=dates[0],
        last_seen=dates[-1],
        confidence=round(share, 2),
        previous_pence=payments[step - 1][1] if step else None,
        changed_on=dates[step] if step else None,
    )


@dataclass
class DetectionSummary:
    known: int = 0
    proposed: int = 0
    candidates: int = 0


def detect(
    session: Session, today: date, skip: set[tuple[int, str]] | None = None
) -> DetectionSummary:
    """Find the rhythms in every account's money out and record them; propose new ones.

    `skip` is (account, payee) pairs not to propose: e.g. a bill marked to cancel that's
    still being paid, which has its own inbox item."""
    summary = DetectionSummary()
    groups: dict[tuple[int, str], list[BankTransaction]] = defaultdict(list)
    for txn in session.scalars(
        select(BankTransaction).where(
            BankTransaction.amount_pence < 0,
            BankTransaction.status == "booked",
            BankTransaction.transfer_pair_id.is_(None),
        )
    ):
        if txn.match_method == "transfer" or txn.bank_category == "pot_transfer":
            continue  # our own money moving between accounts (or into a Monzo pot)
        groups[(txn.account_id, txn.merchant_normalised)].append(txn)

    accounts = {a.id: a for a in session.scalars(select(Account))}
    existing = {
        (c.account_id, c.merchant_normalised): c
        for c in session.scalars(select(RecurringCandidate))
    }
    for (account_id, merchant), txns in groups.items():
        found = rhythm([(t.booked_date, -t.amount_pence) for t in txns])
        if found is None:
            continue
        lines = [t.matched_line_id for t in txns if t.matched_line_id]
        line_id = max(set(lines), key=lines.count) if lines else None
        current = (today - found.last_seen).days <= STALE_AFTER[found.cadence]
        candidate = existing.get((account_id, merchant))
        if candidate is None:
            candidate = RecurringCandidate(
                account_id=account_id, merchant_normalised=merchant, status="new"
            )
            session.add(candidate)
        candidate.cadence = found.cadence
        candidate.typical_amount_pence = found.typical_pence
        candidate.previous_amount_pence = found.previous_pence
        candidate.price_changed_on = found.changed_on
        candidate.day_of_month = found.day_of_month
        candidate.occurrences = found.occurrences
        candidate.first_seen = found.first_seen
        candidate.last_seen = found.last_seen
        candidate.confidence = found.confidence
        candidate.line_id = line_id
        candidate.updated_at = utcnow()
        summary.candidates += 1
        if line_id is not None:
            if candidate.status in ("new", "known"):
                candidate.status = "known"
            summary.known += 1
            continue
        if candidate.status == "known":
            candidate.status = "new"  # its line went; it's a stranger again
        session.flush()
        if candidate.status == "new" and current and (account_id, merchant) not in (skip or set()):
            summary.proposed += _propose(session, candidate, accounts[account_id], today)
    session.flush()
    return summary


def _likely_line(
    session: Session, candidate: RecurringCandidate, account: Account, today: date
) -> ResolvedLine | None:
    """A live bill on the same account at this price that nothing pays yet: probably this
    payee under another name (Car, paid to Black Horse)."""
    claimed = {
        c.line_id for c in session.scalars(select(RecurringCandidate)) if c.line_id is not None
    }
    near = [
        ln
        for ln in matching.bill_lines(session, today)
        if ln.account == account.key
        and ln.line_id not in claimed
        and ln.amount_pence
        and abs(ln.amount_pence - candidate.typical_amount_pence) <= ln.amount_pence * 0.02
    ]
    return near[0] if len(near) == 1 else None


def _propose(session: Session, candidate: RecurringCandidate, account: Account, today: date) -> int:
    key = f"new_recurring:{candidate.id}"
    if session.scalars(select(InboxItem).where(InboxItem.dedupe_key == key)).first():
        return 0
    likely = _likely_line(session, candidate, account, today)
    session.add(
        InboxItem(
            type="new_recurring",
            dedupe_key=key,
            payload_json={
                "candidate_id": candidate.id,
                "merchant": candidate.merchant_normalised,
                "account": account.key,
                "cadence": candidate.cadence,
                "amount_pence": candidate.typical_amount_pence,
                "day_of_month": candidate.day_of_month,
                "occurrences": candidate.occurrences,
                "first_seen": candidate.first_seen.isoformat(),
                "last_seen": candidate.last_seen.isoformat(),
                "previous_amount_pence": candidate.previous_amount_pence,
                "price_changed_on": candidate.price_changed_on.isoformat()
                if candidate.price_changed_on
                else None,
                "likely_line_id": likely.line_id if likely else None,
                "likely_label": likely.label if likely else None,
            },
        )
    )
    session.flush()
    return 1
