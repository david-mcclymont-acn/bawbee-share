"""The budget engine (PLAN.md §3.3, §7).

Pure and side-effect free: takes a resolved budget, returns transfers, flows and surpluses.
Everything stays an exact Fraction of a penny until the spreadsheet itself rounds (the £5
ceilings on transfers); callers round to whole pence only for display.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from app.engine.money import Frequency, ceil_to_step, monthly_equivalent, round_pence

SplitBasis = Literal["gross", "take_home", "surplus", "fixed_pct"]
SPLIT_BASES: tuple[SplitBasis, ...] = ("gross", "take_home", "surplus", "fixed_pct")
ExtraLinesMode = Literal["on_top_of_share", "exclusive"]
LineKind = Literal["bill", "allocation", "transfer"]
AccountRole = Literal[
    "salary_hub", "subscriptions", "joint_bills", "savings", "investment", "dormant"
]


@dataclass(frozen=True)
class Account:
    key: str
    role: AccountRole
    owner: str | None = None  # person key; None for the joint account


@dataclass(frozen=True)
class JointShareRule:
    """This person's calculated share of the joint account's outgoings."""

    person: str
    # Joint lines this person pays in full (PLAN.md §3.3 "extra_lines_for_person_1").
    extra_line_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverAccountRule:
    """Top up `account` by the sum of its lines, rounded up (the Starling top-up)."""

    account: str
    rounding_step_pence: int = 500
    buffer_pence: int = 0


TransferRule = JointShareRule | CoverAccountRule


@dataclass(frozen=True)
class Line:
    key: str
    name: str
    kind: LineKind
    account: str
    amount_pence: int | None = None
    frequency: Frequency = "monthly"
    to_account: str | None = None
    rule: TransferRule | None = None
    # A typed-in transfer amount that replaces the suggested one (PLAN.md §3.3).
    actual_pence: int | None = None
    day_of_month: int | None = None
    provider: str | None = None

    @property
    def monthly(self) -> Fraction:
        return monthly_equivalent(self.amount_pence or 0, self.frequency)


@dataclass(frozen=True)
class Income:
    person: str
    net_monthly_pence: int
    gross_annual_pence: int = 0
    benefits_allowance_pence: int = 0


@dataclass(frozen=True)
class Settings:
    split_basis: SplitBasis = "take_home"
    # Only for split_basis "fixed_pct": each person's share, summing to 1.
    fixed_shares: Mapping[str, Fraction] = field(default_factory=dict)
    rounding_step_pence: int = 500
    buffer_pence: int = 500
    extra_lines_mode: ExtraLinesMode = "on_top_of_share"


@dataclass(frozen=True)
class Budget:
    accounts: tuple[Account, ...]
    lines: tuple[Line, ...]
    incomes: tuple[Income, ...]
    settings: Settings = Settings()


@dataclass(frozen=True)
class Transfer:
    line_key: str
    name: str
    from_account: str
    to_account: str
    rule: Literal["joint_share", "cover_account_lines", "fixed"]
    suggested_pence: int
    actual_pence: int | None = None

    @property
    def effective_pence(self) -> int:
        return self.suggested_pence if self.actual_pence is None else self.actual_pence

    @property
    def difference_pence(self) -> int:
        """Actual minus suggested; 0 when there is no manual amount."""
        return self.effective_pence - self.suggested_pence


@dataclass(frozen=True)
class AccountFlow:
    account: str
    inflow: Fraction
    outflow: Fraction


@dataclass(frozen=True)
class Warning:
    code: str
    message: str
    subject: str | None = None


@dataclass(frozen=True)
class Result:
    joint_account: str
    joint_total: Fraction
    split_basis: SplitBasis
    shares: dict[str, Fraction]
    transfers: tuple[Transfer, ...]
    flows: dict[str, AccountFlow]
    personal_surplus: dict[str, Fraction]
    joint_surplus: Fraction
    household_surplus: Fraction
    # Suggested joint transfer per person under every split basis that can be computed.
    split_options: dict[SplitBasis, dict[str, int]]
    warnings: tuple[Warning, ...]

    def transfer(self, line_key: str) -> Transfer:
        return next(t for t in self.transfers if t.line_key == line_key)


class BudgetError(ValueError):
    """The budget cannot be calculated (e.g. no joint account)."""


def calculate(budget: Budget) -> Result:
    accounts = {a.key: a for a in budget.accounts}
    joint = _single(a.key for a in budget.accounts if a.role == "joint_bills")
    hubs = {a.owner: a.key for a in budget.accounts if a.role == "salary_hub" and a.owner}
    incomes = {i.person: i for i in budget.incomes}
    settings = budget.settings
    warnings: list[Warning] = []

    for line in budget.lines:
        for key in (line.account, line.to_account):
            if key is not None and key not in accounts:
                raise BudgetError(f"Line {line.name!r} refers to unknown account {key!r}")

    # 1. Top-ups that cover another account's lines (they feed the surplus split basis).
    transfers: dict[str, Transfer] = {}
    for line in budget.lines:
        if isinstance(line.rule, CoverAccountRule):
            covered = sum(
                (other.monthly for other in budget.lines if other.account == line.rule.account),
                Fraction(0),
            )
            suggested = ceil_to_step(covered, line.rule.rounding_step_pence)
            transfers[line.key] = _transfer(
                line, "cover_account_lines", suggested + line.rule.buffer_pence
            )
        elif line.kind == "transfer" and line.rule is None:
            transfers[line.key] = _transfer(line, "fixed", round_pence(line.monthly))

    # 2. The joint account's outgoings, and each person's share of them.
    joint_total = sum((ln.monthly for ln in budget.lines if ln.account == joint), Fraction(0))
    share_lines = [(ln, ln.rule) for ln in budget.lines if isinstance(ln.rule, JointShareRule)]
    people = [rule.person for _, rule in share_lines]

    def pre_joint_surplus(person: str) -> Fraction:
        hub = hubs.get(person)
        income = incomes.get(person)
        net = Fraction(income.net_monthly_pence if income else 0)
        out = sum(
            (
                Fraction(transfers[ln.key].effective_pence) if ln.key in transfers else ln.monthly
                for ln in budget.lines
                if ln.account == hub and not isinstance(ln.rule, JointShareRule)
            ),
            Fraction(0),
        )
        return net - out

    def weights(basis: SplitBasis) -> dict[str, Fraction] | None:
        if basis == "fixed_pct":
            if not settings.fixed_shares:
                return None
            return {p: Fraction(settings.fixed_shares.get(p, 0)) for p in people}
        raw: dict[str, Fraction] = {}
        for p in people:
            inc = incomes.get(p)
            if basis == "gross":
                raw[p] = Fraction(
                    (inc.gross_annual_pence + inc.benefits_allowance_pence) if inc else 0
                )
            elif basis == "take_home":
                raw[p] = Fraction(inc.net_monthly_pence if inc else 0)
            else:
                raw[p] = max(pre_joint_surplus(p), Fraction(0))
        total = sum(raw.values(), Fraction(0))
        if total <= 0:
            return None
        return {p: w / total for p, w in raw.items()}

    def joint_suggestions(shares: Mapping[str, Fraction]) -> dict[str, int]:
        extras = {
            rule.person: sum(
                (
                    other.monthly
                    for other in budget.lines
                    if other.key in rule.extra_line_keys and other.account == joint
                ),
                Fraction(0),
            )
            for _, rule in share_lines
        }
        pool = joint_total
        if settings.extra_lines_mode == "exclusive":
            pool -= sum(extras.values(), Fraction(0))
        return {
            p: ceil_to_step(
                pool * shares[p] + extras.get(p, Fraction(0)), settings.rounding_step_pence
            )
            + settings.buffer_pence
            for p in shares
        }

    split_options: dict[SplitBasis, dict[str, int]] = {}
    for basis in SPLIT_BASES:
        w = weights(basis)
        if w is not None:
            split_options[basis] = joint_suggestions(w)

    shares = weights(settings.split_basis)
    if shares is None and people:
        warnings.append(
            Warning(
                "split_basis_unavailable",
                f"Can't split the joint account by {settings.split_basis}: no positive basis "
                "for anyone, so it's split equally.",
            )
        )
        shares = {p: Fraction(1, len(people)) for p in people}
    shares = shares or {}
    suggested_joint = joint_suggestions(shares) if shares else {}
    for ln, rule in share_lines:
        transfers[ln.key] = _transfer(ln, "joint_share", suggested_joint[rule.person])

    # 3. Money flows per account, with every transfer at its effective amount.
    inflow = {key: Fraction(0) for key in accounts}
    outflow = {key: Fraction(0) for key in accounts}
    for ln in budget.lines:
        value = Fraction(transfers[ln.key].effective_pence) if ln.key in transfers else ln.monthly
        outflow[ln.account] += value
        if ln.to_account is not None:
            inflow[ln.to_account] += value
    flows = {k: AccountFlow(k, inflow[k], outflow[k]) for k in accounts}

    # 4. Surpluses. Household always includes the joint surplus (PLAN.md §4.2).
    personal: dict[str, Fraction] = {}
    for person, hub in hubs.items():
        income = incomes.get(person)
        net = Fraction(income.net_monthly_pence if income else 0)
        personal[person] = net + inflow[hub] - outflow[hub]
        if personal[person] < 0:
            warnings.append(
                Warning(
                    "personal_shortfall",
                    f"{person.replace('_', ' ').title()}'s account is "
                    f"£{-round_pence(personal[person]) / 100:,.2f} short each month.",
                    person,
                )
            )
    joint_surplus = inflow[joint] - outflow[joint]
    if joint_surplus < 0:
        warnings.append(
            Warning(
                "joint_shortfall",
                f"Transfers into the joint account are £{-round_pence(joint_surplus) / 100:,.2f}"
                " short of its outgoings.",
                joint,
            )
        )
    household = sum(personal.values(), Fraction(0)) + joint_surplus

    for t in transfers.values():
        if t.actual_pence is not None and t.difference_pence != 0:
            warnings.append(
                Warning(
                    "manual_transfer",
                    f"{t.name} is set to £{t.effective_pence / 100:,.2f}, "
                    f"£{abs(t.difference_pence) / 100:,.2f} "
                    f"{'more' if t.difference_pence > 0 else 'less'} than suggested.",
                    t.line_key,
                )
            )

    return Result(
        joint_account=joint,
        joint_total=joint_total,
        split_basis=settings.split_basis,
        shares=shares,
        transfers=tuple(transfers[ln.key] for ln in budget.lines if ln.key in transfers),
        flows=flows,
        personal_surplus=personal,
        joint_surplus=joint_surplus,
        household_surplus=household,
        split_options=split_options,
        warnings=tuple(warnings),
    )


def _transfer(
    line: Line, rule: Literal["joint_share", "cover_account_lines", "fixed"], suggested: int
) -> Transfer:
    if line.to_account is None:
        raise BudgetError(f"Transfer {line.name!r} has no destination account")
    return Transfer(
        line_key=line.key,
        name=line.name,
        from_account=line.account,
        to_account=line.to_account,
        rule=rule,
        suggested_pence=suggested,
        actual_pence=line.actual_pence,
    )


def _single(keys: Iterable[str]) -> str:
    found = list(keys)
    if len(found) != 1:
        raise BudgetError(f"Expected exactly one joint bills account, found {len(found)}")
    return found[0]
