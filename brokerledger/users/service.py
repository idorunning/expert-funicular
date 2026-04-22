"""User roster + profile photo handling."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from .. import paths
from ..auth.session import CurrentUser, require_admin, require_login
from ..db.engine import session_scope
from ..db.models import AdminBrokerAssignment, AuditLog, User, utcnow


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


# ── Admin-staff ↔ broker allocations ─────────────────────────────────────────


def list_brokers() -> list[UserRow]:
    """Every active user whose role is 'broker' — the targets of an allocation."""
    with session_scope() as s:
        q = (
            select(User)
            .where(User.is_active == 1, User.role == "broker")
            .order_by(User.username)
        )
        return [_row(u) for u in s.execute(q).scalars().all()]


def get_admin_broker_ids(admin_user_id: int) -> list[int]:
    """Return broker user-ids currently allocated to ``admin_user_id``."""
    with session_scope() as s:
        rows = s.execute(
            select(AdminBrokerAssignment.broker_user_id).where(
                AdminBrokerAssignment.admin_user_id == admin_user_id
            )
        ).all()
        return sorted(r[0] for r in rows)


def list_manageable_users(actor: CurrentUser | None = None) -> list[UserRow]:
    """Users that ``actor`` (default: current user) is allowed to manage.

    * Admins see every user.
    * Brokers see only the admin_staff users currently allocated to them.
    * Admin staff see nobody (the management UI is hidden for them anyway).
    """
    if actor is None:
        actor = require_login()
    with session_scope() as s:
        if actor.role == "admin":
            q = select(User).order_by(User.username)
        elif actor.role == "broker":
            q = (
                select(User)
                .join(
                    AdminBrokerAssignment,
                    AdminBrokerAssignment.admin_user_id == User.id,
                )
                .where(
                    AdminBrokerAssignment.broker_user_id == actor.id,
                    User.role == "admin_staff",
                )
                .order_by(User.username)
            )
        else:
            return []
        return [_row(u) for u in s.execute(q).scalars().all()]


def allocate_admin_staff_to_broker(admin_user_id: int, broker_id: int) -> None:
    """Idempotently record that ``admin_user_id`` supports ``broker_id``.

    Used immediately after a broker creates an admin_staff account so the new
    user is already scoped to the creating broker's clients.  Safe to call on
    an allocation that already exists.
    """
    require_login()
    with session_scope() as s:
        admin = s.get(User, admin_user_id)
        broker = s.get(User, broker_id)
        if admin is None or admin.role != "admin_staff":
            raise ValueError("Target user is not admin_staff.")
        if broker is None or broker.role != "broker":
            raise ValueError("Target broker is not a broker.")
        existing = s.execute(
            select(AdminBrokerAssignment).where(
                AdminBrokerAssignment.admin_user_id == admin_user_id,
                AdminBrokerAssignment.broker_user_id == broker_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        s.add(AdminBrokerAssignment(
            admin_user_id=admin_user_id,
            broker_user_id=broker_id,
            created_at=utcnow(),
        ))
        s.commit()


def set_admin_broker_ids(admin_user_id: int, broker_ids: list[int]) -> None:
    """Replace the broker allocations for an admin-staff user. Admin-only."""
    actor = require_admin()
    broker_ids_set = {int(b) for b in broker_ids if int(b) != admin_user_id}
    with session_scope() as s:
        target = s.get(User, admin_user_id)
        if target is None:
            raise ValueError(f"User {admin_user_id} not found")
        if target.role != "admin_staff":
            raise ValueError(
                "Broker allocations only apply to users with role 'admin_staff'."
            )
        if broker_ids_set:
            rows = s.execute(
                select(User.id).where(
                    User.id.in_(broker_ids_set),
                    User.role == "broker",
                    User.is_active == 1,
                )
            ).all()
            found = {r[0] for r in rows}
            missing = broker_ids_set - found
            if missing:
                raise ValueError(f"Broker user(s) not found or inactive: {sorted(missing)}")

        existing = s.execute(
            select(AdminBrokerAssignment).where(
                AdminBrokerAssignment.admin_user_id == admin_user_id
            )
        ).scalars().all()
        current_ids = {a.broker_user_id for a in existing}

        for a in existing:
            if a.broker_user_id not in broker_ids_set:
                s.delete(a)
        now = utcnow()
        for bid in broker_ids_set - current_ids:
            s.add(AdminBrokerAssignment(
                admin_user_id=admin_user_id,
                broker_user_id=bid,
                created_at=now,
            ))
        s.add(AuditLog(
            user_id=actor.id,
            action="set_admin_broker_ids",
            entity_type="user",
            entity_id=admin_user_id,
            detail_json='{"broker_ids": ' + str(sorted(broker_ids_set)) + "}",
        ))
        s.commit()
