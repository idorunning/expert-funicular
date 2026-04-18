"""Canonical Transaction dataclass + merchant-string normalisation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

Direction = Literal["debit", "credit"]


@dataclass
class RawTransaction:
    """Output of a statement parser, before categorisation."""
    posted_date: date
    description_raw: str
    amount: Decimal  # signed: negative = debit, positive = credit
    balance_after: Decimal | None = None
    currency: str = "GBP"
    merchant_normalized: str = field(init=False, default="")
    direction: Direction = field(init=False, default="debit")

    def __post_init__(self) -> None:
        self.direction = "credit" if self.amount > 0 else "debit"
        self.merchant_normalized = normalize_merchant(self.description_raw)


_BANK_PREFIX_RE = re.compile(
    r"^(CARD PAYMENT TO|CARD PAYMENT|DIRECT DEBIT|D\.?D\.?|STANDING ORDER|S\.?O\.?|"
    r"CONTACTLESS PAYMENT|CONTACTLESS|POS PURCHASE|POS|BP|VIS|VISA|FPI|FPO|FASTER PAYMENT|"
    r"PAYPAL|GOOGLE PAY|APPLE PAY)\s+",
    re.IGNORECASE,
)
_TRAILING_LOCATION_RE = re.compile(
    r"\s+(LONDON|MANCHESTER|BIRMINGHAM|BRISTOL|EDINBURGH|GLASGOW|LEEDS|LIVERPOOL|"
    r"GB|UK|GBR|[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\s*$",
    re.IGNORECASE,
)
_LONG_DIGIT_RUN_RE = re.compile(r"\b\d{5,}\b")
_REF_TOKEN_RE = re.compile(r"\b(REF|REFERENCE|TRF|TRANSFER)[:# ]+\S+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_LEN = 60


# Tokens we preserve verbatim because they are strong signals for categorisation.
_STRONG_TOKENS = (
    "COUNCIL TAX",
    "TV LICENCE",
    "TV LICENSING",
    "THAMES WATER",
)


def normalize_merchant(description: str) -> str:
    """Canonicalise a bank description into a short, stable merchant string."""
    if not description:
        return ""
    s = description.upper()

    # Keep strong signal tokens intact by pre-extracting them.
    preserved: list[str] = []
    for tok in _STRONG_TOKENS:
        if tok in s:
            preserved.append(tok)
            s = s.replace(tok, " ")

    # Strip common bank prefixes, possibly multiple.
    prev = None
    while prev != s:
        prev = s
        s = _BANK_PREFIX_RE.sub("", s)

    # Drop reference tokens and long digit runs.
    s = _REF_TOKEN_RE.sub("", s)
    s = _LONG_DIGIT_RUN_RE.sub("", s)

    # Trim trailing city / postcode tokens. Loop to catch chains like "LONDON GB".
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_LOCATION_RE.sub("", s)

    # Collapse whitespace and punctuation.
    s = re.sub(r"[^A-Z0-9&/+\-. ]+", " ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()

    if preserved:
        s = (preserved[0] + (" " + s if s else "")).strip()

    if len(s) > _MAX_LEN:
        s = s[:_MAX_LEN].rstrip()
    return s
