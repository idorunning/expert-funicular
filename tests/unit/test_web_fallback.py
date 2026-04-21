"""Conditional web-lookup fallback in the categorisation pipeline."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from brokerledger.categorize import web_lookup
from brokerledger.categorize.categorizer import categorize_statement
from brokerledger.categorize.llm_client import FakeLLMClient, FewShotExample, LLMResult
from brokerledger.clients.service import create_client
from brokerledger.db.engine import session_scope
from brokerledger.db.models import Transaction
from brokerledger.ingest.router import ingest_statement


class _ScriptedLLM(FakeLLMClient):
    """Returns a scripted low-confidence first result then a better second.

    Records every call so the test can assert how many fired and whether
    ``web_hint`` was passed on the retry.
    """

    def __init__(self, first_confidence: float, retry_confidence: float):
        self._first_conf = first_confidence
        self._retry_conf = retry_confidence
        self.calls: list[tuple[str, str | None]] = []

    def classify(
        self,
        description_raw: str,
        merchant_normalized: str,
        amount: Decimal,
        direction: str,
        posted_date: str,
        few_shot: list[FewShotExample],
        web_hint: str | None = None,
    ) -> LLMResult:
        self.calls.append((description_raw, web_hint))
        if web_hint is None:
            return LLMResult(
                category="Other",
                group="discretionary",
                confidence=self._first_conf,
                reason="first pass, unsure",
                thinking="initial reasoning",
            )
        return LLMResult(
            category="Entertainment",
            group="discretionary",
            confidence=self._retry_conf,
            reason="web hint clarified",
            thinking="after web lookup",
        )


def _write_csv(tmp_path: Path) -> Path:
    p = tmp_path / "demo.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "01/03/2025,ROCKETFLIX SVC,9.99,,990.01\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def _web_enabled(monkeypatch):
    web_lookup.set_enabled(True)
    monkeypatch.setattr(
        web_lookup, "lookup_merchant",
        lambda m: "Rocketflix is a fictional streaming service." if m else None,
    )
    yield
    web_lookup.set_enabled(False)


@pytest.fixture
def _web_disabled(monkeypatch):
    web_lookup.set_enabled(False)
    # Track that lookup_merchant is NEVER called when disabled.
    called = {"count": 0}
    orig = web_lookup.lookup_merchant
    def spy(m):
        called["count"] += 1
        return orig(m)
    monkeypatch.setattr(web_lookup, "lookup_merchant", spy)
    yield called


def test_low_confidence_triggers_web_retry(logged_in_admin, tmp_path: Path, _web_enabled):
    client = create_client("Low Confidence Client")
    csv = _write_csv(tmp_path)
    result = ingest_statement(client.id, csv)
    llm = _ScriptedLLM(first_confidence=0.30, retry_confidence=0.80)
    categorize_statement(result.statement_id, llm=llm)
    assert len(llm.calls) == 2
    assert llm.calls[0][1] is None            # first pass — no hint
    assert llm.calls[1][1] is not None        # retry — hint present
    with session_scope() as s:
        row = s.execute(
            select(Transaction).where(Transaction.client_id == client.id)
        ).scalar_one()
    assert row.category == "Entertainment"
    assert "web-assisted" in (row.reason or "")
    assert "[after web]" in (row.reasoning or "")


def test_high_confidence_skips_web(logged_in_admin, tmp_path: Path, _web_enabled):
    client = create_client("High Confidence Client")
    csv = _write_csv(tmp_path)
    result = ingest_statement(client.id, csv)
    # First pass already confident enough → no retry.
    llm = _ScriptedLLM(first_confidence=0.90, retry_confidence=0.99)
    # Override the first pass to return a non-generic category so the
    # "GENERIC_CATEGORIES" branch doesn't trigger.
    def _first_only(**kwargs):
        llm.calls.append((kwargs["description_raw"], kwargs.get("web_hint")))
        return LLMResult(category="Food", group="discretionary",
                         confidence=0.90, reason="confident",
                         thinking="no need for web")
    llm.classify = _first_only  # type: ignore[assignment]
    categorize_statement(result.statement_id, llm=llm)
    assert len(llm.calls) == 1  # no retry


def test_disabled_setting_skips_web_entirely(logged_in_admin, tmp_path: Path, _web_disabled):
    client = create_client("Web Off Client")
    csv = _write_csv(tmp_path)
    result = ingest_statement(client.id, csv)
    llm = _ScriptedLLM(first_confidence=0.20, retry_confidence=0.90)
    categorize_statement(result.statement_id, llm=llm)
    # First pass ran, retry did NOT run because web is off.
    assert len(llm.calls) == 1
    assert _web_disabled["count"] == 0
