"""Portable JSON mirror of user corrections."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from brokerledger.categorize import corrections_cache
from brokerledger.categorize.categorizer import categorize_statement
from brokerledger.categorize.llm_client import FakeLLMClient
from brokerledger.categorize.memory import apply_correction
from brokerledger.clients.service import create_client
from brokerledger.db.engine import session_scope
from brokerledger.db.models import MerchantRule, Transaction
from brokerledger.ingest.router import ingest_statement


def _write_demo_csv(tmp_path: Path) -> Path:
    p = tmp_path / "demo.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "10/03/2025,UNKNOWN MERCHANT XYZ,14.75,,3119.96\n",
        encoding="utf-8",
    )
    return p


def test_apply_correction_writes_json_entry(logged_in_admin, tmp_path: Path):
    client = create_client("Cache Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(Transaction.client_id == client.id)
        ).scalar_one()
        apply_correction(s, tx=tx, new_category="Entertainment", user_id=logged_in_admin.id)
        s.commit()

    cache_file = corrections_cache.cache_path()
    assert cache_file.exists()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) >= 1
    entry = next(e for e in data if e["scope"] == "client")
    assert entry["merchant"] == "UNKNOWN MERCHANT XYZ"
    assert entry["category"] == "Entertainment"
    assert entry["client_id"] == client.id
    assert "updated_at" in entry


def test_sync_into_db_imports_missing_rules(logged_in_admin, tmp_path: Path):
    # Seed the JSON cache directly, then confirm sync_into_db inserts the row.
    p = corrections_cache.cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps([
            {
                "merchant": "FOO BAR CAFE",
                "category": "Food",
                "group": "discretionary",
                "scope": "global",
                "client_id": None,
                "weight": 2,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ]),
        encoding="utf-8",
    )

    with session_scope() as s:
        before = s.execute(
            select(MerchantRule).where(
                MerchantRule.merchant_normalized == "FOO BAR CAFE",
            )
        ).scalars().all()
        assert before == []
        inserted = corrections_cache.sync_into_db(s)
        assert inserted == 1
        after = s.execute(
            select(MerchantRule).where(
                MerchantRule.merchant_normalized == "FOO BAR CAFE",
            )
        ).scalar_one()
        assert after.category == "Food"
        assert after.scope == "global"
        assert after.client_id is None
        # Running again is a no-op.
        assert corrections_cache.sync_into_db(s) == 0


def test_cache_path_respects_env_override(tmp_path: Path, monkeypatch):
    override = tmp_path / "custom" / "corrections.json"
    monkeypatch.setenv("BROKERLEDGER_CORRECTIONS_CACHE", str(override))
    assert corrections_cache.cache_path() == override.expanduser().resolve()

    corrections_cache.append(
        merchant="ROUND TRIP LTD",
        category="Food",
        group="discretionary",
        scope="client",
        client_id=42,
        weight=2,
    )
    assert override.exists()
    data = json.loads(override.read_text(encoding="utf-8"))
    assert data[0]["merchant"] == "ROUND TRIP LTD"
    assert data[0]["client_id"] == 42
