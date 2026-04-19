"""Client CRUD + archive/restore/delete/reassign."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from brokerledger.auth.service import create_user, login
from brokerledger.auth.session import set_current
from brokerledger.clients.service import (
    ClientError,
    archive_client,
    create_client,
    delete_client,
    get_client,
    list_clients,
    reassign_client,
    rename_client,
    restore_client,
)
from brokerledger.db.engine import session_scope
from brokerledger.db.models import AuditLog, Client, Statement, Transaction


def test_rename_client(logged_in_admin):
    c = create_client("Old Name")
    updated = rename_client(c.id, "New Name")
    assert updated.display_name == "New Name"
    assert get_client(c.id).display_name == "New Name"


def test_archive_then_restore(logged_in_admin):
    c = create_client("Archivable")
    archive_client(c.id)
    # Default list excludes archived.
    assert not any(r.id == c.id for r in list_clients())
    # Including archived surfaces it again with archived_at populated.
    with_arch = [r for r in list_clients(include_archived=True) if r.id == c.id]
    assert len(with_arch) == 1
    assert with_arch[0].archived_at is not None
    restored = restore_client(c.id)
    assert restored.archived_at is None
    assert any(r.id == c.id for r in list_clients())


def test_delete_client_cascades(logged_in_admin):
    c = create_client("Doomed Client")
    # Insert a statement + transaction directly so we can verify cascade.
    with session_scope() as s:
        stmt = Statement(
            client_id=c.id,
            original_name="fake.csv",
            stored_path="/tmp/fake.csv",
            file_sha256="0" * 64,
            file_kind="csv",
            imported_by=logged_in_admin.id,
        )
        s.add(stmt)
        s.flush()
        s.add(Transaction(
            statement_id=stmt.id,
            client_id=c.id,
            posted_date="2025-01-01",
            description_raw="FAKE",
            merchant_normalized="FAKE",
            amount=10,
            direction="debit",
        ))
        s.commit()
        stmt_id = stmt.id

    delete_client(c.id)

    with session_scope() as s:
        assert s.get(Client, c.id) is None
        assert s.get(Statement, stmt_id) is None
        remaining = s.execute(
            select(Transaction).where(Transaction.client_id == c.id)
        ).scalars().all()
        assert remaining == []


def test_delete_client_requires_admin(logged_in_admin):
    # Create a broker user, then log them in and try to delete.
    create_user("broker1", "BrokerPass1", role="broker", full_name="Broker One")
    c = create_client("Held Client")
    login("broker1", "BrokerPass1")
    try:
        with pytest.raises(PermissionError):
            delete_client(c.id)
    finally:
        set_current(logged_in_admin)


def test_reassign_client(logged_in_admin):
    broker_id = create_user("broker2", "BrokerPass1", role="broker", full_name="Broker Two")
    c = create_client("Reassignable")
    result = reassign_client(c.id, broker_id)
    assert result.id == c.id
    # Verify DB reflects the change.
    with session_scope() as s:
        row = s.get(Client, c.id)
        assert row.created_by == broker_id
        # Audit row with from/to.
        audits = s.execute(
            select(AuditLog).where(
                AuditLog.action == "reassign_client",
                AuditLog.entity_id == c.id,
            )
        ).scalars().all()
        assert len(audits) == 1
        detail = json.loads(audits[0].detail_json)
        assert detail["from"] == logged_in_admin.id
        assert detail["to"] == broker_id


def test_reassign_client_requires_admin(logged_in_admin):
    create_user("broker3", "BrokerPass1", role="broker")
    c = create_client("No Reassign Without Admin")
    login("broker3", "BrokerPass1")
    try:
        with pytest.raises(PermissionError):
            reassign_client(c.id, logged_in_admin.id)
    finally:
        set_current(logged_in_admin)


def test_reassign_client_rejects_inactive_user(logged_in_admin):
    from brokerledger.auth.service import set_user_active

    broker_id = create_user("inactive_broker", "BrokerPass1", role="broker")
    set_user_active(broker_id, False)
    c = create_client("Target Client")
    with pytest.raises(ClientError):
        reassign_client(c.id, broker_id)
