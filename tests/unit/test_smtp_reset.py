"""SMTP password-reset flow + outbound-network policy."""
from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from brokerledger.auth.password_reset import (
    RESET_CODE_TTL_MINUTES,
    submit_reset_code,
    verify_and_reset,
)
from brokerledger.auth.service import AuthError, create_user, login
from brokerledger.db import app_settings
from brokerledger.db.engine import session_scope
from brokerledger.db.models import PasswordResetRequest, User, utcnow
from brokerledger.mail import smtp as smtp_mod


def _seed_smtp_config() -> None:
    app_settings.put("smtp_host", "smtp.example.com")
    app_settings.put("smtp_port", "587")
    app_settings.put("smtp_starttls", "1")
    app_settings.put("smtp_username", "app@example.com")
    app_settings.put("smtp_password", "s3cret")
    app_settings.put("smtp_from", "BrokerLedger <app@example.com>")


def test_smtp_is_off_when_not_configured(db_session):
    assert smtp_mod.is_configured() is False
    assert smtp_mod.load_config() is None


def test_send_reset_code_uses_starttls_login_and_send(db_session):
    _seed_smtp_config()
    fake_server = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = fake_server
    cm.__exit__.return_value = False

    with patch("smtplib.SMTP", return_value=cm) as smtp_cls:
        smtp_mod.send_reset_code("user@example.com", "123456")

    smtp_cls.assert_called_once()
    args, _ = smtp_cls.call_args
    assert args[0] == "smtp.example.com"
    assert args[1] == 587
    fake_server.starttls.assert_called_once()
    fake_server.login.assert_called_once_with("app@example.com", "s3cret")
    fake_server.send_message.assert_called_once()
    sent: EmailMessage = fake_server.send_message.call_args.args[0]
    assert "123456" in sent.get_content()
    assert sent["To"] == "user@example.com"


def test_send_reset_code_raises_when_smtp_unconfigured(db_session):
    assert not smtp_mod.is_configured()
    with pytest.raises(RuntimeError):
        smtp_mod.send_reset_code("user@example.com", "000000")


def test_verify_and_reset_accepts_correct_code(db_session):
    create_user("resetme", "OldPassword1", role="broker",
                full_name="Reset Me", email="reset@example.com")

    req_id, code = submit_reset_code("reset@example.com")
    assert code is not None and len(code) == 6 and code.isdigit()
    assert req_id > 0

    verify_and_reset("reset@example.com", code, "BrandNewPassword1")

    # Old password no longer works; new one does.
    with pytest.raises(AuthError):
        login("resetme", "OldPassword1")
    assert login("resetme", "BrandNewPassword1").username == "resetme"

    # Request is marked resolved.
    with session_scope() as s:
        req = s.get(PasswordResetRequest, req_id)
        assert req.resolved_at is not None


def test_verify_and_reset_rejects_wrong_code(db_session):
    create_user("wrongcode", "OldPassword1", role="broker",
                full_name="Wrong Code", email="wc@example.com")
    submit_reset_code("wc@example.com")
    with pytest.raises(AuthError):
        verify_and_reset("wc@example.com", "000000", "AnotherPassword1")


def test_verify_and_reset_rejects_expired_code(db_session):
    from datetime import timedelta

    create_user("expired", "OldPassword1", role="broker",
                full_name="Exp", email="exp@example.com")
    _req_id, code = submit_reset_code("exp@example.com")
    assert code is not None

    # Backdate the expiry into the past.
    with session_scope() as s:
        row = s.execute(
            PasswordResetRequest.__table__.select().order_by(
                PasswordResetRequest.id.desc()
            ).limit(1)
        ).first()
        req = s.get(PasswordResetRequest, row.id)
        req.code_expires_at = utcnow() - timedelta(minutes=RESET_CODE_TTL_MINUTES + 5)
        s.commit()

    with pytest.raises(AuthError):
        verify_and_reset("exp@example.com", code, "FreshPassword1")


def test_submit_reset_code_enumeration_safe_for_unknown_email(db_session):
    # No user for this email — should still create a request row but not leak
    # a code back to the caller (so the caller can't tell the email matched).
    req_id, code = submit_reset_code("ghost@example.com")
    assert req_id > 0
    assert code is None
    with session_scope() as s:
        req = s.get(PasswordResetRequest, req_id)
        assert req.user_id is None
        assert req.code_hash is not None  # still hashed-stored


def test_ollama_client_refuses_non_local_url():
    # Policy regression: outbound LLM traffic must be localhost-only. SMTP
    # is the only sanctioned non-local exception and is gated by app_settings.
    from brokerledger.categorize.llm_client import LLMError, OllamaClient

    with pytest.raises(LLMError):
        OllamaClient(base_url="http://example.com:11434")


def test_smtp_allowlist_requires_explicit_configuration(db_session):
    # With no smtp_host stored, no SMTP traffic should be even attempted —
    # load_config() must return None so callers raise before touching the net.
    assert app_settings.get("smtp_host") in (None, "")
    assert smtp_mod.load_config() is None
    # After an admin fills in the host, load_config() returns it verbatim;
    # this is the single sanctioned allowlist entry.
    _seed_smtp_config()
    cfg = smtp_mod.load_config()
    assert cfg is not None
    assert cfg.host == "smtp.example.com"
