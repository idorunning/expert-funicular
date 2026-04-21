"""Authentication service: create/edit/delete users, login, password management."""
from __future__ import annotations

import json
import re
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db.engine import session_scope
from ..db.models import AuditLog, User, utcnow
from .hashing import hash_password, needs_rehash, verify_password
from .session import CurrentUser, get_current, set_current

ROLES = ("admin", "broker")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    pass


class AccountLocked(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


def _check_password_strength(pw: str) -> None:
    s = get_settings()
    if len(pw) < s.password_min_length:
        raise AuthError(f"Password must be at least {s.password_min_length} characters")
    has_letter = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    if not (has_letter and has_digit):
        raise AuthError("Password must contain at least one letter and one digit")


def _normalize_email(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if not _EMAIL_RE.match(value):
        raise AuthError("Email address looks invalid")
    return value


def user_count() -> int:
    with session_scope() as s:
        return s.execute(select(func.count()).select_from(User)).scalar_one()


def create_user(
    username: str,
    password: str,
    role: str,
    full_name: str | None = None,
    email: str | None = None,
    actor_id: int | None = None,
) -> int:
    if role not in ROLES:
        raise AuthError(f"role must be one of {ROLES}")
    username = username.strip()
    if not username:
        raise AuthError("Username required")
    email_norm = _normalize_email(email)
    _check_password_strength(password)
    with session_scope() as s:
        user = User(
            username=username,
            email=email_norm,
            password_hash=hash_password(password),
            role=role,
            full_name=full_name,
        )
        s.add(user)
        try:
            s.commit()
        except IntegrityError as e:
            s.rollback()
            msg = str(e.orig).lower() if e.orig else str(e).lower()
            if "email" in msg:
                raise AuthError(f"Email {email_norm!r} is already in use") from e
            raise AuthError(f"Username {username!r} already exists") from e
        s.add(AuditLog(user_id=actor_id, action="create_user", entity_type="user", entity_id=user.id,
                       detail_json=json.dumps({"username": username, "role": role, "email": email_norm})))
        s.commit()
        return user.id


def update_user(
    user_id: int,
    *,
    username: str | None = None,
    full_name: str | None = None,
    email: str | None = None,
    role: str | None = None,
    actor_id: int | None = None,
) -> None:
    """Update mutable fields. Pass None to leave a field unchanged; email="" clears it."""
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError("User not found")
        changed: dict[str, object] = {}
        if username is not None:
            new_username = username.strip()
            if not new_username:
                raise AuthError("Username cannot be blank")
            if new_username != u.username:
                u.username = new_username
                changed["username"] = new_username
        if full_name is not None:
            cleaned = full_name.strip() or None
            if cleaned != u.full_name:
                u.full_name = cleaned
                changed["full_name"] = cleaned
        if email is not None:
            cleaned = _normalize_email(email) if email else None
            if cleaned != u.email:
                u.email = cleaned
                changed["email"] = cleaned
        if role is not None:
            if role not in ROLES:
                raise AuthError(f"role must be one of {ROLES}")
            if role != u.role:
                u.role = role
                changed["role"] = role
        if not changed:
            return
        try:
            s.commit()
        except IntegrityError as e:
            s.rollback()
            msg = str(e.orig).lower() if e.orig else str(e).lower()
            if "email" in msg:
                raise AuthError("That email address is already in use") from e
            raise AuthError("That username is already in use") from e
        s.add(AuditLog(
            user_id=actor_id, action="update_user", entity_type="user", entity_id=user_id,
            detail_json=json.dumps(changed, default=str),
        ))
        s.commit()


def delete_user(user_id: int, actor_id: int | None = None) -> None:
    """Hard-delete a user. Refuses to delete the last active admin."""
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError("User not found")
        if u.role == "admin":
            remaining = s.execute(
                select(func.count()).select_from(User).where(
                    User.role == "admin",
                    User.is_active == 1,
                    User.id != user_id,
                )
            ).scalar_one()
            if remaining == 0:
                raise AuthError("Refusing to delete the last active admin")
        snapshot = {"username": u.username, "email": u.email, "role": u.role}
        s.delete(u)
        s.add(AuditLog(
            user_id=actor_id, action="delete_user", entity_type="user", entity_id=user_id,
            detail_json=json.dumps(snapshot),
        ))
        s.commit()


def set_user_active(user_id: int, active: bool, actor_id: int | None = None) -> None:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError("User not found")
        u.is_active = 1 if active else 0
        u.failed_logins = 0
        u.locked_until = None
        s.add(AuditLog(user_id=actor_id, action="set_active", entity_type="user", entity_id=user_id,
                       detail_json=json.dumps({"active": bool(active)})))
        s.commit()


def change_password(user_id: int, new_password: str, actor_id: int | None = None) -> None:
    _check_password_strength(new_password)
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise AuthError("User not found")
        u.password_hash = hash_password(new_password)
        u.failed_logins = 0
        u.locked_until = None
        s.add(AuditLog(user_id=actor_id, action="change_password", entity_type="user", entity_id=user_id))
        s.commit()


def login(identifier: str, password: str) -> CurrentUser:
    """Authenticate by email (preferred) or username (backwards-compatible fallback)."""
    s_cfg = get_settings()
    now = utcnow()
    ident = (identifier or "").strip()
    ident_lc = ident.lower()
    with session_scope() as s:
        u = s.execute(
            select(User).where(
                or_(User.email == ident_lc, User.username == ident)
            )
        ).scalar_one_or_none()
        if u is None:
            # Always hash to avoid user-enumeration timing side channel
            _ = hash_password(password)
            raise InvalidCredentials("Invalid email or password")
        if u.is_active == 0:
            raise AuthError("Account is disabled")
        if u.locked_until is not None and u.locked_until > now:
            raise AccountLocked(f"Account locked until {u.locked_until.isoformat()}")
        if not verify_password(u.password_hash, password):
            u.failed_logins += 1
            if u.failed_logins >= s_cfg.max_failed_logins:
                u.locked_until = now + timedelta(minutes=s_cfg.lockout_minutes)
                u.failed_logins = 0
                s.add(AuditLog(user_id=u.id, action="account_locked", entity_type="user", entity_id=u.id))
            s.commit()
            raise InvalidCredentials("Invalid email or password")
        # Successful login
        u.failed_logins = 0
        u.last_login_at = now
        if needs_rehash(u.password_hash):
            u.password_hash = hash_password(password)
        s.add(AuditLog(user_id=u.id, action="login", entity_type="user", entity_id=u.id))
        s.commit()
        cu = CurrentUser(
            id=u.id,
            username=u.username,
            role=u.role,
            full_name=u.full_name,
            photo_path=u.photo_path,
            email=u.email,
        )
    set_current(cu)
    return cu


def logout() -> None:
    user = get_current()
    if user is not None:
        with session_scope() as s:
            s.add(AuditLog(
                user_id=user.id,
                action="logout",
                entity_type="user",
                entity_id=user.id,
            ))
            s.commit()
    set_current(None)
