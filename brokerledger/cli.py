"""Headless CLI: bootstrap and smoke-test the ingest + categorise pipeline.

Usage:
  python -m brokerledger --demo
  python -m brokerledger --bootstrap --username admin --password ...
"""
from __future__ import annotations

import argparse
import os
from decimal import Decimal

from . import paths
from .auth.service import create_user, login, user_count
from .auth.session import set_current
from .categorize.llm_client import FakeLLMClient
from .db.engine import init_engine
from .db.seed import run_all_seeds
from .utils.logging import configure_logging, logger


def _bootstrap(username: str, password: str) -> None:
    init_engine()
    run_all_seeds()
    if user_count() == 0:
        create_user(username, password, role="admin", full_name="Admin")
        print(f"Created admin user {username!r}")
    login(username, password)


def _demo() -> int:
    from pathlib import Path

    from .categorize.categorizer import categorize_statement
    from .clients.service import create_client
    from .ingest.router import ingest_statement

    configure_logging()
    os.environ.setdefault("BROKERLEDGER_FAKE_LLM", "1")
    init_engine()
    run_all_seeds()
    if user_count() == 0:
        create_user("demo-admin", "demo-password-1", role="admin")
    user = login("demo-admin", "demo-password-1")
    set_current(user)

    client = create_client("Demo Client")
    print(f"Created client {client.display_name} (id={client.id})")

    # Write a tiny synthetic CSV and ingest it.
    csv_path = paths.app_data_dir() / "demo_statement.csv"
    csv_path.write_text(
        "Date,Description,Debit,Credit,Balance\n"
        "01/03/2025,SALARY ACME LTD,,3500.00,3500.00\n"
        "02/03/2025,COUNCIL TAX DDR,180.00,,3320.00\n"
        "03/03/2025,TESCO STORES 1234 LONDON GB,54.30,,3265.70\n"
        "05/03/2025,OCTOPUS ENERGY DDR,120.00,,3145.70\n"
        "07/03/2025,NETFLIX COM,10.99,,3134.71\n"
        "10/03/2025,THAMES WATER DDR,42.00,,3092.71\n"
        "15/03/2025,TFL TRAVEL CH 12345,35.60,,3057.11\n"
        "20/03/2025,UNKNOWN MERCHANT XYZ,14.75,,3042.36\n",
        encoding="utf-8",
    )
    result = ingest_statement(client.id, Path(csv_path))
    print(f"Ingested {result.transaction_count} rows (kind={result.file_kind})")

    categorize_statement(result.statement_id, llm=FakeLLMClient())

    from .affordability.calculator import compute_for_client
    report = compute_for_client(client.id)
    print(f"\nAffordability report for {client.display_name}:")
    print(f"  Period:               {report.period_start} → {report.period_end}")
    print(f"  Income (total):       £{report.income_total:,.2f}")
    print(f"  Committed (total):    £{report.committed_total:,.2f}")
    print(f"  Discretionary:        £{report.discretionary_total:,.2f}")
    print(f"  Net disposable:       £{report.net_disposable:,.2f}")
    print()
    for cat, t in sorted(report.per_category.items()):
        if t.count:
            print(f"    {cat:40s}  count={t.count}  total=£{t.total:,.2f}")
    return 0


def run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="brokerledger")
    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--demo", action="store_true", help="run an end-to-end smoke test (fake LLM)")
    sub.add_argument("--cli", action="store_true", help="alias for --demo")
    sub.add_argument("--bootstrap", action="store_true", help="create the first admin user non-interactively")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password")
    args = parser.parse_args(argv)

    if args.bootstrap:
        if not args.password:
            parser.error("--password is required with --bootstrap")
        _bootstrap(args.username, args.password)
        return 0

    if args.demo or args.cli:
        return _demo()

    return 1
