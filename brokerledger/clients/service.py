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
from ..auth.session import require_admin, require_login
from ..db.engine import session_scope
from ..db.models import AuditLog, Client, Statement, Transaction, User, utcnow


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
        return ClientRecord(c.id, c.display_name, c.reference, c.folder_path, c.created_at, c.archived_at)


def list_clients(include_archived: bool = False) -> list[ClientRecord]:
    require_login()
    with session_scope() as s:
        q = select(Client).order_by(Client.display_name.asc())
        if not include_archived:
            q = q.where(Client.archived_at.is_(None))
        rows = s.execute(q).scalars().all()
        return [
            ClientRecord(c.id, c.display_name, c.reference, c.folder_path, c.created_at, c.archived_at)
            for c in rows
        ]


def get_client(client_id: int) -> ClientRecord:
    require_login()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        return ClientRecord(c.id, c.display_name, c.reference, c.folder_path, c.created_at, c.archived_at)


def rename_client(client_id: int, new_name: str) -> ClientRecord:
    user = require_login()
    new_name = new_name.strip()
    if not new_name:
        raise ClientError("Name required")
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        c.display_name = new_name
        s.add(AuditLog(user_id=user.id, action="rename_client", entity_type="client", entity_id=c.id,
                       detail_json=json.dumps({"display_name": new_name})))
        s.commit()
        return ClientRecord(c.id, c.display_name, c.reference, c.folder_path, c.created_at, c.archived_at)


def archive_client(client_id: int) -> None:
    user = require_login()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        c.archived_at = utcnow()
        s.add(AuditLog(user_id=user.id, action="archive_client", entity_type="client", entity_id=c.id))
        s.commit()


def restore_client(client_id: int) -> ClientRecord:
    user = require_login()
    with session_scope() as s:
        c = s.get(Client, client_id)
        if c is None:
            raise ClientError("Client not found")
        c.archived_at = None
        s.add(AuditLog(user_id=user.id, action="restore_client", entity_type="client", entity_id=c.id))
        s.commit()
        return ClientRecord(c.id, c.display_name, c.reference, c.folder_path, c.created_at, c.archived_at)


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
        return ClientRecord(c.id, c.display_name, c.reference, c.folder_path, c.created_at, c.archived_at)
