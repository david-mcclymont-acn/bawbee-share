"""Take-home pay from the tax-year configs, checked against the salary that lands (Phase 6).

Each income source's take-home is worked out from its gross, tax code, pension and salary
sacrifice (engine/tax.py, figures from config/tax/). The check compares the salary that
actually lands in the person's account, found around their payday, with the take-home the
budget uses: the version's own figure where one is set (Person 2's maternity pay), otherwise
the calculation. More than £5 apart raises an inbox item (sync/alerts.py).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import taxconfig
from app.domain.models import Account, BankTransaction, IncomeSource, Payslip, Person
from app.domain.payday import pay_dates
from app.domain.versions import live_version, resolve
from app.engine import tax
from app.engine.money import round_pence
from app.engine.mortgage import add_months

TOLERANCE_PENCE = 500  # £5 (Phase 6: within £5 of a real payslip)
WINDOW_DAYS = 4  # around the expected payday
MIN_SALARY_PENCE = 50_000


def latest_sources(session: Session) -> list[IncomeSource]:
    out = []
    for person in session.scalars(select(Person).order_by(Person.id)):
        source = session.scalars(
            select(IncomeSource)
            .where(IncomeSource.person_id == person.id)
            .order_by(IncomeSource.as_of_date.desc(), IncomeSource.id.desc())
        ).first()
        if source is not None:
            out.append(source)
    return out


def latest_payslip(session: Session, source: IncomeSource) -> Payslip | None:
    return session.scalars(
        select(Payslip)
        .where(Payslip.income_source_id == source.id)
        .order_by(Payslip.on.desc(), Payslip.id.desc())
    ).first()


def calculate(source: IncomeSource, on: date) -> tax.TakeHome:
    year = taxconfig.year_for(on)
    gross = Fraction(source.gross_annual_pence + (source.benefits_allowance_pence or 0), 100)
    return tax.take_home(
        gross,
        year,
        tax_code=source.tax_code,
        pension=source.pension_scheme,  # type: ignore[arg-type]
        pension_pct=Fraction(source.pension_pct or "0"),
        salary_sacrifice=Fraction(source.salary_sacrifice_pence or 0, 100),
    )


def monthly_pence(pounds_a_year: Fraction) -> int:
    return round_pence(pounds_a_year * 100 / 12)


@dataclass(frozen=True)
class Salary:
    on: date
    pence: int
    description: str


def salaries(session: Session, person: Person, today: date, months: int = 3) -> list[Salary]:
    """The salary payment that landed in each recent month, if one can be found."""
    hub = session.scalars(
        select(Account).where(Account.owner_person_id == person.id, Account.role == "salary_hub")
    ).first()
    if hub is None:
        return []
    source = session.scalars(
        select(IncomeSource).where(IncomeSource.person_id == person.id)
    ).first()
    employer_words = (
        [w.lower() for w in (source.employer or "").split() if len(w) > 3] if source else []
    )
    out: list[Salary] = []
    this_month = date(today.year, today.month, 1)
    for i in range(months, -1, -1):
        month = add_months(this_month, -i)
        payday = pay_dates(session, month).get(person.key)
        if payday is None or payday.expected > today:
            continue
        credits = session.scalars(
            select(BankTransaction).where(
                BankTransaction.account_id == hub.id,
                BankTransaction.amount_pence >= MIN_SALARY_PENCE,
                BankTransaction.status == "booked",
                BankTransaction.booked_date >= payday.expected - timedelta(days=WINDOW_DAYS),
                BankTransaction.booked_date <= payday.expected + timedelta(days=WINDOW_DAYS),
            )
        ).all()
        credits = [
            c for c in credits if c.match_method != "transfer" and c.transfer_pair_id is None
        ]
        if not credits:
            continue
        named = [
            c for c in credits if any(w in (c.description or "").lower() for w in employer_words)
        ]
        best = max(named or credits, key=lambda c: c.amount_pence)
        out.append(Salary(best.booked_date, best.amount_pence, best.description))
    return out


@dataclass(frozen=True)
class Check:
    person: str
    name: str
    year: str
    exact_year: bool  # False: a later tax year's config isn't in yet, so the latest is used
    calculated: tax.TakeHome
    calculated_monthly_pence: int
    budget_monthly_pence: int | None  # what the live budget uses
    budget_is_override: bool
    payslip_monthly_pence: int | None
    payslip_on: date | None
    salaries: list[Salary]

    @property
    def expected_pence(self) -> int:
        """What should land: the budget's figure, else the calculation."""
        if self.budget_monthly_pence is not None:
            return self.budget_monthly_pence
        return self.calculated_monthly_pence

    def mismatches(self) -> list[Salary]:
        return [s for s in self.salaries if abs(s.pence - self.expected_pence) > TOLERANCE_PENCE]


def checks(session: Session, today: date) -> list[Check]:
    live = resolve(session, live_version(session), today)
    budget = {i.person: i.net_monthly_pence for i in live.incomes}
    out = []
    for source in latest_sources(session):
        person = source.person
        calc = calculate(source, today)
        budget_net = budget.get(person.key)
        payslip = latest_payslip(session, source)
        out.append(
            Check(
                person=person.key,
                name=person.name,
                year=taxconfig.year_for(today).label,
                exact_year=taxconfig.is_exact(today),
                calculated=calc,
                calculated_monthly_pence=monthly_pence(calc.net),
                budget_monthly_pence=budget_net,
                budget_is_override=budget_net is not None,
                payslip_monthly_pence=payslip.net_monthly_pence if payslip else None,
                payslip_on=payslip.on if payslip else None,
                salaries=salaries(session, person, today),
            )
        )
    return out
