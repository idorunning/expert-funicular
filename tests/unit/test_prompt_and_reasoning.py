"""System prompt shape + reasoning trace end-to-end persistence."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from brokerledger.categorize.categorizer import categorize_statement
from brokerledger.categorize.llm_client import FakeLLMClient, FewShotExample, LLMResult
from brokerledger.categorize.prompts import build_system_prompt
from brokerledger.clients.service import create_client
from brokerledger.db.engine import session_scope
from brokerledger.db.models import Transaction
from brokerledger.ingest.router import ingest_statement


def test_prompt_requests_step_by_step_thinking():
    prompt = build_system_prompt()
    lower = prompt.lower()
    assert "step by step" in lower or "step-by-step" in lower


def test_prompt_declares_thinking_key_in_schema():
    prompt = build_system_prompt()
    # The schema section should mention the thinking key explicitly so
    # the model knows to emit it.
    assert '"thinking"' in prompt


def test_prompt_contains_pocket_money_worked_example():
    prompt = build_system_prompt()
    assert "POCKET MONEY" in prompt
    assert "Child care" in prompt


def test_prompt_contains_salary_and_takeaway_worked_examples():
    prompt = build_system_prompt()
    assert "SALARY" in prompt
    assert "JUST EAT" in prompt


class _ThinkingLLMClient(FakeLLMClient):
    """FakeLLMClient subclass that returns a chain-of-thought trace."""

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
        base = super().classify(
            description_raw=description_raw,
            merchant_normalized=merchant_normalized,
            amount=amount,
            direction=direction,
            posted_date=posted_date,
            few_shot=few_shot,
        )
        base.thinking = f"Thinking about {merchant_normalized!r}: picked {base.category}."
        return base


def _write_csv(tmp_path: Path) -> Path:
    p = tmp_path / "demo.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "01/03/2025,UNKNOWN MERCHANT XYZ,14.75,,985.25\n",
        encoding="utf-8",
    )
    return p


def test_reasoning_from_llm_is_persisted_on_transaction(logged_in_admin, tmp_path: Path):
    client = create_client("Reasoning Client")
    csv_path = _write_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=_ThinkingLLMClient())

    with session_scope() as s:
        row = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
    assert row.reasoning
    assert "Thinking about" in row.reasoning
