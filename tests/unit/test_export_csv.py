"""Round-trip test for the CSV/TSV exporter."""
from __future__ import annotations

import csv
from pathlib import Path

from brokerledger.categorize.categorizer import categorize_statement
from brokerledger.categorize.llm_client import FakeLLMClient
from brokerledger.clients.service import create_client
from brokerledger.export import export_transactions_csv
from brokerledger.export.csv import COLUMNS
from brokerledger.ingest.router import ingest_statement


def _write_demo_csv(tmp_path: Path) -> Path:
    p = tmp_path / "demo.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "01/03/2025,SALARY ACME LTD,,3500.00,3500.00\n"
        "02/03/2025,COUNCIL TAX ACME BOROUGH,180.00,,3320.00\n"
        "03/03/2025,TESCO STORES LONDON GB,54.30,,3265.70\n",
        encoding="utf-8",
    )
    return p


def test_export_transactions_csv_round_trip(logged_in_admin, tmp_path: Path):
    client = create_client("Export Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    out = tmp_path / "export.csv"
    returned = export_transactions_csv(client.id, out)
    assert returned == out
    assert out.exists()

    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 3
    assert list(rows[0].keys()) == COLUMNS

    # Ordered by posted_date ascending, so SALARY (01/03) is first.
    assert rows[0]["Description"] == "SALARY ACME LTD"
    assert rows[0]["Direction"] == "credit"
    # Our ingester writes ISO posted dates as YYYY-MM-DD strings.
    assert rows[0]["Date"] == "2025-03-01"

    council = next(r for r in rows if r["Description"] == "COUNCIL TAX ACME BOROUGH")
    assert council["Category"] == "Council tax"
    assert council["Group"] == "committed"
    assert council["Direction"] == "debit"
    # Debits are stored as signed negatives; the CSV writes the raw value.
    assert council["Amount (GBP)"] == "-180.00"
    # Seeded rule → source=='rule' with high confidence, not flagged.
    assert council["Source"] == "rule"
    assert council["Needs Review"] == ""


def test_export_transactions_tsv_uses_tabs(logged_in_admin, tmp_path: Path):
    client = create_client("TSV Client")
    csv_path = _write_demo_csv(tmp_path)
    result = ingest_statement(client.id, csv_path)
    categorize_statement(result.statement_id, llm=FakeLLMClient())

    out = tmp_path / "export.tsv"
    export_transactions_csv(client.id, out, delimiter="\t")

    text = out.read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    assert "\t" in first_line
    assert "," not in first_line  # column headers have no commas
    assert first_line.split("\t") == COLUMNS
