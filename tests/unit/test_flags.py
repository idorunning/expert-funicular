"""Unit tests for the flag-detection and smart-default module."""
from __future__ import annotations

from brokerledger.categorize.flags import (
    FLAG_FAST_PAYMENT,
    FLAG_GAMBLING,
    deserialize_flags,
    detect_flags,
    serialize_flags,
    smart_default_category,
)


def test_detects_gambling_keyword():
    flags = detect_flags("Bet365 deposit 07/03", "BET365")
    assert FLAG_GAMBLING in flags


def test_detects_fast_payment_keyword():
    flags = detect_flags("FASTER PAYMENT JOHN SMITH", "JOHN SMITH")
    assert FLAG_FAST_PAYMENT in flags


def test_honours_fp_tag_on_merchant():
    flags = detect_flags("generic description", "JOHN SMITH [FP]")
    assert FLAG_FAST_PAYMENT in flags


def test_no_flags_for_ordinary_merchant():
    flags = detect_flags("tesco metro london gb", "TESCO METRO")
    assert flags == []


def test_smart_default_gambling_debit_is_entertainment():
    assert smart_default_category([FLAG_GAMBLING], is_credit=False) == "Entertainment"


def test_smart_default_gambling_credit_is_blank():
    assert smart_default_category([FLAG_GAMBLING], is_credit=True) is None


def test_smart_default_fp_credit_is_other_income():
    assert smart_default_category([FLAG_FAST_PAYMENT], is_credit=True) == "Other income"


def test_smart_default_fp_debit_is_blank():
    assert smart_default_category([FLAG_FAST_PAYMENT], is_credit=False) is None


def test_serialize_roundtrip():
    flags = [FLAG_GAMBLING, FLAG_FAST_PAYMENT]
    packed = serialize_flags(flags)
    assert packed is not None
    assert deserialize_flags(packed) == sorted(flags)


def test_serialize_empty_returns_none():
    assert serialize_flags([]) is None
    assert deserialize_flags(None) == []
    assert deserialize_flags("") == []
