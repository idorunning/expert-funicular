"""Authentication service: create_user, login, change_password."""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db.engine import session_scope
from ..db.models import AuditLog, User, utcnow
from .hashing import hash_password, needs_rehash, verify_password
from .session import CurrentUser, set_current

ROLES = ("admin", "broker")


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


def user_count() -> int:
    with session_scope() as s:
        return s.execute(select(func.count()).select_from(User)).scalar_one()


def create_user(username: str, password: str, role: str, full_name: str | None = None,
                actor_id: int | None = None) -> int:
    if role not in ROLES:
        raise AuthError(f"role must be one of {ROLES}")
    username = username.strip()
    if not username:
        raise AuthError("Username required")
    _check_password_strength(password)
    with session_scope() as s:
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            full_name=full_name,
        )
        s.add(user)
        try:
            s.commit()
        except IntegrityError as e:
            s.rollback()
            raise AuthError(f"Username {username!r} already exists") from e
        s.add(AuditLog(user_id=actor_id, action="create_user", entity_type="user", entity_id=user.id,
                       detail_json=json.dumps({"username": username, "role": role})))
        s.commit()
        return user.id


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


def login(username: str, password: str) -> CurrentUser:
    s_cfg = get_settings()
    now = utcnow()
    with session_scope() as s:
        u = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if u is None:
            # Always hash to avoid user-enumeration timing side channel
            _ = hash_password(password)
            raise InvalidCredentials("Invalid username or password")
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
            raise InvalidCredentials("Invalid username or password")
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
        )
    set_current(cu)
    return cu


def logout() -> None:
    set_current(None)
