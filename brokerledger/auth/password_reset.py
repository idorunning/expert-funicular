"""Admin-mediated password reset requests.

The app is fully local — there's no outbound email. Instead, a user who
forgets their password submits a request from the Login screen; an admin
sees pending requests in the Admin Users view and can reset the password
in-app (they then tell the user the new password out-of-band).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from ..db.engine import session_scope
from ..db.models import AuditLog, PasswordResetRequest, User, utcnow
from .service import AuthError, _normalize_email, change_password


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
