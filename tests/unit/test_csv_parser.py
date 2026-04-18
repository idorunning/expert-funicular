from decimal import Decimal
from pathlib import Path

from brokerledger.ingest.csv_parser import parse_csv


def test_parse_debit_credit_columns(tmp_path: Path):
    path = tmp_path / "barclays.csv"
    path.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "01/03/2025,OPENING BALANCE,,,100.00\n"
        "02/03/2025,TESCO STORES LONDON GB,12.34,,87.66\n"
        "03/03/2025,SALARY ACME,,2000.00,2087.66\n",
        encoding="utf-8",
    )
    rows = parse_csv(path)
    # Opening balance has no amount -> skipped
    assert len(rows) == 2
    debit = rows[0]
    credit = rows[1]
    assert debit.amount == Decimal("-12.34")
    assert debit.direction == "debit"
    assert "TESCO" in debit.merchant_normalized
    assert credit.amount == Decimal("2000.00")
    assert credit.direction == "credit"


def test_parse_single_amount_column(tmp_path: Path):
    path = tmp_path / "generic.csv"
    path.write_text(
        "Date,Description,Amount\n"
        "2025-03-01,NETFLIX COM,-10.99\n"
        "2025-03-02,REFUND AMAZON,25.00\n",
        encoding="utf-8",
    )
    rows = parse_csv(path)
    assert len(rows) == 2
    assert rows[0].amount == Decimal("-10.99")
    assert rows[0].direction == "debit"
    assert rows[1].amount == Decimal("25.00")
    assert rows[1].direction == "credit"


def test_pound_symbol_and_thousands(tmp_path: Path):
    path = tmp_path / "fancy.csv"
    path.write_text(
        "Date,Description,Amount\n"
        "2025-03-01,SALARY,\"£3,500.00\"\n",
        encoding="utf-8",
    )
    rows = parse_csv(path)
    assert rows[0].amount == Decimal("3500.00")
