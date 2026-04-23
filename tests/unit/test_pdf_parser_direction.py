"""Regression tests for transaction direction when all three columns are present.

UK banks (Barclays, HSBC, Lloyds) export CSV/tabular PDFs with separate Debit,
Credit, and Amount columns where Amount is unsigned.  The parsers must prefer the
explicit Debit/Credit columns so the sign is correct.
"""
from decimal import Decimal
from pathlib import Path

from brokerledger.ingest.csv_parser import parse_csv


def _three_col_csv(tmp_path: Path) -> Path:
    """Barclays-style: Date, Description, Debit, Credit, Amount, Balance.

    Amount is always unsigned — sign must come from Debit/Credit column.
    """
    p = tmp_path / "barclays_three_col.csv"
    p.write_text(
        "Date,Description,Debit,Credit,Amount,Balance\n"
        "01/03/2025,TESCO STORES,50.00,,50.00,1450.00\n"
        "02/03/2025,SALARY ACME LTD,,3500.00,3500.00,4950.00\n",
        encoding="utf-8",
    )
    return p


def test_three_col_debit_is_negative(tmp_path: Path):
    rows = parse_csv(_three_col_csv(tmp_path))
    debit_row = next(r for r in rows if "TESCO" in r.description_raw)
    assert debit_row.amount == Decimal("-50.00"), (
        f"Expected -50.00 for debit row, got {debit_row.amount}"
    )
    assert debit_row.direction == "debit"


def test_three_col_credit_is_positive(tmp_path: Path):
    rows = parse_csv(_three_col_csv(tmp_path))
    credit_row = next(r for r in rows if "SALARY" in r.description_raw)
    assert credit_row.amount == Decimal("3500.00"), (
        f"Expected 3500.00 for credit row, got {credit_row.amount}"
    )
    assert credit_row.direction == "credit"


def test_single_amount_col_no_regression(tmp_path: Path):
    """NatWest-style: only an Amount column with signed values — must still work."""
    p = tmp_path / "natwest.csv"
    p.write_text(
        "Date,Description,Amount\n"
        "2025-03-01,DIRECT DEBIT COUNCIL TAX,-180.00\n"
        "2025-03-02,BACS CREDIT SALARY,2500.00\n",
        encoding="utf-8",
    )
    rows = parse_csv(p)
    assert rows[0].amount == Decimal("-180.00")
    assert rows[0].direction == "debit"
    assert rows[1].amount == Decimal("2500.00")
    assert rows[1].direction == "credit"
