"""Smoke tests for the terminal trace CLI."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from brokerledger import trace
from brokerledger.categorize.llm_client import FakeLLMClient, LLMResult


def test_parse_args_file_only(tmp_path: Path):
    p = tmp_path / "demo.csv"
    p.write_text("x", encoding="utf-8")
    args = trace._parse_args([str(p)])
    assert args.path == p
    assert args.web is False
    assert args.fake_llm is False


def test_parse_args_flags(tmp_path: Path):
    p = tmp_path / "demo.csv"
    p.write_text("x", encoding="utf-8")
    args = trace._parse_args([str(p), "--web", "--fake-llm", "--client", "Bob"])
    assert args.web is True
    assert args.fake_llm is True
    assert args.client == "Bob"


def test_tracing_llm_wraps_and_prints(capsys):
    tracing = trace._TracingLLM(FakeLLMClient())
    tracing.classify(
        description_raw="TESCO STORES LONDON",
        merchant_normalized="TESCO",
        amount=Decimal("-12.34"),
        direction="debit",
        posted_date="2025-03-01",
        few_shot=[],
    )
    out = capsys.readouterr().out
    assert "[llm call " in out
    assert "[thinking" in out
    assert tracing.calls == 1


def test_tracing_llm_prints_retry_marker_when_web_hint_supplied(capsys):
    tracing = trace._TracingLLM(FakeLLMClient())
    tracing.classify(
        description_raw="UNKNOWN MERCH",
        merchant_normalized="UNKNOWN MERCH",
        amount=Decimal("-5.00"),
        direction="debit",
        posted_date="2025-03-01",
        few_shot=[],
        web_hint="fictional web hint",
    )
    out = capsys.readouterr().out
    assert "[llm retry" in out


def test_main_rejects_missing_file(tmp_path: Path, capsys):
    missing = tmp_path / "nope.csv"
    rc = trace.main([str(missing)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "file not found" in err.lower()
