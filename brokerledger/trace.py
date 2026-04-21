"""Terminal trace for the categorisation pipeline.

Run it to watch the LLM categorise a bank statement line-by-line, without
touching your real BrokerLedger database::

    python -m brokerledger.trace ~/Downloads/statement.csv
    python -m brokerledger.trace --web statement.pdf      # allow web lookup
    python -m brokerledger.trace --fake-llm statement.csv # no Ollama needed

The trace uses a throwaway app-home (tempdir + fresh SQLite DB) so your
production data is never written to. Each transaction prints a block:

    [NN/TT] date  amount  description
            [normalise] raw=... -> merchant=...
            [register ] miss | hit(...)
            [llm call ] model=... prompt=... tokens few_shot=N
            [thinking ] "..."
            [web      ] lookup enabled | hint=...
            [decision ] source=... category=... confidence=...
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

# BROKERLEDGER_TRACE stream is set up by configure_logging, but the trace
# CLI prints its own structured blocks to stdout directly so the broker
# sees one clean block per transaction.


def _set_app_home(tmp: Path) -> None:
    os.environ["BROKERLEDGER_HOME"] = str(tmp)


def _set_web_enabled(value: bool) -> None:
    from .db import app_settings
    app_settings.put("llm_web_search_enabled", "1" if value else "0")


class _TracingLLM:
    """Wraps a real ``LLMClient`` and prints every call to stdout."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def classify(self, *, description_raw, merchant_normalized, amount,
                 direction, posted_date, few_shot, web_hint=None):
        self.calls += 1
        marker = "llm call " if not web_hint else "llm retry"
        print(f"        [{marker}] few_shot={len(few_shot)} "
              f"web_hint={'yes' if web_hint else 'no'}")
        result = self._inner.classify(
            description_raw=description_raw,
            merchant_normalized=merchant_normalized,
            amount=amount, direction=direction, posted_date=posted_date,
            few_shot=few_shot, web_hint=web_hint,
        )
        thinking = (getattr(result, "thinking", "") or "").replace("\n", " ")
        if thinking:
            print(f"        [thinking ] {thinking[:240]}"
                  f"{'...' if len(thinking) > 240 else ''}")
        else:
            print("        [thinking ] (none — model didn't return a thinking trace)")
        return result


def _print_tx_header(idx: int, total: int, tx) -> None:
    amt = tx.amount
    sign = "+" if amt > 0 else ""
    print(f"[{idx:02}/{total:02}] {tx.posted_date}  "
          f"{sign}{amt}  {tx.description_raw}")


def _print_register_state(tx, exact_hit, fuzzy_top) -> None:
    merch = tx.merchant_normalized or "(empty)"
    print(f"        [normalise] raw={tx.description_raw!r} -> merchant={merch!r}")
    if exact_hit is not None:
        print(f"        [register ] exact hit scope={exact_hit.scope} "
              f"weight={exact_hit.weight} cat={exact_hit.category!r}")
    elif fuzzy_top is not None:
        print(f"        [register ] fuzzy top score={fuzzy_top.score:.0f} "
              f"cat={fuzzy_top.category!r}")
    else:
        print("        [register ] miss")


def _print_decision(decision) -> None:
    conf = decision.confidence or 0.0
    flagged = "flagged" if decision.needs_review else "auto-accepted"
    print(f"        [decision ] source={decision.source} "
          f"category={decision.category!r} confidence={conf:.2f} {flagged}")
    if decision.reason:
        print(f"        [reason   ] {decision.reason[:200]}")


def _run_trace(path: Path, *, web: bool, fake_llm: bool, client_name: str) -> int:
    # Late imports so the BROKERLEDGER_HOME env var is honoured.
    from .auth.service import create_user, login
    from .categorize import categorizer
    from .categorize.llm_client import FakeLLMClient, get_llm_client
    from .categorize.rules import find_exact, fuzzy_topk
    from .clients.service import create_client
    from .config import reset_settings_for_tests
    from .db import engine as db_engine
    from .db.models import Transaction
    from .db.seed import run_all_seeds
    from .ingest.router import ingest_statement

    reset_settings_for_tests()
    if fake_llm:
        os.environ["BROKERLEDGER_FAKE_LLM"] = "1"
    db_engine.reset_for_tests()
    db_engine.init_engine()
    run_all_seeds()
    _set_web_enabled(web)

    create_user("tracer", "TracePassword1!", role="admin", full_name="Tracer")
    login("tracer", "TracePassword1!")

    client = create_client(client_name)
    print(f"[ingest   ] {path.name} -> client {client.display_name!r}")
    result = ingest_statement(client.id, path)
    if result.duplicate:
        print(f"[ingest   ] (duplicate) statement_id={result.statement_id}")
    else:
        print(f"[ingest   ] {result.transaction_count} row(s) parsed "
              f"(kind={result.file_kind})")

    base_llm = FakeLLMClient() if fake_llm else get_llm_client()
    tracing_llm = _TracingLLM(base_llm)

    # We want the register state BEFORE the decision is recorded, so iterate
    # the txs ourselves and call `_decide` directly.
    with db_engine.session_scope() as s:
        txs = s.execute(
            Transaction.__table__.select().where(
                Transaction.statement_id == result.statement_id
            )
        ).fetchall()
    total = len(txs)
    # Reload ORM rows for _decide inside a fresh session.
    with db_engine.session_scope() as s:
        from sqlalchemy import select
        orm_txs = s.execute(
            select(Transaction).where(Transaction.statement_id == result.statement_id)
        ).scalars().all()
        for idx, tx in enumerate(orm_txs, start=1):
            _print_tx_header(idx, total, tx)
            merchant = tx.merchant_normalized
            exact = find_exact(s, merchant, tx.client_id)
            top = None
            if exact is None:
                fz = fuzzy_topk(s, merchant, k=1)
                top = fz[0] if fz else None
            _print_register_state(tx, exact, top)
            if web:
                print("        [web      ] lookup enabled (fires on low-conf)")
            decision = categorizer._decide(
                s,
                merchant=merchant,
                description_raw=tx.description_raw,
                amount=tx.amount,
                direction=tx.direction,
                posted_date=tx.posted_date,
                client_id=tx.client_id,
                llm=tracing_llm,
            )
            _print_decision(decision)
        s.rollback()  # we don't need to persist — this is a read-only trace

    print(f"\n[summary  ] total txs={total}  llm calls={tracing_llm.calls}")
    return 0


def _parse_args(argv: list[str] | None):
    p = argparse.ArgumentParser(
        prog="python -m brokerledger.trace",
        description=(
            "Run the categorisation pipeline against a statement file and "
            "print the AI's reasoning, register hits, and web-lookup calls "
            "to stdout. Uses a throwaway database — nothing persists."
        ),
    )
    p.add_argument("path", type=Path,
                   help="Path to a CSV, XLSX, or PDF statement.")
    p.add_argument("--web", action="store_true",
                   help="Enable the merchant web-lookup fallback for this run.")
    p.add_argument("--fake-llm", action="store_true",
                   help="Use the deterministic FakeLLMClient (no Ollama needed).")
    p.add_argument("--client", default="Trace Client",
                   help="Display name for the throwaway client.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.path.is_file():
        print(f"error: file not found: {args.path}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="brokerledger-trace-") as td:
        _set_app_home(Path(td))
        return _run_trace(args.path.resolve(), web=args.web,
                          fake_llm=args.fake_llm, client_name=args.client)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
