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
    r"CONTACTLESS PAYMENT|CONTACTLESS|POS PURCHASE|POS|BP|VIS|VISA|"
    r"GOOGLE PAY|APPLE PAY)\s+",
    re.IGNORECASE,
)
# Prefixes that mark a person-to-person / Faster Payment / instant transfer.
# We strip them like other prefixes but append a [FP] tag to the normalized
# merchant so the categoriser can short-circuit to the P2P risk category.
_P2P_PREFIX_RE = re.compile(
    r"^(FASTER PAYMENT(S)?|FPS|FPI|FPO|FP|BACS|TRANSFER (TO|FROM)|"
    r"TFR|BANK TRANSFER|MOBILE PAYMENT|INSTANT TRANSFER)\s+",
    re.IGNORECASE,
)
_P2P_TAG = "[FP]"
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
# The first matching token is moved to the front of the normalised string.
_STRONG_TOKENS = (
    "COUNCIL TAX",
    "TV LICENCE",
    "TV LICENSING",
    "THAMES WATER",
    # Household / child-related low-signal descriptions that would otherwise
    # be scrubbed to an empty string by the cleanup pipeline. Preserving them
    # gives the merchant-rule register and the LLM a stable key to learn from.
    "POCKET MONEY",
    "ALLOWANCE",
    "SCHOOL FEES",
    "SCHOOL MEALS",
    "TUITION",
    "NURSERY FEES",
    "CHILDMINDER",
    # Gambling brands — many contain digits or no spaces, so protect them from
    # the punctuation/digit scrub and give the fuzzy matcher a clean target.
    "BET365",
    "PADDYPOWER",
    "PADDY POWER",
    "SKYBET",
    "SKY BET",
    "SKY VEGAS",
    "SKY BINGO",
    "WILLIAM HILL",
    "BETFAIR",
    "LADBROKES",
    "CORAL",
    "32RED",
    "CASUMO",
    "BETWAY",
    "UNIBET",
    "BETFRED",
    "MRQ",
    "BETVICTOR",
    "LEOVEGAS",
    "GROSVENOR",
    "VIRGIN GAMES",
    "POKERSTARS",
    "888CASINO",
    "888 CASINO",
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

    # Detect (and strip) person-to-person / Faster Payment prefixes before the
    # generic bank-prefix stripper runs. We tag the output so the categoriser
    # can short-circuit every P2P payment to the risk category.
    is_p2p = False
    prev = None
    while prev != s:
        prev = s
        new_s = _P2P_PREFIX_RE.sub("", s)
        if new_s != s:
            is_p2p = True
            s = new_s

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

    # Collapse whitespace and punctuation (keep [ and ] so the [FP] tag survives).
    s = re.sub(r"[^A-Z0-9&/+\-.\[\] ]+", " ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()

    if preserved:
        s = (preserved[0] + (" " + s if s else "")).strip()

    if is_p2p and _P2P_TAG not in s:
        s = (s + " " + _P2P_TAG).strip() if s else _P2P_TAG

    # Empty-fallback: if every transform stripped the description to nothing,
    # fall back to an uppercased whitespace-collapsed copy of the raw input so
    # the merchant-rule register has a non-empty key. Otherwise every low-
    # signal description would collide on "" and a broker's training wouldn't
    # stick.
    if not s:
        fallback = _WHITESPACE_RE.sub(" ", description.upper()).strip()
        s = fallback[:_MAX_LEN].rstrip()

    if len(s) > _MAX_LEN:
        s = s[:_MAX_LEN].rstrip()
    return s
