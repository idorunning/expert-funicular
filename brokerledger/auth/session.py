"""In-process current-user context."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str
    full_name: str | None
    photo_path: str | None = None
    email: str | None = None


_current: ContextVar[CurrentUser | None] = ContextVar("current_user", default=None)


def set_current(user: CurrentUser | None) -> None:
    _current.set(user)


def get_current() -> CurrentUser | None:
    return _current.get()


def require_admin() -> CurrentUser:
    u = get_current()
    if u is None or u.role != "admin":
        raise PermissionError("Admin privileges required")
    return u


def require_login() -> CurrentUser:
    u = get_current()
    if u is None:
        raise PermissionError("Login required")
    return u


def can_manage_user(actor: CurrentUser, target_role: str, target_id: int) -> bool:
    """Authorisation check for staff-management operations.

    * ``admin``    — can manage anyone, including other admins and brokers.
    * ``broker``   — can only manage ``admin_staff`` users who are currently
                     allocated to them via ``AdminBrokerAssignment``.
    * ``admin_staff`` — cannot manage users.
    """
    if actor.role == "admin":
        return True
    if actor.role == "broker":
        if target_role != "admin_staff":
            return False
        from ..db.engine import session_scope
        from ..db.models import AdminBrokerAssignment
        from sqlalchemy import select
        with session_scope() as s:
            row = s.execute(
                select(AdminBrokerAssignment).where(
                    AdminBrokerAssignment.admin_user_id == target_id,
                    AdminBrokerAssignment.broker_user_id == actor.id,
                )
            ).scalar_one_or_none()
            return row is not None
    return False


def require_manageable_user(target_role: str, target_id: int) -> CurrentUser:
    """Raise ``PermissionError`` unless the current user can manage ``target``."""
    actor = require_login()
    if not can_manage_user(actor, target_role, target_id):
        raise PermissionError(
            "You don't have permission to manage that user."
        )
    return actor
