"""Client CRUD and per-client folder management."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import paths
from ..auth.session import CurrentUser, require_admin, require_login
from ..db.engine import session_scope
from ..db.models import (
    AdminBrokerAssignment,
    AuditLog,
    Client,
    Statement,
    Transaction,
    User,
    utcnow,
)


@dataclass(frozen=True)
class StatementVerification:
    statement_id: int
    verified_at: datetime
    verified_by: int
    verified_by_username: str | None


@dataclass(frozen=True)
class ClientRecord:
    id: int
    display_name: str
    reference: str | None
    folder_path: str
    created_at: datetime
    archived_at: datetime | None
    deleted_at: datetime | None = None
    created_by: int | None = None
    # Human-readable broker name ("Jane Smith" or "jsmith"). Populated by
    # ``list_clients`` / ``get_client`` via a cheap one-shot join.
    created_by_name: str | None = None


class ClientError(Exception):
    pass


_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.strip()).strip("-").lower() or "client"


def _allocate_folder(display_name: str, client_id: int) -> Path:
    base = paths.clients_dir()
    base.mkdir(parents=True, exist_ok=True)
    folder = base / f"{client_id:05d}-{_slugify(display_name)}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "statements").mkdir(parents=True, exist_ok=True)
    (folder / "exports").mkdir(parents=True, exist_ok=True)
    return folder


def _owner_name_map(session, user_ids: set[int]) -> dict[int, str]:
    """Return {user_id: full_name or username} for the given ids."""
    if not user_ids:
        return {}
    rows = session.execute(
        select(User.id, User.full_name, User.username).where(User.id.in_(user_ids))
    ).all()
    return {
        uid: (full or username or "")
        for (uid, full, username) in rows
    }


def _record(c: Client, owner_name: str | None = None) -> ClientRecord:
    return ClientRecord(
        id=c.id,
        display_name=c.display_name,
        reference=c.reference,
        folder_path=c.folder_path,
        created_at=c.created_at,
        archived_at=c.archived_at,
        deleted_at=c.deleted_at,
        created_by=c.created_by,
        created_by_name=owner_name,
    )


def _visible_broker_ids(user: CurrentUser, session) -> set[int] | None:
    """Broker ids whose clients are visible to ``user``.

    Returns ``None`` to mean "no scope filter" (administrators see everything).
    """
    if user.role == "admin":
        return None
    if user.role == "broker":
        return {user.id}
    if user.role == "admin_staff":
        rows = session.execute(
            select(AdminBrokerAssignment.broker_user_id).where(
                AdminBrokerAssignment.admin_user_id == user.id
            )
        ).all()
        return {r[0] for r in rows}
    return set()


def _require_client_in_scope(client: Client, user: CurrentUser, session) -> None:
    """Raise :class:`ClientError` if ``user`` can't operate on ``client``.

    Admins bypass the check.  Brokers and admin-staff must find the client
    within their visibility scope (mirrors ``list_clients``).
    """
    scope = _visible_broker_ids(user, session)
    if scope is None:
        return
    if client.created_by in scope:
        return
    raise ClientError("This client is outside your access scope.")


def create_client(display_name: str, reference: str | None = None) -> ClientRecord:
    user = require_login()
    display_name = display_name.strip()
    if not display_name:
        raise ClientError("Client name is required")
    with session_scope() as s:
        # Insert with a placeholder folder_path, then update once we know the id.
        placeholder = f"__pending__-{display_name}-{utcnow().timestamp():.0f}"
        c = Client(
            display_name=display_name,
            reference=(reference.strip() or None) if reference else None,
            folder_path=placeholder,
            created_by=user.id,
        )
        s.add(c)
        try:
            s.commit()
        except IntegrityError as e:
            s.rollback()
            raise ClientError("Reference already in use") from e
        folder = _allocate_folder(display_name, c.id)
        c.folder_path = str(folder)
        s.add(AuditLog(user_id=user.id, action="create_client", entity_type="client", entity_id=c.id,
                       detail_json=json.dumps({"display_name": display_name})))
        s.commit()
        return _record(c, owner_name=user.full_name or user.username)


def list_clients(
    include_archived: bool = False,
    include_deleted: bool = False,
) -> list[ClientRecord]:
    """List clients visible to the current user.

    * Administrator (``role='admin'``) sees every client.
    * Broker sees only clients they created.
    * Admin staff sees clients of every broker they're assigned to.

    ``include_archived`` — show clients with ``archived_at`` set (the renamed
    "Closed" status).  ``include_deleted`` — only honoured for administrators;
    shows clients with ``deleted_at`` set.
    """
    user = require_login()
    with session_scope() as s:
        q = select(Client).order_by(Client.display_name.asc())

        scope = _visible_broker_ids(user, s)
        if scope is not None:
            if not scope:
                return []
            q = q.where(Client.created_by.in_(scope))

        if not include_archived:
            q = q.where(Client.archived_at.is_(None))

        # Only admins can see soft-deleted clients, and even then only when
        # they explicitly ask.
        if not (include_deleted and user.role == "admin"):
            q = q.where(Client.deleted_at.is_(None))

        rows = list(s.execute(q).scalars().all())
        owner_names = _owner_name_map(s, {c.created_by for c in rows if c.created_by})
        return [_record(c, owner_name=owner_names.get(c.created_by)) for c in rows]


def get_client(client_id: int) -> ClientRecord:
    user = require_login()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        _require_client_in_scope(c, user, s)
        names = _owner_name_map(s, {c.created_by} if c.created_by else set())
        return _record(c, owner_name=names.get(c.created_by) if c.created_by else None)


def rename_client(client_id: int, new_name: str) -> ClientRecord:
    user = require_login()
    new_name = new_name.strip()
    if not new_name:
        raise ClientError("Name required")
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        _require_client_in_scope(c, user, s)
        c.display_name = new_name
        s.add(AuditLog(user_id=user.id, action="rename_client", entity_type="client", entity_id=c.id,
                       detail_json=json.dumps({"display_name": new_name})))
        s.commit()
        return _record(c)


def archive_client(client_id: int) -> None:
    """Mark the client as Closed (soft-hidden but recoverable)."""
    user = require_login()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        _require_client_in_scope(c, user, s)
        # Admin staff are view-only; they cannot change client status.
        if user.role == "admin_staff":
            raise ClientError("Admin staff can view clients but not change their status.")
        c.archived_at = utcnow()
        s.add(AuditLog(user_id=user.id, action="archive_client", entity_type="client", entity_id=c.id))
        s.commit()


def restore_client(client_id: int) -> ClientRecord:
    """Clear Closed/Deleted markers and make the client Active again."""
    user = require_login()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        _require_client_in_scope(c, user, s)
        if user.role == "admin_staff":
            raise ClientError("Admin staff can view clients but not change their status.")
        # If the client was soft-deleted, only administrators may restore.
        if c.deleted_at is not None and user.role != "admin":
            raise ClientError("Only an administrator can restore a deleted client.")
        c.archived_at = None
        c.deleted_at = None
        s.add(AuditLog(user_id=user.id, action="restore_client", entity_type="client", entity_id=c.id))
        s.commit()
        return _record(c)


def soft_delete_client(client_id: int) -> None:
    """Administrator-only soft delete — marks ``deleted_at`` so the client is
    hidden from every user but can be recovered via ``restore_client``.

    Distinct from :func:`delete_client`, which performs a hard delete
    (cascading to statements and transactions).
    """
    user = require_admin()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        c.deleted_at = utcnow()
        s.add(AuditLog(user_id=user.id, action="soft_delete_client", entity_type="client", entity_id=c.id))
        s.commit()


def delete_client(client_id: int) -> None:
    """Admin-only. Removes the client row (statements/transactions cascade)."""
    user = require_admin()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        display_name = c.display_name
        s.delete(c)
        s.add(AuditLog(
            user_id=user.id,
            action="delete_client",
            entity_type="client",
            entity_id=client_id,
            detail_json=json.dumps({"display_name": display_name}),
        ))
        s.commit()


def count_flagged_transactions(statement_id: int) -> int:
    """Number of transactions on the statement still marked for review."""
    with session_scope() as s:
        return int(
            s.execute(
                select(func.count())
                .select_from(Transaction)
                .where(
                    Transaction.statement_id == statement_id,
                    Transaction.needs_review == 1,
                )
            ).scalar_one()
        )


def verify_statement(statement_id: int) -> StatementVerification:
    """Stamp a statement as reviewed by the current broker.

    Blocks if any transactions on the statement are still flagged for review.
    Idempotent on re-verify: timestamp is refreshed, a new audit row is added.
    """
    user = require_login()
    with session_scope() as s:
        stmt = s.get(Statement, statement_id)
        if stmt is None:
            raise ClientError("Statement not found")
        flagged = int(
            s.execute(
                select(func.count())
                .select_from(Transaction)
                .where(
                    Transaction.statement_id == statement_id,
                    Transaction.needs_review == 1,
                )
            ).scalar_one()
        )
        if flagged:
            raise ClientError(
                f"{flagged} transaction(s) still need review before you can verify."
            )
        now = utcnow()
        stmt.verified_at = now
        stmt.verified_by = user.id
        s.add(AuditLog(
            user_id=user.id,
            action="verify_statement",
            entity_type="statement",
            entity_id=statement_id,
            detail_json=json.dumps({"client_id": stmt.client_id}),
        ))
        s.commit()
        verifier = s.get(User, user.id)
        return StatementVerification(
            statement_id=statement_id,
            verified_at=now,
            verified_by=user.id,
            verified_by_username=verifier.username if verifier else None,
        )


def reassign_client(client_id: int, new_user_id: int) -> ClientRecord:
    """Admin-only. Change the owning broker (``created_by``)."""
    actor = require_admin()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        target = s.get(User, new_user_id)
        if target is None or target.is_active == 0:
            raise ClientError("Target user not found or inactive")
        from_id = c.created_by
        c.created_by = new_user_id
        s.add(AuditLog(
            user_id=actor.id,
            action="reassign_client",
            entity_type="client",
            entity_id=c.id,
            detail_json=json.dumps({"from": from_id, "to": new_user_id}),
        ))
        s.commit()
        return _record(c)
