"""Turning bank text into a merchant name (PLAN.md §8.4 step 1).

"DD SKY DIGITAL 4471029 GB" and "SKY UK LTD" are both Sky. Bank text is cleaned of
references, card numbers and payment-type codes; then the user-editable alias table has the
final say.
"""

import re
from collections.abc import Iterable

# Payment-type codes and filler that banks put around the payee.
_NOISE = re.compile(
    r"\b(?:DD|D/D|DIRECT DEBIT|DDR|STO|S/O|SO|BGC|BAC|BACS|FPI|FPO|FASTER PAYMENT|DPC|POS|VIS|"
    r"VISA|CHQ|CLP|CONTACTLESS|CARD PAYMENT TO|CARD PAYMENT|PAYMENT TO|PAYMENT FROM|TO|FROM|ON|"
    r"REF|REFERENCE|GB|GBR|GBP|LTD|LIMITED|PLC|UK|WWW|COM|CO)\b"
)
_REFERENCES = re.compile(
    r"\b[\dX*]{4,}\b|\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b|\b\d{2}[A-Z]{3}\d{0,2}\b"
)
_NON_WORD = re.compile(r"[^A-Z0-9&+ ]+")
_SPACES = re.compile(r"\s+")

# Words too common to identify a payee on their own.
STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "of",
        "via",
        "ltd",
        "limited",
        "plc",
        "uk",
        "co",
        "com",
        "www",
        "bill",
        "bills",
        "payment",
        "payments",
        "fee",
        "fees",
        "fund",
        "money",
        "account",
        "insurance",
        "tv",
        "car",
        "phone",
        "phones",
        "savings",
        "services",
        "service",
    }
)


def clean(text: str) -> str:
    # References first, while dates still have their slashes ("ON 02/11").
    up = _REFERENCES.sub(" ", text.upper())
    up = _NON_WORD.sub(" ", up)
    up = _NOISE.sub(" ", up)
    return _SPACES.sub(" ", up).strip()


def normalise(
    description: str, counterparty: str | None, aliases: Iterable[tuple[str, str]]
) -> str:
    """The merchant name for a transaction: an alias if one matches, else the cleaned text."""
    haystack = f"{counterparty or ''} {description}".upper()
    for pattern, merchant in sorted(aliases, key=lambda a: -len(a[0])):
        if pattern.upper() in haystack:
            return merchant
    cleaned = clean(counterparty or description) or clean(description)
    words = cleaned.split()[:4]
    return " ".join(_case(w, i == 0) for i, w in enumerate(words)) or "Unknown"


_SMALL = frozenset({"OF", "AND", "AT", "THE", "FOR", "IN", "BY", "&"})


def _case(word: str, first: bool) -> str:
    """Title case, but initials stay capitals: "EE", "BP", "NHS"; "OF" and "AT" go small."""
    if word in _SMALL and not first:
        return word.lower()
    if len(word) <= 2 and word not in _SMALL:
        return word
    if len(word) == 3 and word.isalpha() and not any(c in "AEIOU" for c in word):
        return word  # NHS, RBS, SKY
    return word.capitalize()


def tokens(text: str | None) -> set[str]:
    """Identifying words, lower-case: "Ultimate Pet Partners" -> {"ultimate", "pet", "partners"}."""
    if not text:
        return set()
    words = re.findall(r"[a-z0-9&+]+", text.lower())
    return {w for w in words if len(w) >= 2 and w not in STOPWORDS}


class Haystack:
    """A transaction's text, ready for token matching."""

    def __init__(self, *parts: str | None) -> None:
        text = " ".join(p for p in parts if p).lower()
        self.words = set(re.findall(r"[a-z0-9&+]+", text))
        self.compact = re.sub(r"[^a-z0-9]", "", text)

    def has(self, token: str) -> bool:
        # Short tokens ("ee", "sky") must be whole words, so "ee" doesn't match "fees".
        if len(token) <= 3:
            return token in self.words
        return token in self.compact

    def has_phrase(self, text: str | None) -> bool:
        """The whole name, spaces ignored: "1stCentral" matches "1ST CENTRAL"."""
        if not text:
            return False
        compact = re.sub(r"[^a-z0-9]", "", text.lower())
        return len(compact) >= 4 and compact in self.compact
