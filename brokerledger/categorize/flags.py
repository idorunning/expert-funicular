"""Transaction-level flags — Gambling and Fast Payments.

Flags are orthogonal to categories. A transaction can belong to any category
and also carry one or more flags. Flags drive special treatment in the UI
(coloured chips, always-review) and in smart-default category selection.
"""
from __future__ import annotations

FLAG_GAMBLING = "gambling"
FLAG_FAST_PAYMENT = "fast_payment"

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


# (category_if_debit, category_if_credit)
SMART_DEFAULTS: dict[str, tuple[str | None, str | None]] = {
    FLAG_GAMBLING:      ("Entertainment", None),
    FLAG_FAST_PAYMENT:  (None, "Other income"),
}


def detect_flags(description_raw: str, merchant_normalized: str) -> list[str]:
    """Return flag strings for a transaction description."""
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
}


def flag_display_name(flag: str) -> str:
    return FLAG_DISPLAY_NAMES.get(flag, flag)
