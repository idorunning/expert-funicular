from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from brokerledger.categorize.categorizer import categorize_statement, recategorize_client
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
        "01/03/2025,SALARY ACME LTD,,3500.00,3500.00\n"
        "02/03/2025,COUNCIL TAX ACME BOROUGH,180.00,,3320.00\n"
        "03/03/2025,TESCO STORES LONDON GB,54.30,,3265.70\n"
        "05/03/2025,OCTOPUS ENERGY DDR,120.00,,3145.70\n"
        "07/03/2025,NETFLIX COM,10.99,,3134.71\n"
        "10/03/2025,UNKNOWN MERCHANT XYZ,14.75,,3119.96\n",
        encoding="utf-8",
    )
    return p


def test_end_to_end_ingest_and_categorise(logged_in_admin, tmp_path: Path):
    client = create_client("Test Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    assert result.transaction_count == 6
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        txs = s.execute(
            select(Transaction).where(Transaction.client_id == client.id)
        ).scalars().all()
    by_desc = {t.description_raw: t for t in txs}

    # Seeded rule: COUNCIL TAX and TESCO, OCTOPUS ENERGY are bootstrap rules.
    assert by_desc["COUNCIL TAX ACME BOROUGH"].category == "Council tax"
    assert by_desc["COUNCIL TAX ACME BOROUGH"].source == "rule"
    assert by_desc["TESCO STORES LONDON GB"].category == "Food"
    assert by_desc["OCTOPUS ENERGY DDR"].category == "Electricity / Gas / Oil"

    # Credits default to income.
    assert by_desc["SALARY ACME LTD"].category_group == "income"

    # Unknown merchant -> flagged for review.
    unk = by_desc["UNKNOWN MERCHANT XYZ"]
    assert unk.needs_review == 1


def test_correction_creates_rule_and_classifies_future_rows(logged_in_admin, tmp_path: Path):
    client = create_client("Learning Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        unk = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
        apply_correction(s, tx=unk, new_category="Entertainment", user_id=logged_in_admin.id)
        s.commit()

    # A client-scope rule was created.
    with session_scope() as s:
        rules = s.execute(
            select(MerchantRule).where(
                MerchantRule.merchant_normalized == unk.merchant_normalized,
                MerchantRule.scope == "client",
            )
        ).scalars().all()
    assert len(rules) == 1
    assert rules[0].category == "Entertainment"
    assert rules[0].weight >= 2

    # Import the same CSV again for a new client -> rule is still client-scoped
    # so the unknown will still be flagged. But a second correction on a
    # different client should push weight up.
    client2 = create_client("Another Client")
    csv_path2 = tmp_path / "demo2.csv"
    csv_path2.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")
    result2 = ingest_statement(client2.id, csv_path2)
    categorize_statement(result2.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        unk2 = s.execute(
            select(Transaction).where(
                Transaction.client_id == client2.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
        apply_correction(s, tx=unk2, new_category="Entertainment", user_id=logged_in_admin.id)
        s.commit()

    # Create a 3rd client and correct again → promotion to global should fire.
    client3 = create_client("Third Client")
    csv_path3 = tmp_path / "demo3.csv"
    csv_path3.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")
    result3 = ingest_statement(client3.id, csv_path3)
    categorize_statement(result3.statement_id, llm=FakeLLMClient())
    with session_scope() as s:
        unk3 = s.execute(
            select(Transaction).where(
                Transaction.client_id == client3.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
        apply_correction(s, tx=unk3, new_category="Entertainment", user_id=logged_in_admin.id)
        s.commit()

        global_rules = s.execute(
            select(MerchantRule).where(
                MerchantRule.merchant_normalized == unk3.merchant_normalized,
                MerchantRule.scope == "global",
            )
        ).scalars().all()
    assert len(global_rules) == 1
    assert global_rules[0].category == "Entertainment"


def test_recategorize_client_skips_user_corrections(logged_in_admin, tmp_path: Path):
    client = create_client("Recat Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    # User corrects the unknown merchant to an arbitrary category.
    with session_scope() as s:
        unk = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "UNKNOWN MERCHANT XYZ",
            )
        ).scalar_one()
        apply_correction(s, tx=unk, new_category="Entertainment", user_id=logged_in_admin.id)
        s.commit()
        unk_id = unk.id

    # Count rows eligible for re-categorisation (i.e. source != 'user').
    with session_scope() as s:
        eligible = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.source != "user",
            )
        ).scalars().all()
        expected = len(eligible)

    updated = recategorize_client(client.id, llm=FakeLLMClient())
    assert updated == expected

    # The user-corrected row must retain its category and source.
    with session_scope() as s:
        tx = s.get(Transaction, unk_id)
    assert tx.source == "user"
    assert tx.category == "Entertainment"
    assert tx.needs_review == 0


def test_recategorize_client_returns_zero_when_all_user_corrected(logged_in_admin, tmp_path: Path):
    client = create_client("All Corrected Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        txs = s.execute(
            select(Transaction).where(Transaction.client_id == client.id)
        ).scalars().all()
        for tx in txs:
            apply_correction(s, tx=tx, new_category="Entertainment", user_id=logged_in_admin.id)
        s.commit()

    assert recategorize_client(client.id, llm=FakeLLMClient()) == 0


def test_recategorize_client_returns_zero_for_no_transactions(logged_in_admin):
    client = create_client("Empty Client")
    assert recategorize_client(client.id, llm=FakeLLMClient()) == 0


def _write_risk_csv(tmp_path: Path) -> Path:
    """Statement containing a gambling brand and a Faster Payment to a person."""
    p = tmp_path / "risky.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "02/03/2025,BET365 DEP,50.00,,1000.00\n"
        "03/03/2025,FASTER PAYMENT JOHN SMITH,75.00,,925.00\n"
        "04/03/2025,TESCO STORES LONDON GB,12.40,,912.60\n",
        encoding="utf-8",
    )
    return p


def test_gambling_is_flagged(logged_in_admin, tmp_path: Path):
    client = create_client("Risk Gambling Client")
    csv_path = _write_risk_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        row = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "BET365 DEP",
            )
        ).scalar_one()
    assert row.category == "Gambling"
    assert row.category_group == "discretionary"
    assert row.needs_review == 1
    assert row.confidence is not None and row.confidence <= 0.84


def test_fast_payments_is_flagged(logged_in_admin, tmp_path: Path):
    client = create_client("Risk P2P Client")
    csv_path = _write_risk_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    with session_scope() as s:
        row = s.execute(
            select(Transaction).where(
                Transaction.client_id == client.id,
                Transaction.description_raw == "FASTER PAYMENT JOHN SMITH",
            )
        ).scalar_one()
    assert row.category == "Fast payments / person-to-person"
    assert row.needs_review == 1
    assert row.confidence is not None and row.confidence <= 0.84


def test_thresholds_from_app_settings_override_env(logged_in_admin):
    from brokerledger.config import THRESHOLD_DEFAULTS, get_threshold
    from brokerledger.db import app_settings

    assert get_threshold("fuzzy_high") == THRESHOLD_DEFAULTS["fuzzy_high"]
    assert get_threshold("llm_confidence_threshold") == THRESHOLD_DEFAULTS["llm_confidence_threshold"]

    app_settings.put("fuzzy_high", "77")
    app_settings.put("llm_confidence_threshold", "0.42")
    try:
        assert get_threshold("fuzzy_high") == 77
        assert abs(get_threshold("llm_confidence_threshold") - 0.42) < 1e-9
    finally:
        app_settings.delete("fuzzy_high")
        app_settings.delete("llm_confidence_threshold")
