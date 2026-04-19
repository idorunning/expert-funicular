"""Password-reset requests.

Two flows are supported:

1. **Admin-mediated** (default, fully offline): a user submits a request
   from the Login screen, an admin sees it in the Admin Users view and
   resets the password in-app.
2. **Emailed code** (opt-in, activated when SMTP is configured in Settings):
   the user receives a 6-digit code by email and enters it in the app to
   pick a new password. No admin involvement.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from ..db.engine import session_scope
from ..db.models import AuditLog, PasswordResetRequest, User, utcnow
from .hashing import hash_password, verify_password
from .service import AuthError, _normalize_email, change_password

RESET_CODE_TTL_MINUTES = 15


@dataclass(frozen=True)
class ResetRequestRow:
    id: int
    user_id: int | None
    username: str | None
    email_submitted: str
    note: str | None
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: int | None


def submit_reset_request(email: str, note: str | None = None) -> int:
    """Create a pending reset request. Returns the request id.

    Does NOT reveal whether the email is registered — same behaviour whether
    or not a matching user exists, to avoid enumeration.
    """
    try:
        email_norm = _normalize_email(email) or ""
    except AuthError:
        raise AuthError("Enter a valid email address")
    if not email_norm:
        raise AuthError("Enter a valid email address")
    with session_scope() as s:
        u = s.execute(select(User).where(User.email == email_norm)).scalar_one_or_none()
        req = PasswordResetRequest(
            user_id=u.id if u is not None else None,
            email_submitted=email_norm,
            note=(note.strip() or None) if note else None,
        )
        s.add(req)
        s.commit()
        s.add(AuditLog(
            user_id=u.id if u is not None else None,
            action="password_reset_requested",
            entity_type="user",
            entity_id=u.id if u is not None else None,
            detail_json=json.dumps({"email": email_norm, "request_id": req.id}),
        ))
        s.commit()
        return req.id


def list_pending_requests() -> list[ResetRequestRow]:
    with session_scope() as s:
        rows = s.execute(
            select(PasswordResetRequest, User)
            .join(User, User.id == PasswordResetRequest.user_id, isouter=True)
            .where(PasswordResetRequest.resolved_at.is_(None))
            .order_by(PasswordResetRequest.created_at.desc())
        ).all()
        return [
            ResetRequestRow(
                id=r.id,
                user_id=r.user_id,
                username=u.username if u else None,
                email_submitted=r.email_submitted,
                note=r.note,
                created_at=r.created_at,
                resolved_at=r.resolved_at,
                resolved_by=r.resolved_by,
            )
            for r, u in rows
        ]


def resolve_request(request_id: int, new_password: str | None, actor_id: int) -> None:
    """Mark the request resolved and (if supplied) change the user's password."""
    with session_scope() as s:
        req = s.get(PasswordResetRequest, request_id)
        if req is None:
            raise AuthError("Reset request not found")
        if req.resolved_at is not None:
            raise AuthError("Reset request is already resolved")
        target_user_id = req.user_id
        req.resolved_at = utcnow()
        req.resolved_by = actor_id
        s.commit()
    if new_password and target_user_id is not None:
        change_password(target_user_id, new_password, actor_id=actor_id)


def dismiss_request(request_id: int, actor_id: int) -> None:
    """Close the request without changing a password (e.g. unknown email)."""
    with session_scope() as s:
        req = s.get(PasswordResetRequest, request_id)
        if req is None:
            raise AuthError("Reset request not found")
        if req.resolved_at is not None:
            return
        req.resolved_at = utcnow()
        req.resolved_by = actor_id
        s.add(AuditLog(
            user_id=actor_id, action="password_reset_dismissed",
            entity_type="password_reset", entity_id=request_id,
        ))
        s.commit()


def _generate_code() -> str:
    """6-digit numeric code. secrets.randbelow keeps it cryptographically random."""
    return f"{secrets.randbelow(1_000_000):06d}"


def submit_reset_code(email: str, note: str | None = None) -> tuple[int, str | None]:
    """Create a reset request and, if SMTP is configured, store a one-time code.

    Returns ``(request_id, code_or_none)``. The ``code`` is only returned for
    callers that want to send the email themselves; the caller MUST NOT show
    it to the user. If no matching account exists the code is still generated
    and recorded (with a null user_id) to preserve enumeration safety — the UI
    shows the same confirmation either way.
    """
    try:
        email_norm = _normalize_email(email) or ""
    except AuthError:
        raise AuthError("Enter a valid email address")
    if not email_norm:
        raise AuthError("Enter a valid email address")
    code = _generate_code()
    code_hash = hash_password(code)
    expires = utcnow() + timedelta(minutes=RESET_CODE_TTL_MINUTES)
    with session_scope() as s:
        u = s.execute(select(User).where(User.email == email_norm)).scalar_one_or_none()
        req = PasswordResetRequest(
            user_id=u.id if u is not None else None,
            email_submitted=email_norm,
            note=(note.strip() or None) if note else None,
            code_hash=code_hash,
            code_expires_at=expires,
        )
        s.add(req)
        s.commit()
        s.add(AuditLog(
            user_id=u.id if u is not None else None,
            action="password_reset_code_issued",
            entity_type="user",
            entity_id=u.id if u is not None else None,
            detail_json=json.dumps({"email": email_norm, "request_id": req.id}),
        ))
        s.commit()
        # Only return the real code when there's a user to email — the
        # no-match case still writes a row (for enumeration safety) but we
        # don't leak a code into a caller that might email it.
        return req.id, (code if u is not None else None)


def verify_and_reset(email: str, code: str, new_password: str) -> None:
    """Consume the most recent emailed code for ``email`` and set the password.

    Raises ``AuthError`` for unknown email, wrong code, or expired/used code.
    """
    try:
        email_norm = _normalize_email(email) or ""
    except AuthError:
        raise AuthError("Enter a valid email address")
    if not email_norm:
        raise AuthError("Enter a valid email address")
    code_str = (code or "").strip()
    if not code_str:
        raise AuthError("Enter the code from the email")
    with session_scope() as s:
        u = s.execute(select(User).where(User.email == email_norm)).scalar_one_or_none()
        if u is None:
            raise AuthError("Reset code is not valid")
        req = s.execute(
            select(PasswordResetRequest)
            .where(
                PasswordResetRequest.email_submitted == email_norm,
                PasswordResetRequest.resolved_at.is_(None),
                PasswordResetRequest.code_hash.is_not(None),
            )
            .order_by(PasswordResetRequest.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if req is None:
            raise AuthError("No outstanding reset code for that email")
        if req.code_expires_at is None or req.code_expires_at < utcnow():
            raise AuthError("Reset code has expired — request a new one")
        if not verify_password(req.code_hash or "", code_str):
            raise AuthError("Reset code is not valid")
        req.resolved_at = utcnow()
        req.resolved_by = u.id
        s.add(AuditLog(
            user_id=u.id,
            action="password_reset_by_email_code",
            entity_type="user",
            entity_id=u.id,
            detail_json=json.dumps({"request_id": req.id}),
        ))
        s.commit()
        target_user_id = u.id
    change_password(target_user_id, new_password, actor_id=target_user_id)
