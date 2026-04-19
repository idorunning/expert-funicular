"""Tests for admin-mediated user CRUD, email login, and password-reset requests."""
from __future__ import annotations

import pytest

from brokerledger.auth.password_reset import (
    dismiss_request,
    list_pending_requests,
    resolve_request,
    submit_reset_request,
)
from brokerledger.auth.service import (
    AuthError,
    InvalidCredentials,
    create_user,
    delete_user,
    login,
    update_user,
    user_count,
)
from brokerledger.db import engine as db_engine
from brokerledger.db.models import User
from sqlalchemy import select


def test_create_user_with_email(db_session):
    uid = create_user("bob", "SecurePw0rd", role="broker", email="BOB@Example.COM")
    with db_engine.session_scope() as s:
        u = s.get(User, uid)
        assert u.email == "bob@example.com"


def test_login_by_email(db_session):
    create_user("bob", "SecurePw0rd", role="broker", email="bob@example.com")
    u = login("bob@example.com", "SecurePw0rd")
    assert u.username == "bob"
    assert u.email == "bob@example.com"


def test_login_by_username_still_works(db_session):
    create_user("bob", "SecurePw0rd", role="broker", email="bob@example.com")
    u = login("bob", "SecurePw0rd")
    assert u.username == "bob"


def test_email_must_be_valid(db_session):
    with pytest.raises(AuthError):
        create_user("x", "SecurePw0rd", role="broker", email="not-an-email")


def test_duplicate_email_rejected(db_session):
    create_user("a", "SecurePw0rd", role="broker", email="dup@example.com")
    with pytest.raises(AuthError):
        create_user("b", "SecurePw0rd", role="broker", email="dup@example.com")


def test_update_user_changes_email_and_username(db_session):
    uid = create_user("bob", "SecurePw0rd", role="broker", email="bob@example.com")
    update_user(uid, username="bobby", email="bobby@example.com", full_name="Bobby")
    with db_engine.session_scope() as s:
        u = s.get(User, uid)
        assert u.username == "bobby"
        assert u.email == "bobby@example.com"
        assert u.full_name == "Bobby"


def test_update_user_clears_email_with_empty_string(db_session):
    uid = create_user("bob", "SecurePw0rd", role="broker", email="bob@example.com")
    update_user(uid, email="")
    with db_engine.session_scope() as s:
        assert s.get(User, uid).email is None


def test_update_user_role(db_session):
    uid = create_user("bob", "SecurePw0rd", role="broker")
    update_user(uid, role="admin")
    with db_engine.session_scope() as s:
        assert s.get(User, uid).role == "admin"


def test_delete_user(db_session):
    # Two admins so we can delete one.
    create_user("admin1", "SecurePw0rd", role="admin")
    admin2 = create_user("admin2", "SecurePw0rd", role="admin")
    delete_user(admin2)
    with db_engine.session_scope() as s:
        assert s.get(User, admin2) is None


def test_delete_last_admin_rejected(db_session):
    admin_id = create_user("admin", "SecurePw0rd", role="admin")
    with pytest.raises(AuthError):
        delete_user(admin_id)
    assert user_count() == 1


def test_password_reset_request_known_email(db_session):
    create_user("bob", "SecurePw0rd", role="broker", email="bob@example.com")
    rid = submit_reset_request("bob@example.com", note="locked out")
    pending = list_pending_requests()
    assert any(p.id == rid and p.username == "bob" for p in pending)


def test_password_reset_request_unknown_email_still_succeeds(db_session):
    # Enumeration safety: the call must succeed and record the request.
    rid = submit_reset_request("ghost@example.com")
    pending = list_pending_requests()
    assert any(p.id == rid and p.username is None for p in pending)


def test_resolve_reset_request_sets_new_password(db_session, logged_in_admin):
    create_user("bob", "SecurePw0rd", role="broker", email="bob@example.com")
    rid = submit_reset_request("bob@example.com")
    resolve_request(rid, "BrandNewPw1", actor_id=logged_in_admin.id)
    # Old password must now fail; new password works.
    with pytest.raises(InvalidCredentials):
        login("bob@example.com", "SecurePw0rd")
    u = login("bob@example.com", "BrandNewPw1")
    assert u.username == "bob"
    # Request is no longer pending.
    assert all(p.id != rid for p in list_pending_requests())


def test_dismiss_reset_request(db_session, logged_in_admin):
    rid = submit_reset_request("someone@example.com")
    dismiss_request(rid, actor_id=logged_in_admin.id)
    assert all(p.id != rid for p in list_pending_requests())


def test_resolve_already_resolved_raises(db_session, logged_in_admin):
    create_user("bob", "SecurePw0rd", role="broker", email="bob@example.com")
    rid = submit_reset_request("bob@example.com")
    resolve_request(rid, "BrandNewPw1", actor_id=logged_in_admin.id)
    with pytest.raises(AuthError):
        resolve_request(rid, "OtherPw1", actor_id=logged_in_admin.id)
