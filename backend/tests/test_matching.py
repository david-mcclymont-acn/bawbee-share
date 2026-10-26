"""A synthetic month of CSVs from every account, matched against the live budget.

Phase 3's acceptance check: a month of CSVs from all accounts matches at least 80% of the
bill lines automatically.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import payday
from app.domain.actuals import _unpaid, month_actuals
from app.domain.models import Account, BankTransaction, InboxItem
from app.domain.versions import live_version, resolve
from app.importers.seed import ImportResult
from app.sync import ingest, matching
from app.sync.ingest import ImportSummary
from app.sync.review import add_alias, match_manually
from tests import bank_fixtures as bf

NOV = date(2026, 11, 1)


def account(session: Session, key: str) -> Account:
    return session.scalars(select(Account).where(Account.key == key)).one()


def line_id(session: Session, name: str, account_key: str, provider: str | None = None) -> int:
    lines = resolve(session, live_version(session), NOV).lines
    return next(
        ln.line_id
        for ln in lines
        if ln.name == name
        and ln.account == account_key
        and (provider is None or ln.provider == provider)
    )


def import_month(session: Session) -> dict[str, ImportSummary]:
    out = {}
    for key, (fmt, text) in bf.month_files().items():
        out[key] = ingest.import_csv(session, account(session, key), text, f"{key}.csv", fmt, None)  # type: ignore[arg-type]
    session.commit()
    return out


def matched_names(session: Session) -> set[str]:
    by_id = {ln.line_id: ln.name for ln in resolve(session, live_version(session), NOV).lines}
    return {
        by_id[t.matched_line_id]
        for t in session.scalars(select(BankTransaction))
        if t.matched_line_id in by_id
    }


def test_a_month_of_csvs_matches_at_least_80_percent_of_bills(
    session: Session, imported: ImportResult
) -> None:
    import_month(session)
    matched, total = matching.bill_coverage(session, 2026, 11)
    assert total == 35
    assert matched / total >= 0.8
    # The ones it can't know: Black Horse is the car, the budget spells Narwhal "Narwahl",
    # Apple's text doesn't say iCloud, and Disney+ is paid inside the Sky bill.
    lines = [ln for ln in matching.bill_lines(session, NOV) if ln.frequency == "monthly"]
    missing = {ln.name for ln in lines} - matched_names(session)
    assert missing == {"Car", "Narwahl", "iCloud+", "Disney+"}
    assert matched == 31


def test_everyday_spending_is_left_unmatched(session: Session, imported: ImportResult) -> None:
    import_month(session)
    for name in ("TESCO STORES 3021", "ALDI 123", "PETS AT HOME", "BP EXAMPLETOWN"):
        txn = session.scalars(
            select(BankTransaction).where(
                (BankTransaction.counterparty == name) | (BankTransaction.description == name)
            )
        ).one()
        assert txn.matched_line_id is None, name


def test_composite_bills_take_one_payment_per_component(
    session: Session, imported: ImportResult
) -> None:
    import_month(session)
    aviva = line_id(session, "Life and Income Protection Insurance", "joint_billing")
    pets = line_id(session, "Pet Insurance", "joint_billing")
    paid = session.scalars(select(BankTransaction)).all()
    assert sorted(-t.amount_pence for t in paid if t.matched_line_id == aviva) == [3678, 4236]
    assert sorted(-t.amount_pence for t in paid if t.matched_line_id == pets) == [6512, 7362]
    # Both components at their budget price: no price change for either.
    assert not session.scalars(select(InboxItem).where(InboxItem.line_id.in_((aviva, pets)))).all()


def test_any_price_difference_raises_one_price_change(
    session: Session, imported: ImportResult
) -> None:
    summaries = import_month(session)
    items = session.scalars(select(InboxItem)).all()
    assert [(i.type, i.payload_json["label"]) for i in items] == [("price_change", "TV (Sky)")]
    sky = items[0]
    assert (
        sky.payload_json.items()
        >= {
            "old_pence": 10475,
            "new_pence": 10850,
            "seen_on": "2026-11-01",
        }.items()
    )
    assert summaries["joint_billing"].price_changes == 1

    # The same file again adds nothing: no new rows and no second proposal.
    fmt, text = bf.month_files()["joint_billing"]
    again = ingest.import_csv(session, account(session, "joint_billing"), text, None, fmt, None)  # type: ignore[arg-type]
    assert (again.new, again.duplicates, again.price_changes) == (0, len(bf.JOINT_BILLING), 0)
    assert len(session.scalars(select(InboxItem)).all()) == 1


def test_transfers_pair_up_and_tick_payday(session: Session, imported: ImportResult) -> None:
    summaries = import_month(session)
    txns = session.scalars(select(BankTransaction)).all()
    to_joint = next(t for t in txns if t.description == "Joint Billing" and t.amount_pence < 0)
    pair = session.get(BankTransaction, to_joint.transfer_pair_id)
    assert pair is not None
    assert pair.amount_pence == 267000
    assert account(session, "joint_billing").id == pair.account_id
    assert to_joint.matched_line_id == line_id(session, "To Joint Billing", "person_1_monzo")

    assert summaries["person_2_rbs"].transfers_matched == 1
    assert summaries["person_1_monzo"].transfers_matched == 3  # Joint, Starling, Savings
    run = payday.get_run(session, payday.month_key(2026, 10))
    assert run is not None
    ticked = [t for t in run.transfers if t.done_at is not None]
    assert len(ticked) == len(run.transfers) == 4  # Person 1's three, and Person 2's to Joint
    assert {t.done_via for t in ticked} == {"bank"}
    assert all(t.matched_transaction_id for t in ticked)
    # Salaries and the money arriving in Joint and Starling aren't bills.
    assert all(t.matched_line_id is None for t in txns if t.amount_pence > 0)


def test_overlapping_exports_import_only_the_new_rows(
    session: Session, imported: ImportResult
) -> None:
    acct = account(session, "person_2_rbs")
    first = ingest.import_csv(session, acct, bf.rbs_csv(bf.PERSON_2_RBS[:4]), None, "rbs", None)
    later = ingest.import_csv(session, acct, bf.rbs_csv(bf.PERSON_2_RBS), None, None, None)
    assert (first.new, later.new, later.duplicates) == (4, 2, 4)
    assert ingest.last_format_for(session, acct) == "rbs"
    preview = ingest.preview(session, acct, bf.rbs_csv(bf.PERSON_2_RBS), None)
    assert (preview.total, preview.new, preview.duplicates) == (6, 0, 6)


def test_a_manual_match_with_always_teaches_a_rule(
    session: Session, imported: ImportResult
) -> None:
    import_month(session)
    car = line_id(session, "Car", "person_2_rbs")
    black_horse = session.scalars(
        select(BankTransaction).where(BankTransaction.description.like("BLACK HORSE%"))
    ).one()
    result = match_manually(session, black_horse, None, line_id=car, category=None, always=True)
    assert black_horse.matched_line_id == car
    assert black_horse.match_method == "manual"
    assert result.rule_id is not None
    assert result.price_changes == 0

    # Next month's payment matches by the rule, and a new price is still noticed.
    dec = [(date(2026, 12, 2), "BLACK HORSE LTD 0042", -43000)]
    summary = ingest.import_csv(
        session, account(session, "person_2_rbs"), bf.rbs_csv(dec), None, "rbs", None
    )
    assert (summary.rule_matched, summary.price_changes) == (1, 1)


def test_an_alias_rematches_what_was_left_over(session: Session, imported: ImportResult) -> None:
    import_month(session)
    assert "Narwahl" not in matched_names(session)
    newly = add_alias(session, "NARWHAL", "Narwahl", None)
    assert newly == 1
    assert "Narwahl" in matched_names(session)
    assert matching.bill_coverage(session, 2026, 11) == (32, 35)


def test_actuals_compare_this_month_with_the_plan(session: Session, imported: ImportResult) -> None:
    import_month(session)
    actuals = {
        (a.account, a.label): a for a in month_actuals(session, live_version(session), 2026, 11)
    }
    # Sky took more than the budget says, the same rise the inbox proposes.
    sky = actuals[("joint_billing", "TV (Sky)")]
    assert (sky.planned_pence, sky.actual_pence, sky.status) == (10475, 10850, "over")
    assert sky.paid_on == [date(2026, 11, 1)]
    # Person 2's export stops on the 12th and the car has no set day: too soon to say.
    assert actuals[("person_2_rbs", "Car")].status == "no_data"
    groceries = actuals[("joint_billing", "Groceries")]
    assert (groceries.actual_pence, groceries.status) == (8412 + 4230 + 6000, "under")
    # No export from the savings account: nothing to say rather than "not seen".
    assert all(a.status == "no_data" for a in actuals.values() if a.account == "person_1_savings")


def test_a_bill_is_only_missing_inside_the_dates_an_export_covers() -> None:
    first, after = date(2026, 11, 1), date(2026, 12, 1)
    to_the_12th = (date(2026, 11, 2), date(2026, 11, 12))
    assert _unpaid(8, first, after, to_the_12th) == ("not_seen", date(2026, 11, 8))
    assert _unpaid(25, first, after, to_the_12th) == ("due", date(2026, 11, 25))
    late_start = (date(2026, 11, 20), date(2026, 11, 30))
    assert _unpaid(3, first, after, late_start) == ("no_data", date(2026, 11, 3))
    assert _unpaid(31, first, after, late_start) == ("not_seen", date(2026, 11, 30))
    assert _unpaid(None, first, after, to_the_12th) == ("no_data", None)
    assert _unpaid(None, first, after, (date(2026, 11, 2), date(2026, 11, 29))) == (
        "not_seen",
        None,
    )
