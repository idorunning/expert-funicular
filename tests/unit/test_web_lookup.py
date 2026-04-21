"""Unit tests for the opt-in merchant web lookup."""
from __future__ import annotations

import pytest

from brokerledger.categorize import web_lookup


def test_disabled_by_default(db_session):
    assert web_lookup.is_enabled() is False


def test_returns_none_when_disabled(db_session, monkeypatch):
    # Even if we stub the HTTP call, it must not be reached when off.
    called = {"n": 0}

    def _boom(*_a, **_kw):
        called["n"] += 1
        raise AssertionError("httpx.get should not be called when lookup is off")

    monkeypatch.setattr(web_lookup.httpx, "get", _boom)
    assert web_lookup.lookup_merchant("TESCO METRO") is None
    assert called["n"] == 0


def test_enable_and_disable_roundtrip(db_session):
    web_lookup.set_enabled(True)
    assert web_lookup.is_enabled() is True
    web_lookup.set_enabled(False)
    assert web_lookup.is_enabled() is False


def test_sanitiser_strips_digits_and_specials():
    # Defence in depth — merchant should never leak reference numbers.
    assert web_lookup._sanitise_merchant("TESCO 123456789") == "TESCO"
    assert web_lookup._sanitise_merchant("JOHN SMITH [FP] ref#A7") == "JOHN SMITH FP ref A"
    assert web_lookup._sanitise_merchant("   ") == ""


def test_lookup_returns_abstract_when_enabled(db_session, monkeypatch):
    web_lookup.set_enabled(True)

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"AbstractText": "Tesco is a British multinational groceries retailer."}

    def _fake_get(url, timeout):
        assert "TESCO" in url  # only the sanitised merchant
        assert "amount" not in url.lower()
        return _FakeResponse()

    monkeypatch.setattr(web_lookup.httpx, "get", _fake_get)
    out = web_lookup.lookup_merchant("TESCO METRO 123")
    assert out is not None
    assert "Tesco" in out


def test_lookup_swallows_network_errors(db_session, monkeypatch):
    web_lookup.set_enabled(True)

    def _boom(*_a, **_kw):
        import httpx as _h
        raise _h.ConnectError("offline")

    monkeypatch.setattr(web_lookup.httpx, "get", _boom)
    assert web_lookup.lookup_merchant("TESCO") is None


@pytest.mark.parametrize("too_short", ["", "AB", "  "])
def test_lookup_skips_short_queries(db_session, monkeypatch, too_short):
    web_lookup.set_enabled(True)
    called = {"n": 0}

    def _boom(*_a, **_kw):
        called["n"] += 1
        raise AssertionError("should not reach network")

    monkeypatch.setattr(web_lookup.httpx, "get", _boom)
    assert web_lookup.lookup_merchant(too_short) is None
    assert called["n"] == 0
