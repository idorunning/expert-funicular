"""System prompt shape + reasoning trace end-to-end persistence."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from brokerledger.categorize.categorizer import categorize_statement
from brokerledger.categorize.llm_client import FakeLLMClient, FewShotExample, LLMResult
from brokerledger.categorize.prompts import build_system_prompt
from brokerledger.categorize.taxonomy import user_visible_categories
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


def test_prompt_contains_includes_line_per_user_visible_category():
    """Every user-visible category must appear with an ``includes:`` hint so
    the model reasons from category descriptions rather than memorised
    merchant names. This is the mechanism that lets the model infer e.g.
    ``POCKET MONEY -> Child care`` from the taxonomy alone."""
    prompt = build_system_prompt()
    for cat in user_visible_categories():
        assert cat in prompt, f"Category {cat!r} missing from prompt"
        # Locate the category line and assert the includes hint is attached.
        line_start = prompt.find(f":: {cat}")
        assert line_start >= 0, f"Category line for {cat!r} not found"
        line_end = prompt.find("\n", line_start)
        line = prompt[line_start:line_end]
        assert "includes:" in line, (
            f"Category {cat!r} line has no includes hint: {line!r}"
        )


def test_prompt_mentions_pocket_money_via_child_care_description():
    """POCKET MONEY is covered via the Child care description's vocabulary,
    not as a hard-coded worked example. That's what allows the model to
    generalise to other allowance-like terms."""
    prompt = build_system_prompt()
    lower = prompt.lower()
    # Pocket money should appear — but in the Child care includes line,
    # not as a "Description (as printed): POCKET MONEY" worked example.
    assert "pocket money" in lower
    assert 'description (as printed): "pocket money"' not in lower


def test_prompt_keeps_single_format_example_only():
    """The prompt should keep exactly one worked example (a SALARY credit)
    to demonstrate the JSON output shape. Content-specific worked examples
    (POCKET MONEY, ALLOWANCE, JUST EAT) have been retired — they are
    redundant with the taxonomy's includes hints."""
    prompt = build_system_prompt()
    # The format-demonstrating example survives.
    assert "SALARY" in prompt
    # Content-specific worked examples have been removed.
    assert 'Description (as printed): "JUST EAT LONDON"' not in prompt
    assert 'Description (as printed): "ALLOWANCE"' not in prompt
    assert 'Description (as printed): "POCKET MONEY"' not in prompt


def test_prompt_does_not_instruct_model_to_avoid_examples():
    """Regression: an earlier iteration told the model ``do NOT copy
    verbatim``, which small models read as 'don't reuse the taxonomy
    mappings'. We want the opposite behaviour — reason from the includes
    hints and pick the matching category."""
    prompt = build_system_prompt()
    lower = prompt.lower()
    assert "do not copy" not in lower
    assert "do not copy verbatim" not in lower


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
