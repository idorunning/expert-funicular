"""Transaction-level flags — Gambling, Fast Payments, and Inbound.

Flags are orthogonal to categories. A transaction can belong to any category
and also carry one or more flags. Flags drive special treatment in the UI
(coloured chips, always-review) and in smart-default category selection.
"""
from __future__ import annotations

FLAG_GAMBLING = "gambling"
FLAG_FAST_PAYMENT = "fast_payment"
FLAG_INSURANCE_DD = "insurance_dd"   # recurring insurance direct debit
FLAG_INBOUND = "inbound"   # any credit / money-in transaction

# Keyword hits that mark a description as gambling. Lower-case substrings.
_GAMBLING_KEYWORDS: tuple[str, ...] = (
    "betfair",
    "paddy power",
    "william hill",
    "ladbrokes",
    "coral.co",
    "bet365",
    "skybet",
    "sky bet",
    "betway",
    "casino",
    "gambling",
    "pokerstars",
    "unibet",
    "betfred",
)

# Keyword hits that mark a description as a Faster Payment / bank transfer.
_FAST_PAYMENT_KEYWORDS: tuple[str, ...] = (
    "faster payment",
    "faster pay",
    "fpo ",
    "bacs ",
    "chaps ",
)

# Keyword hits that mark a direct debit as an insurance premium.  UK brokers
# want these called out as a separate risk flag because they are recurring
# monthly commitments that affect affordability (same bucket as gambling and
# fast-payment risk from the underwriter's point of view).
_INSURANCE_KEYWORDS: tuple[str, ...] = (
    "insurance",
    "insure",
    " assurance",    # "life assurance" — leading space avoids matching "reassurance"
    "aviva",
    "direct line",
    "churchill",
    "admiral",
    "more than",
    "hastings",
    "lv=",
    "lv =",
    "legal & general",
    "legal and general",
    "nfu mutual",
    "saga ",
    "esure",
    "sheilas wheels",
    "swiftcover",
    "petplan",
    "pet plan",
    "axa ",
    "allianz",
    "zurich",
    "royal london",
    "prudential",
    "scottish widows",
    "vitality",
)
_INSURANCE_DD_TOKENS: tuple[str, ...] = (
    "direct debit",
    "dd ",
    " dd",
)


# (category_if_debit, category_if_credit)
SMART_DEFAULTS: dict[str, tuple[str | None, str | None]] = {
    FLAG_GAMBLING:      ("Entertainment", None),
    FLAG_FAST_PAYMENT:  (None, "Other income"),
    FLAG_INSURANCE_DD:  ("Insurance", None),
}


def detect_flags(
    description_raw: str,
    merchant_normalized: str,
    *,
    direction: str = "debit",
) -> list[str]:
    """Return flag strings for a transaction.

    ``direction`` should be ``'credit'`` or ``'debit'`` (default). All
    credits automatically receive ``FLAG_INBOUND`` so the broker can
    easily multi-select and disregard personal transfers.
    """
    result: list[str] = []
    blob = f"{description_raw or ''} {merchant_normalized or ''}".lower()

    # Gambling
    for kw in _GAMBLING_KEYWORDS:
        if kw in blob:
            result.append(FLAG_GAMBLING)
            break

    # Fast payments — also honour the explicit [FP] tag set by the normaliser.
    if "[fp]" in blob or "[FP]" in (merchant_normalized or ""):
        result.append(FLAG_FAST_PAYMENT)
    else:
        for kw in _FAST_PAYMENT_KEYWORDS:
            if kw in blob:
                result.append(FLAG_FAST_PAYMENT)
                break

    # Insurance direct debits. Match either an explicit insurance-brand name
    # OR the combination of a DD marker with a generic insurance token.  We
    # skip crediting this flag to credits (no such thing as a premium being
    # paid into the account).
    if direction == "debit":
        is_insurance = False
        for kw in _INSURANCE_KEYWORDS:
            if kw in blob:
                is_insurance = True
                break
        if is_insurance:
            # Only mark as a DD when we can see a DD marker or the phrase
            # "direct debit" is present — otherwise it's ambiguous (could be a
            # one-off card payment that we don't want to flag).
            for tok in _INSURANCE_DD_TOKENS:
                if tok in blob:
                    result.append(FLAG_INSURANCE_DD)
                    break
            else:
                # Brand-name insurers on a debit almost always arrive as a DD
                # even when the statement line doesn't say so; flag them anyway.
                result.append(FLAG_INSURANCE_DD)

    # All credits get the inbound marker so brokers can spot and disregard
    # personal bank-to-bank transfers in one bulk action.
    if direction == "credit":
        result.append(FLAG_INBOUND)

    return result


def serialize_flags(flags: list[str]) -> str | None:
    """Pack a list of flag strings into a comma-separated column value."""
    if not flags:
        return None
    return ",".join(sorted(set(flags)))


def deserialize_flags(value: str | None) -> list[str]:
    """Unpack a comma-separated column value back into a list."""
    if not value:
        return []
    return [s for s in (v.strip() for v in value.split(",")) if s]


def smart_default_category(flags: list[str], is_credit: bool) -> str | None:
    """Suggested category for a flagged transaction. ``None`` = leave blank."""
    for flag in flags:
        debit_cat, credit_cat = SMART_DEFAULTS.get(flag, (None, None))
        cat = credit_cat if is_credit else debit_cat
        if cat:
            return cat
    return None


FLAG_DISPLAY_NAMES: dict[str, str] = {
    FLAG_GAMBLING:     "Gambling",
    FLAG_FAST_PAYMENT: "Fast payment",
    FLAG_INSURANCE_DD: "Insurance DD",
    FLAG_INBOUND:      "Inbound",
}


def flag_display_name(flag: str) -> str:
    return FLAG_DISPLAY_NAMES.get(flag, flag)
