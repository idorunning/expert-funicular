"""User roster + profile photo handling."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from .. import paths
from ..db.engine import session_scope
from ..db.models import AuditLog, User


@dataclass(frozen=True)
class UserRow:
    id: int
    username: str
    role: str
    full_name: str | None
    is_active: int
    photo_path: str | None


def _row(u: User) -> UserRow:
    return UserRow(
        id=u.id,
        username=u.username,
        role=u.role,
        full_name=u.full_name,
        is_active=u.is_active,
        photo_path=u.photo_path,
    )


def get_user(user_id: int) -> UserRow | None:
    with session_scope() as s:
        u = s.get(User, user_id)
        return _row(u) if u is not None else None


def list_active_users(exclude_admins: bool = False) -> list[UserRow]:
    with session_scope() as s:
        q = select(User).where(User.is_active == 1).order_by(User.username)
        if exclude_admins:
            q = q.where(User.role != "admin")
        return [_row(u) for u in s.execute(q).scalars().all()]


def list_audit_users() -> list[UserRow]:
    """Every user who has ever written an audit_log row.

    Used by the admin audit log viewer to populate its user filter dropdown.
    Includes inactive and deleted-then-recreated accounts if they still exist.
    """
    with session_scope() as s:
        q = (
            select(User)
            .where(User.id.in_(select(AuditLog.user_id).distinct()))
            .order_by(User.username)
        )
        return [_row(u) for u in s.execute(q).scalars().all()]


def list_audit_actions() -> list[str]:
    """Distinct action strings present in audit_log, alphabetically sorted."""
    with session_scope() as s:
        rows = s.execute(
            select(AuditLog.action).distinct().order_by(AuditLog.action.asc())
        ).all()
        return [r[0] for r in rows]


def set_user_photo(user_id: int, source_path: Path) -> str:
    """Copy + re-encode the image to PNG at 256x256 in avatars/ and update user.

    Returns the stored absolute path.
    """
    # Deferred import so the service module is still importable during tests
    # that don't require Qt (or run on headless CI without xcb plugins).
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    img = QImage(str(source_path))
    if img.isNull():
        raise ValueError(f"Could not read image: {source_path}")

    scaled = img.scaled(
        256, 256,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )

    paths.avatars_dir().mkdir(parents=True, exist_ok=True)
    dest = paths.avatars_dir() / f"{user_id}.png"
    if not scaled.save(str(dest), "PNG"):
        raise RuntimeError(f"Could not write avatar to {dest}")

    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise ValueError(f"User {user_id} not found")
        u.photo_path = str(dest)
        s.commit()
    return str(dest)


def clear_user_photo(user_id: int) -> None:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            return
        existing = u.photo_path
        u.photo_path = None
        s.commit()
    if existing:
        try:
            Path(existing).unlink()
        except OSError:
            pass
