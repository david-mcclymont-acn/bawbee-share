"""Pure engine behaviour beyond the golden tests."""

from fractions import Fraction

import pytest

from app.engine.budget import (
    Account,
    Budget,
    BudgetError,
    CoverAccountRule,
    Income,
    JointShareRule,
    Line,
    Settings,
    calculate,
)
from app.engine.money import ceil_to_step, monthly_equivalent, round_pence

ACCOUNTS = (
    Account("d_hub", "salary_hub", "d"),
    Account("a_hub", "salary_hub", "a"),
    Account("subs", "subscriptions", "d"),
    Account("joint", "joint_bills"),
)
INCOMES = (Income("d", 300000, 6000000), Income("a", 100000, 2000000))


def _budget(*extra: Line, **settings: object) -> Budget:
    lines = (
        Line("t_d", "To Joint", "transfer", "d_hub", to_account="joint", rule=JointShareRule("d")),
        Line("t_a", "To Joint", "transfer", "a_hub", to_account="joint", rule=JointShareRule("a")),
        Line("mortgage", "Mortgage", "bill", "joint", amount_pence=100000),
        *extra,
    )
    return Budget(ACCOUNTS, lines, INCOMES, Settings(**settings))  # type: ignore[arg-type]


def test_ceil_to_step_is_exact() -> None:
    assert ceil_to_step(500, 500) == 500
    assert ceil_to_step(Fraction(1001, 2), 500) == 1000
    assert ceil_to_step(Fraction(-1, 3), 500) == 0
    assert ceil_to_step(0, 500) == 0
    with pytest.raises(ValueError, match="positive"):
        ceil_to_step(1, 0)


def test_round_pence_halves_away_from_zero() -> None:
    assert round_pence(Fraction(5, 2)) == 3
    assert round_pence(Fraction(-5, 2)) == -3
    assert round_pence(Fraction(4999, 12)) == 417


def test_monthly_equivalents() -> None:
    assert monthly_equivalent(1200, "annual") == 100
    assert monthly_equivalent(300, "quarterly") == 100
    assert monthly_equivalent(1200, "weekly") == 5200


def test_take_home_split() -> None:
    result = calculate(_budget())
    assert result.shares == {"d": Fraction(3, 4), "a": Fraction(1, 4)}
    assert result.transfer("t_d").suggested_pence == 75000 + 500
    assert result.transfer("t_a").suggested_pence == 25000 + 500


def test_fixed_percentage_split() -> None:
    result = calculate(
        _budget(split_basis="fixed_pct", fixed_shares={"d": Fraction(3, 5), "a": Fraction(2, 5)})
    )
    assert result.transfer("t_d").suggested_pence == 60500
    assert result.split_options["fixed_pct"] == {"d": 60500, "a": 40500}


def test_extra_lines_on_top_of_share_versus_exclusive() -> None:
    gym = Line("gym", "Gym", "bill", "joint", amount_pence=1000)
    rules = (
        Line(
            "t_d",
            "To Joint",
            "transfer",
            "d_hub",
            to_account="joint",
            rule=JointShareRule("d", extra_line_keys=("gym",)),
        ),
        Line("t_a", "To Joint", "transfer", "a_hub", to_account="joint", rule=JointShareRule("a")),
        Line("mortgage", "Mortgage", "bill", "joint", amount_pence=100000),
        gym,
    )
    # The spreadsheet's way: d pays a share of the gym *and* all of it.
    on_top = calculate(Budget(ACCOUNTS, rules, INCOMES))
    assert (
        on_top.transfer("t_d").suggested_pence
        == ceil_to_step(Fraction(101000 * 3, 4) + 1000, 500) + 500
    )
    # Exclusive: the gym leaves the shared pool and d alone pays it.
    exclusive = calculate(Budget(ACCOUNTS, rules, INCOMES, Settings(extra_lines_mode="exclusive")))
    assert exclusive.transfer("t_d").suggested_pence == 75000 + 1000 + 500
    assert exclusive.transfer("t_a").suggested_pence == 25000 + 500


def test_cover_account_lines_rounds_up_without_buffer() -> None:
    result = calculate(
        _budget(
            Line(
                "t_s",
                "To Subs",
                "transfer",
                "d_hub",
                to_account="subs",
                rule=CoverAccountRule("subs"),
            ),
            Line("netflix", "Netflix", "bill", "subs", amount_pence=1099),
            Line("icloud", "iCloud", "bill", "subs", amount_pence=299),
        )
    )
    assert result.transfer("t_s").suggested_pence == 1500


def test_negative_surpluses_warn() -> None:
    poor = Budget(
        ACCOUNTS,
        (*_budget().lines, Line("rent", "Rent", "bill", "a_hub", amount_pence=200000)),
        INCOMES,
    )
    codes = [w.code for w in calculate(poor).warnings]
    assert "personal_shortfall" in codes


def test_unknown_account_is_an_error() -> None:
    with pytest.raises(BudgetError):
        calculate(_budget(Line("x", "X", "bill", "nowhere", amount_pence=1)))
