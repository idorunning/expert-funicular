"""Unit tests for the AI Training Zone backend + reasoning persistence."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from brokerledger.categorize import training
from brokerledger.categorize.categorizer import categorize_statement
from brokerledger.categorize.llm_client import FakeLLMClient, FewShotExample, LLMResult
from brokerledger.clients.service import create_client
from brokerledger.db.engine import session_scope
from brokerledger.db.models import MerchantRule, TrainingNote, Transaction
from brokerledger.ingest.router import ingest_statement


def _write_demo_csv(tmp_path: Path) -> Path:
    p = tmp_path / "demo.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "01/03/2025,POCKET MONEY,15.00,,985.00\n"
        "02/03/2025,UNKNOWN MERCHANT XYZ,14.75,,970.25\n",
        encoding="utf-8",
    )
    return p


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
    ) -> LLMResult:
        base = super().classify(
            description_raw, merchant_normalized, amount, direction, posted_date, few_shot,
        )
        base.thinking = (
            f"Looking at '{description_raw}', the merchant appears to be "
            f"{merchant_normalized or 'unclear'}. Going with {base.category}."
        )
        return base


def _ingest_demo(tmp_path: Path, *, use_thinking: bool = False):
    client = create_client("Training Test Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    llm = _ThinkingLLMClient() if use_thinking else FakeLLMClient()
    categorize_statement(result.statement_id, llm=llm)
    return client


def test_reasoning_is_persisted_from_llm(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path, use_thinking=True)
    with session_scope() as s:
        row = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
    assert row.reasoning
    assert "UNKNOWN MERCHANT XYZ" in row.reasoning or "Looking at" in row.reasoning


def test_reasoning_blank_when_llm_returns_none(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path, use_thinking=False)
    with session_scope() as s:
        row = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
    # FakeLLMClient returns thinking="" so the DB column should be NULL.
    assert row.reasoning is None


def test_save_note_requires_note_or_category(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path)
    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(Transaction.client_id == client.id)
        ).scalars().first()
        tx_id = tx.id
    with pytest.raises(ValueError):
        training.save_note(
            transaction_id=tx_id,
            user_id=logged_in_admin.id,
            note="",
            suggested_category=None,
        )


def test_save_note_with_category_only(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path)
    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "POCKET MONEY",
            )
        ).scalar_one()
        tx_id = tx.id
    note_id = training.save_note(
        transaction_id=tx_id,
        user_id=logged_in_admin.id,
        note="",
        suggested_category="Child care",
    )
    with session_scope() as s:
        note = s.get(TrainingNote, note_id)
    assert note is not None
    assert note.suggested_category == "Child care"
    assert note.consumed_at is None
    assert note.dismissed_at is None


def test_run_training_pass_applies_notes_and_creates_rule(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path)
    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "POCKET MONEY",
            )
        ).scalar_one()
        tx_id = tx.id
        merchant = tx.merchant_normalized
    training.save_note(
        transaction_id=tx_id,
        user_id=logged_in_admin.id,
        note="Pocket money is a child allowance — map to Child care.",
        suggested_category="Child care",
    )
    report = training.run_training_pass(user_id=logged_in_admin.id)
    assert report.notes_processed == 1
    assert report.rules_created + report.rules_updated == 1
    # The note should be marked consumed.
    with session_scope() as s:
        note = s.execute(select(TrainingNote)).scalars().first()
        assert note.consumed_at is not None
        # A merchant rule should exist for this merchant/category.
        rule = s.execute(
            select(MerchantRule).where(
                MerchantRule.merchant_normalized == merchant,
                MerchantRule.category == "Child care",
            )
        ).scalar_one()
        assert rule.weight >= 2
        # The source transaction was reclassified.
        tx = s.get(Transaction, tx_id)
        assert tx.category == "Child care"
        assert tx.source == "user"
        assert tx.needs_review == 0


def test_run_training_pass_skips_notes_without_category(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path)
    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(Transaction.client_id == client.id)
        ).scalars().first()
        tx_id = tx.id
    training.save_note(
        transaction_id=tx_id,
        user_id=logged_in_admin.id,
        note="Not sure yet, come back later.",
        suggested_category=None,
    )
    report = training.run_training_pass(user_id=logged_in_admin.id)
    assert report.notes_processed == 0
    assert report.skipped_no_category == 1
    # Note still unconsumed so the broker can pick a category later.
    with session_scope() as s:
        note = s.execute(select(TrainingNote)).scalars().first()
        assert note.consumed_at is None


def test_dismiss_note_excludes_from_future_passes(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path)
    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(Transaction.client_id == client.id)
        ).scalars().first()
        tx_id = tx.id
    note_id = training.save_note(
        transaction_id=tx_id,
        user_id=logged_in_admin.id,
        note="Never mind.",
        suggested_category="Child care",
    )
    assert training.dismiss_note(note_id, logged_in_admin.id) is True
    assert not training.list_unconsumed()
    report = training.run_training_pass(user_id=logged_in_admin.id)
    assert report.notes_processed == 0


def test_run_training_pass_idempotent_when_no_unconsumed_notes(logged_in_admin, tmp_path: Path):
    _ingest_demo(tmp_path)
    report = training.run_training_pass(user_id=logged_in_admin.id)
    assert report.notes_processed == 0
    assert report.rules_created == 0
    assert report.rules_updated == 0


def test_list_unconsumed_returns_tx_context(logged_in_admin, tmp_path: Path):
    client = _ingest_demo(tmp_path, use_thinking=True)
    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
        tx_id = tx.id
    training.save_note(
        transaction_id=tx_id,
        user_id=logged_in_admin.id,
        note="Please review",
        suggested_category=None,
    )
    rows = training.list_unconsumed()
    assert len(rows) == 1
    row = rows[0]
    assert row["tx_id"] == tx_id
    assert row["description"] == "UNKNOWN MERCHANT XYZ"
    assert row["note"] == "Please review"
    assert row["suggested_category"] is None
    # reasoning trace flows through
    assert "UNKNOWN MERCHANT XYZ" in (row["reasoning"] or "")
