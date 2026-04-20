"""SMTP helpers for outbound password-reset and test emails.

The application is otherwise fully offline; the unit test
``tests/unit/test_llm_client_local_only.py`` asserts all httpx traffic goes to
``127.0.0.1``. The SMTP path is a narrowly-scoped exception — it is only
activated when an admin has explicitly filled in SMTP settings.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from ..db import app_settings


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    starttls: bool
    username: str
    password: str
    from_addr: str


def load_config() -> SmtpConfig | None:
    host = (app_settings.get("smtp_host") or "").strip()
    if not host:
        return None
    return SmtpConfig(
        host=host,
        port=int(app_settings.get_int("smtp_port", 587) or 587),
        starttls=app_settings.get_bool("smtp_starttls", True),
        username=(app_settings.get("smtp_username") or "").strip(),
        password=app_settings.get("smtp_password") or "",
        from_addr=(app_settings.get("smtp_from") or "").strip()
        or (app_settings.get("smtp_username") or "").strip(),
    )


def is_configured() -> bool:
    return load_config() is not None


def _send(cfg: SmtpConfig, message: EmailMessage) -> None:
    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as server:
        server.ehlo()
        if cfg.starttls:
            server.starttls()
            server.ehlo()
        if cfg.username:
            server.login(cfg.username, cfg.password)
        server.send_message(message)


def send_reset_code(to_addr: str, code: str) -> None:
    cfg = load_config()
    if cfg is None:
        raise RuntimeError("SMTP is not configured")
    msg = EmailMessage()
    msg["Subject"] = "Mortgage Broker Affordability Assistant — password reset code"
    msg["From"] = cfg.from_addr
    msg["To"] = to_addr
    msg.set_content(
        "Hello,\n\n"
        "You asked to reset your Mortgage Broker Affordability Assistant "
        "password. Enter this code in the app to choose a new password:\n\n"
        f"    {code}\n\n"
        "This code expires in 15 minutes.\n\n"
        "If you did not ask for a reset, you can ignore this message."
    )
    _send(cfg, msg)


def send_test_email(to_addr: str) -> None:
    cfg = load_config()
    if cfg is None:
        raise RuntimeError("SMTP is not configured")
    msg = EmailMessage()
    msg["Subject"] = "Mortgage Broker Affordability Assistant — test email"
    msg["From"] = cfg.from_addr
    msg["To"] = to_addr
    msg.set_content(
        "This is a test email from the Mortgage Broker Affordability Assistant. "
        "If you can read it, your SMTP settings are working."
    )
    _send(cfg, msg)
