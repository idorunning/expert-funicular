"""Audit log plumbing: logout audit row + viewer helpers."""
from __future__ import annotations

from sqlalchemy import select

from brokerledger.auth.service import create_user, login, logout
from brokerledger.auth.session import set_current
from brokerledger.clients.service import create_client
from brokerledger.db.engine import session_scope
from brokerledger.db.models import AuditLog
from brokerledger.users.service import list_audit_actions, list_audit_users


def test_logout_writes_audit_row(logged_in_admin):
    # logged_in_admin fixture logs testadmin in already.
    logout()

    with session_scope() as s:
        rows = s.execute(
            select(AuditLog)
            .where(AuditLog.action == "logout", AuditLog.user_id == logged_in_admin.id)
        ).scalars().all()
    assert len(rows) == 1
    # Log back in so the fixture teardown doesn't stumble.
    login("testadmin", "TestPassword1")


def test_logout_without_current_user_is_noop(logged_in_admin):
    set_current(None)
    # No current user: logout() should not raise and not insert a row.
    logout()
    with session_scope() as s:
        count = s.execute(
            select(AuditLog).where(AuditLog.action == "logout")
        ).scalars().all()
    # No logout row — the admin didn't actually log out in this path.
    assert len(count) == 0
    login("testadmin", "TestPassword1")


def test_list_audit_users_returns_distinct_writers(logged_in_admin):
    broker_id = create_user("auditor2", "BrokerPass1", role="broker", full_name="Audit Two")
    # Trigger several audit rows from the admin.
    create_client("Client A")
    create_client("Client B")
    # Trigger a row from the broker.
    login("auditor2", "BrokerPass1")
    create_client("Client C")
    set_current(logged_in_admin)

    users = list_audit_users()
    ids = {u.id for u in users}
    assert logged_in_admin.id in ids
    assert broker_id in ids
    # No duplicates.
    assert len(ids) == len(users)


def test_list_audit_actions_returns_distinct_sorted(logged_in_admin):
    create_client("Sample Client")
    actions = list_audit_actions()
    # Should contain at minimum the actions we've triggered.
    assert "create_client" in actions
    assert "login" in actions
    # Sorted alphabetically.
    assert actions == sorted(actions)
