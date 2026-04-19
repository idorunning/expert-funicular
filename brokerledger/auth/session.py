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
