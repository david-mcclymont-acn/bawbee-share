"""Take-home pay for a Scottish taxpayer (PLAN.md §9, Phase 6).

Pure. Every rate and threshold comes from a `TaxYear`, which `domain/taxconfig.py` builds
from the cited files in `config/tax/`; nothing here is a tax constant. Figures are annual
and exact (`Fraction`); callers round to pence at the edge.

The method follows the household's old childcare calculator, which matched a real payslip:

- The personal allowance comes from the tax code the way PAYE does it (the code's number
  x 10 + 9, so S1257L allows £12,579), tapered by £1 for every £2 over £100,000.
- Scottish bands apply to taxable income above the allowance.
- An NHS Scotland pension is tiered on actual pensionable pay, not whole-time equivalent,
  and taken before tax under a net pay arrangement; NI is charged on the full pay.
- Salary sacrifice comes off before tax, NI and pension.
"""

import re
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal

PensionKind = Literal["none", "nhs_scotland", "workplace_pct"]


@dataclass(frozen=True)
class Band:
    label: str
    rate: Fraction  # percent
    up_to: Fraction | None  # taxable income above the allowance; None: no upper limit


@dataclass(frozen=True)
class Tier:
    up_to: Fraction | None  # pensionable pay; None: and above
    rate: Fraction  # percent


@dataclass(frozen=True)
class TaxYear:
    label: str  # "2026-27"
    starts: date
    ends: date
    personal_allowance: Fraction
    taper_threshold: Fraction
    taper_ratio: Fraction  # £1 of allowance lost for every £ratio over the threshold
    bands: tuple[Band, ...]
    ni_primary_threshold: Fraction
    ni_upper_limit: Fraction
    ni_main_rate: Fraction  # percent
    ni_upper_rate: Fraction  # percent
    nhs_tiers: tuple[Tier, ...]
    child_benefit_first_weekly: Fraction
    child_benefit_other_weekly: Fraction
    hicbc_threshold: Fraction
    hicbc_step: Fraction  # 1% of Child Benefit clawed back per this much over the threshold
    tfc_rate: Fraction  # percent the government adds
    tfc_cap_per_child: Fraction  # a year
    funded_hours_per_year: Fraction
    # NHS tiers for members tiered on last year's pay (take_home uses `nhs_tiers`: the table
    # for current pay and pay changes, as when hours change).
    nhs_tiers_continuing: tuple[Tier, ...] = ()


def allowance_from_code(code: str | None, default: Fraction) -> Fraction:
    """The personal allowance a PAYE tax code gives: its number x 10 + 9 (a K code takes away)."""
    match = re.search(r"(\d+)", code or "")
    if match is None:
        return default
    amount = Fraction(int(match.group(1)) * 10 + 9)
    return -amount if "K" in (code or "").upper() else amount


@dataclass(frozen=True)
class TaxSlice:
    label: str
    rate: Fraction
    amount: Fraction  # the tax charged in this band


def income_tax(
    taxable: Fraction, year: TaxYear, allowance: Fraction
) -> tuple[Fraction, tuple[TaxSlice, ...]]:
    pa = allowance
    if taxable > year.taper_threshold and pa > 0:
        pa = max(Fraction(0), pa - (taxable - year.taper_threshold) / year.taper_ratio)
    above = max(Fraction(0), taxable - pa)
    tax = Fraction(0)
    slices: list[TaxSlice] = []
    previous = Fraction(0)
    for band in year.bands:
        top = above if band.up_to is None else min(above, band.up_to)
        in_band = max(Fraction(0), top - previous)
        if in_band > 0:
            charged = in_band * band.rate / 100
            tax += charged
            slices.append(TaxSlice(band.label, band.rate, charged))
        if band.up_to is None or above <= band.up_to:
            break
        previous = band.up_to
    return tax, tuple(slices)


def national_insurance(
    pay: Fraction,
    year: TaxYear,
    primary_threshold: Fraction | None = None,
    upper_limit: Fraction | None = None,
) -> Fraction:
    low = year.ni_primary_threshold if primary_threshold is None else primary_threshold
    high = year.ni_upper_limit if upper_limit is None else upper_limit
    main = max(Fraction(0), min(pay, high) - low) * year.ni_main_rate / 100
    return main + max(Fraction(0), pay - high) * year.ni_upper_rate / 100


def nhs_pension_rate(pensionable: Fraction, year: TaxYear) -> Fraction:
    for tier in year.nhs_tiers:
        if tier.up_to is None or pensionable <= tier.up_to:
            return tier.rate
    return year.nhs_tiers[-1].rate


@dataclass(frozen=True)
class TakeHome:
    gross: Fraction  # contractual pay
    sacrifice: Fraction
    pay: Fraction  # gross less salary sacrifice: what tax, NI and pension are worked on
    pension_rate: Fraction  # percent
    pension: Fraction
    allowance: Fraction  # before any taper
    taxable: Fraction
    tax: Fraction
    tax_slices: tuple[TaxSlice, ...]
    ni: Fraction
    net: Fraction

    @property
    def net_monthly(self) -> Fraction:
        return self.net / 12


def take_home(
    gross: Fraction,
    year: TaxYear,
    *,
    tax_code: str | None = None,
    pension: PensionKind = "none",
    pension_pct: Fraction = Fraction(0),
    net_pay_arrangement: bool = True,
    salary_sacrifice: Fraction = Fraction(0),
    ni_primary_threshold: Fraction | None = None,
    ni_upper_limit: Fraction | None = None,
) -> TakeHome:
    pay = gross - salary_sacrifice
    if pension == "nhs_scotland":
        rate = nhs_pension_rate(pay, year)
    elif pension == "workplace_pct":
        rate = pension_pct
    else:
        rate = Fraction(0)
    contribution = pay * rate / 100
    taxable = pay - contribution if net_pay_arrangement else pay
    allowance = allowance_from_code(tax_code, year.personal_allowance)
    tax, slices = income_tax(taxable, year, allowance)
    ni = national_insurance(pay, year, ni_primary_threshold, ni_upper_limit)
    return TakeHome(
        gross=gross,
        sacrifice=salary_sacrifice,
        pay=pay,
        pension_rate=rate,
        pension=contribution,
        allowance=allowance,
        taxable=taxable,
        tax=tax,
        tax_slices=slices,
        ni=ni,
        net=pay - contribution - tax - ni,
    )


def solve_gross(
    target_net: Fraction, year: TaxYear, *, low: Fraction = Fraction(1000), **kwargs: object
) -> Fraction:
    """The gross pay that gives this net, to the penny (a payslip's net back to a salary)."""
    high = Fraction(1_000_000)
    for _ in range(80):
        mid = (low + high) / 2
        if take_home(mid, year, **kwargs).net < target_net:  # type: ignore[arg-type]
            low = mid
        else:
            high = mid
    return (low + high) / 2
