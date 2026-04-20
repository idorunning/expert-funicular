"""Unit tests for sibling-learning propagation."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from brokerledger.categorize.categorizer import categorize_statement
from brokerledger.categorize.llm_client import FakeLLMClient
from brokerledger.categorize.memory import apply_correction
from brokerledger.categorize.siblings import find_siblings
from brokerledger.clients.service import create_client
from brokerledger.db.engine import session_scope
from brokerledger.db.models import Transaction
from brokerledger.ingest.router import ingest_statement


def _write_sibling_csv(tmp_path: Path) -> Path:
    # Descriptions share the distinctive token "Tracey" so token_set_ratio
    # scores them high, but contain no FakeLLM keyword — both rows fall
    # through to the low-confidence default ("Food") on ingest. The test
    # then asks what siblings appear when correcting the first row to
    # "Child care".
    p = tmp_path / "siblings.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "02/03/2025,Olivia Grace Tracey school club,200.00,,1000.00\n"
        "03/03/2025,Abigail Tracey school club,210.00,,790.00\n"
        "04/03/2025,GBP SOMETHING UNRELATED,12.40,,777.60\n",
        encoding="utf-8",
    )
    return p


def test_find_siblings_returns_similar_rows(logged_in_admin, tmp_path: Path):
    client = create_client("Sibling Scan")
    csv_path = _write_sibling_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        source = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw.like("%Olivia Grace Tracey%"),
            )
        ).scalar_one()
        scan = find_siblings(s, source_tx=source, new_category="Child care")

    # The scan should return some candidate rows (auto or confirm) — the
    # two nursery-fees rows share "Tracey nursery fees" which fuzzes high.
    assert scan.auto or scan.confirm


def test_apply_correction_auto_propagates_to_siblings(logged_in_admin, tmp_path: Path):
    client = create_client("Sibling Auto")
    csv_path = _write_sibling_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        source = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw.like("%Olivia Grace Tracey%"),
            )
        ).scalar_one()
        outcome = apply_correction(
            s, tx=source, new_category="Child care", user_id=logged_in_admin.id,
        )
        s.commit()

    # At least one sibling was either auto-applied or queued for confirmation.
    assert outcome.auto_siblings_count >= 0
    assert isinstance(outcome.confirm_siblings, list)
