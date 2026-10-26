"""Tax-year configs: the cited files in config/tax/, as engine `TaxYear`s.

Every rate and threshold the take-home and childcare engines use comes from these files;
each figure group names the gov.scot / gov.uk / SPPA page it was taken from. A year's file
is `config/tax/<YYYY-YY>.json`, e.g. 2026-27.json for 6 April 2026 to 5 April 2027.
"""

import json
from datetime import date
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.engine import tax

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "tax"


class TaxConfigMissing(LookupError):
    pass


def _f(value: Any) -> Fraction:
    return Fraction(str(value))


def _opt(value: Any) -> Fraction | None:
    return None if value is None else _f(value)


def label_for(on: date) -> str:
    """The tax year a date falls in: 6 April to 5 April."""
    start = on.year if (on.month, on.day) >= (4, 6) else on.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def available() -> list[str]:
    return sorted(p.stem for p in CONFIG_DIR.glob("*.json"))


@lru_cache
def raw(label: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{label}.json"
    if not path.is_file():
        raise TaxConfigMissing(f"There's no tax config for {label} (config/tax/{label}.json).")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@lru_cache
def year(label: str) -> tax.TaxYear:
    d = raw(label)
    ni = d["national_insurance"]
    cb = d["child_benefit"]
    return tax.TaxYear(
        label=d["tax_year"],
        starts=date.fromisoformat(d["starts"]),
        ends=date.fromisoformat(d["ends"]),
        personal_allowance=_f(d["personal_allowance"]),
        taper_threshold=_f(d["allowance_taper"]["threshold"]),
        taper_ratio=_f(d["allowance_taper"]["ratio"]),
        bands=tuple(
            tax.Band(b["label"], _f(b["rate"]), _opt(b["up_to"])) for b in d["scottish_bands"]
        ),
        ni_primary_threshold=_f(ni["primary_threshold"]),
        ni_upper_limit=_f(ni["upper_earnings_limit"]),
        ni_main_rate=_f(ni["main_rate"]),
        ni_upper_rate=_f(ni["upper_rate"]),
        nhs_tiers=tuple(
            tax.Tier(_opt(t["up_to"]), _f(t["rate"])) for t in d["nhs_scotland_pension_tiers"]
        ),
        child_benefit_first_weekly=_f(cb["first_weekly"]),
        child_benefit_other_weekly=_f(cb["other_weekly"]),
        hicbc_threshold=_f(d["high_income_child_benefit_charge"]["threshold"]),
        hicbc_step=_f(d["high_income_child_benefit_charge"]["step"]),
        tfc_rate=_f(d["tax_free_childcare"]["rate"]),
        tfc_cap_per_child=_f(d["tax_free_childcare"]["cap_per_child"]),
        funded_hours_per_year=_f(d["funded_childcare"]["hours_per_year"]),
        nhs_tiers_continuing=tuple(
            tax.Tier(_opt(t["up_to"]), _f(t["rate"]))
            for t in d.get("nhs_scotland_pension_tiers_continuing", [])
        ),
    )


def year_for(on: date) -> tax.TaxYear:
    """The config for the tax year `on` falls in, else the latest one before it."""
    wanted = label_for(on)
    labels = available()
    if wanted in labels:
        return year(wanted)
    earlier = [label for label in labels if label <= wanted]
    if not earlier:
        raise TaxConfigMissing(f"There's no tax config for {wanted} or any year before it.")
    return year(earlier[-1])


def is_exact(on: date) -> bool:
    """True when the tax year's own config exists (not a fallback to an earlier year)."""
    return label_for(on) in available()


def funded_from(born_on: date, label: str) -> date:
    """The 1st of the month a child's funded hours start (the term after their 3rd birthday).

    The windows come from the config: a birthday in a window starts at that window's term.
    """
    third = date(born_on.year + 3, born_on.month, min(born_on.day, 28))
    for window in raw(label)["funded_childcare"]["start_after_third_birthday"]:
        months = window["birth_months"]
        if third.month in months:
            start_month = window["starts_month"]
            start_year = third.year + (1 if start_month < third.month else 0)
            return date(start_year, start_month, 1)
    raise TaxConfigMissing(f"No funded-hours window covers a birthday in month {third.month}.")
