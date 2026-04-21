import pytest

from brokerledger.auth.service import (
    AccountLocked,
    AuthError,
    InvalidCredentials,
    change_password,
    create_user,
    login,
    user_count,
)


def test_password_strength_rejected(db_session):
    with pytest.raises(AuthError):
        create_user("bob", "short", role="broker")
    with pytest.raises(AuthError):
        create_user("bob", "nodigitshere", role="broker")


def test_create_and_login(db_session):
    create_user("bob", "SecurePw0rd", role="broker", full_name="Bob")
    assert user_count() == 1
    u = login("bob", "SecurePw0rd")
    assert u.username == "bob"
    assert u.role == "broker"


def test_wrong_password_fails(db_session):
    create_user("alice", "SecurePw0rd", role="broker")
    with pytest.raises(InvalidCredentials):
        login("alice", "wrong-password")


def test_lockout_after_repeated_failures(db_session, monkeypatch):
    # Tighten the limit for the test.
    from brokerledger.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "max_failed_logins", 3)

    create_user("carol", "SecurePw0rd", role="broker")
    for _ in range(3):
        with pytest.raises(InvalidCredentials):
            login("carol", "wrong")
    # Next attempt should be locked even with correct password.
    with pytest.raises(AccountLocked):
        login("carol", "SecurePw0rd")


def test_change_password(db_session):
    create_user("dave", "SecurePw0rd", role="broker")
    u = login("dave", "SecurePw0rd")
    change_password(u.id, "NewerPw0rd", actor_id=u.id)
    with pytest.raises(InvalidCredentials):
        login("dave", "SecurePw0rd")
    assert login("dave", "NewerPw0rd").username == "dave"
