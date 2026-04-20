"""Per-statement broker verification workflow."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from brokerledger.clients.service import (
    ClientError,
    count_flagged_transactions,
    create_client,
    verify_statement,
)
from brokerledger.db.engine import session_scope
from brokerledger.db.models import AuditLog, Statement, Transaction


def _seed_statement(client_id: int, imported_by: int, flagged_rows: int = 0, ok_rows: int = 2) -> int:
    import hashlib
    import uuid

    hash_ = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    with session_scope() as s:
        stmt = Statement(
            client_id=client_id,
            original_name="demo.csv",
            stored_path="/tmp/demo.csv",
            file_sha256=hash_,
            file_kind="csv",
            imported_by=imported_by,
            row_count=flagged_rows + ok_rows,
        )
        s.add(stmt)
        s.flush()
        for i in range(flagged_rows):
            s.add(Transaction(
                statement_id=stmt.id,
                client_id=client_id,
                posted_date="2025-01-01",
                description_raw=f"FLAGGED {i}",
                merchant_normalized=f"FLAG{i}",
                amount=10,
                direction="debit",
                needs_review=1,
            ))
        for i in range(ok_rows):
            s.add(Transaction(
                statement_id=stmt.id,
                client_id=client_id,
                posted_date="2025-01-02",
                description_raw=f"OK {i}",
                merchant_normalized=f"OK{i}",
                amount=20,
                direction="debit",
                needs_review=0,
            ))
        s.commit()
        return stmt.id


def test_verify_happy_path(logged_in_admin):
    client = create_client("Verify Me")
    stmt_id = _seed_statement(client.id, logged_in_admin.id, flagged_rows=0, ok_rows=3)

    assert count_flagged_transactions(stmt_id) == 0

    result = verify_statement(stmt_id)

    assert result.verified_by == logged_in_admin.id
    assert result.verified_by_username == "testadmin"
    assert result.verified_at is not None

    with session_scope() as s:
        stmt = s.get(Statement, stmt_id)
        assert stmt.verified_at is not None
        assert stmt.verified_by == logged_in_admin.id
        audits = s.execute(
            select(AuditLog).where(
                AuditLog.action == "verify_statement",
                AuditLog.entity_id == stmt_id,
            )
        ).scalars().all()
        assert len(audits) == 1


def test_verify_blocked_by_flagged_rows(logged_in_admin):
    client = create_client("Blocked")
    stmt_id = _seed_statement(client.id, logged_in_admin.id, flagged_rows=2, ok_rows=3)

    assert count_flagged_transactions(stmt_id) == 2

    with pytest.raises(ClientError, match="still need review"):
        verify_statement(stmt_id)

    with session_scope() as s:
        stmt = s.get(Statement, stmt_id)
        assert stmt.verified_at is None
        assert stmt.verified_by is None


def test_verify_unblocks_after_clearing_flags(logged_in_admin):
    client = create_client("Clears")
    stmt_id = _seed_statement(client.id, logged_in_admin.id, flagged_rows=1, ok_rows=1)

    with pytest.raises(ClientError):
        verify_statement(stmt_id)

    # Clear the flag.
    with session_scope() as s:
        tx = s.execute(
            select(Transaction).where(
                Transaction.statement_id == stmt_id,
                Transaction.needs_review == 1,
            )
        ).scalars().first()
        tx.needs_review = 0
        s.commit()

    # Now it should go through.
    verify_statement(stmt_id)


def test_verify_idempotent_refreshes_timestamp(logged_in_admin):
    client = create_client("Twice")
    stmt_id = _seed_statement(client.id, logged_in_admin.id, flagged_rows=0, ok_rows=1)

    first = verify_statement(stmt_id)
    second = verify_statement(stmt_id)

    assert second.verified_at >= first.verified_at

    with session_scope() as s:
        audits = s.execute(
            select(AuditLog).where(
                AuditLog.action == "verify_statement",
                AuditLog.entity_id == stmt_id,
            )
        ).scalars().all()
        # Both verify calls should log.
        assert len(audits) == 2


def test_count_flagged_is_scoped_to_statement(logged_in_admin):
    client = create_client("Scoped")
    stmt_a = _seed_statement(client.id, logged_in_admin.id, flagged_rows=2, ok_rows=0)
    stmt_b = _seed_statement(client.id, logged_in_admin.id, flagged_rows=0, ok_rows=1)

    assert count_flagged_transactions(stmt_a) == 2
    assert count_flagged_transactions(stmt_b) == 0
