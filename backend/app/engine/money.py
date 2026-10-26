"""Exact money arithmetic. Amounts are integer pence; intermediates are Fractions."""

from fractions import Fraction
from typing import Literal

Frequency = Literal["monthly", "annual", "quarterly", "weekly"]

# Months per period, as an exact divisor of the period amount.
_PER_MONTH: dict[str, Fraction] = {
    "monthly": Fraction(1),
    "annual": Fraction(1, 12),
    "quarterly": Fraction(1, 3),
    "weekly": Fraction(52, 12),
}


def monthly_equivalent(amount_pence: int, frequency: Frequency) -> Fraction:
    """Exact monthly equivalent, e.g. an annual £49.99 is 4999/12 pence, not 417."""
    return amount_pence * _PER_MONTH[frequency]


def ceil_to_step(value: Fraction | int, step: int) -> int:
    """Round up to the next multiple of `step` pence. Exact multiples stay put."""
    if step <= 0:
        raise ValueError("step must be positive")
    v = Fraction(value)
    q, r = divmod(v.numerator, v.denominator * step)
    return (q + (1 if r else 0)) * step


def round_pence(value: Fraction | int) -> int:
    """Round to the nearest penny, halves away from zero (as Excel displays it)."""
    v = Fraction(value)
    sign = -1 if v < 0 else 1
    whole, rem = divmod(abs(v.numerator), v.denominator)
    return sign * (whole + (1 if 2 * rem >= v.denominator else 0))


def decimal_text(value: Fraction, places: int = 4) -> str:
    """A rate as plain decimal text: "2.79", "11.6", "20" (exact when it terminates)."""
    scaled = value * 10**places
    rounded = scaled.numerator // scaled.denominator
    if scaled - rounded >= Fraction(1, 2):
        rounded += 1
    sign = "-" if rounded < 0 else ""
    whole, part = divmod(abs(rounded), 10**places)
    text = f"{whole}.{part:0{places}d}".rstrip("0").rstrip(".")
    return f"{sign}{text}"
