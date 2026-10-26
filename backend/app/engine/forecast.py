"""The 36-month forecast (PLAN.md §5.5): balances, surpluses, goals and warnings.

Pure. The caller resolves which budget version applies in each month (the forecast path)
and runs the budget engine for it; this module compounds those months into account
balances, adds dated one-offs, follows savings goals, and says where things go wrong.

A month's change to an account is what the budget says moves through it: a salary hub
moves by its owner's personal surplus (income included), the joint account by the joint
surplus, and any other account by money in less money out.
"""

from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from app.engine.money import round_pence
from app.engine.mortgage import add_months, months_between


@dataclass(frozen=True)
class OneOff:
    account: str
    pence: int  # money in positive, money out negative
    label: str


@dataclass(frozen=True)
class Month:
    month: date  # the 1st
    version_id: int
    version_name: str
    change: dict[str, Fraction]  # per account, a month
    personal_surplus: dict[str, Fraction]
    joint_surplus: Fraction
    household_surplus: Fraction
    one_offs: tuple[OneOff, ...] = ()
    events: tuple[str, ...] = ()
    # Savings goal allocations this month, by goal key (from the goal's budget line).
    allocations: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Goal:
    key: str
    name: str
    account: str
    target: int
    target_month: date  # the 1st of the month the money is needed
    saved: int
    repeats_yearly: bool = False  # a sinking fund: spent at the target, then saved again


@dataclass(frozen=True)
class Warning:
    month: date
    code: str  # "negative_balance" | "goal_behind"
    subject: str  # the account or goal key
    message: str


@dataclass(frozen=True)
class MonthOut:
    month: date
    version_id: int
    version_name: str
    personal_surplus: dict[str, int]
    joint_surplus: int
    household_surplus: int
    balances: dict[str, int]
    one_offs: tuple[OneOff, ...]
    events: tuple[str, ...]
    goals: dict[str, int]  # saved so far, by goal key, at the month's end


@dataclass(frozen=True)
class GoalOut:
    key: str
    name: str
    target: int
    target_month: date
    saved: int
    needed_monthly: int  # what's left ÷ months left, rounded up to the penny
    allocation: int  # what the budget puts aside in the first month
    on_track: bool
    short_by: int  # at the target month, if it stays as it is


@dataclass(frozen=True)
class Forecast:
    months: tuple[MonthOut, ...]
    goals: tuple[GoalOut, ...]
    warnings: tuple[Warning, ...]


def needed_monthly(target: int, saved: int, now: date, target_month: date) -> int:
    left = max(0, target - saved)
    months = max(1, months_between(now, target_month))
    whole, rest = divmod(left, months)
    return whole + (1 if rest else 0)


def run(
    months: list[Month],
    opening: dict[str, int],
    goals: tuple[Goal, ...] = (),
    names: dict[str, str] | None = None,
) -> Forecast:
    names = names or {}
    balances = {k: Fraction(v) for k, v in opening.items()}
    saved = {g.key: g.saved for g in goals}
    target_month = {g.key: g.target_month for g in goals}
    warned_negative: set[str] = set()
    warnings: list[Warning] = []
    out: list[MonthOut] = []
    shortfall: dict[str, int] = {}

    for m in months:
        for account, change in m.change.items():
            balances[account] = balances.get(account, Fraction(0)) + change
        for item in m.one_offs:
            balances[item.account] = balances.get(item.account, Fraction(0)) + item.pence
        for g in goals:
            saved[g.key] += m.allocations.get(g.key, 0)
            if m.month == target_month[g.key]:
                if saved[g.key] < g.target:
                    shortfall.setdefault(g.key, g.target - saved[g.key])
                    warnings.append(
                        Warning(
                            m.month,
                            "goal_behind",
                            g.key,
                            f"{g.name} is £{(g.target - saved[g.key]) / 100:,.2f} short of "
                            f"£{g.target / 100:,.2f} by {m.month:%B %Y}.",
                        )
                    )
                if g.repeats_yearly:
                    saved[g.key] = max(0, saved[g.key] - g.target)
                    target_month[g.key] = add_months(target_month[g.key], 12)
        for account, value in balances.items():
            if value < 0 and account not in warned_negative:
                warned_negative.add(account)
                warnings.append(
                    Warning(
                        m.month,
                        "negative_balance",
                        account,
                        f"{names.get(account, account)} goes £{-round_pence(value) / 100:,.2f} "
                        f"overdrawn in {m.month:%B %Y}.",
                    )
                )
        out.append(
            MonthOut(
                month=m.month,
                version_id=m.version_id,
                version_name=m.version_name,
                personal_surplus={p: round_pence(v) for p, v in m.personal_surplus.items()},
                joint_surplus=round_pence(m.joint_surplus),
                household_surplus=round_pence(m.household_surplus),
                balances={k: round_pence(v) for k, v in balances.items()},
                one_offs=m.one_offs,
                events=m.events,
                goals=dict(saved),
            )
        )

    first = months[0] if months else None
    goal_out = []
    for g in goals:
        now = first.month if first else g.target_month
        allocation = first.allocations.get(g.key, 0) if first else 0
        need = needed_monthly(g.target, g.saved, now, g.target_month)
        short = shortfall.get(g.key, 0)
        goal_out.append(
            GoalOut(
                key=g.key,
                name=g.name,
                target=g.target,
                target_month=g.target_month,
                saved=g.saved,
                needed_monthly=need,
                allocation=allocation,
                on_track=short == 0 and allocation >= need,
                short_by=short,
            )
        )
    return Forecast(tuple(out), tuple(goal_out), tuple(warnings))
