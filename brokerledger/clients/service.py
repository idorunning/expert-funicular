"""Client CRUD and per-client folder management."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import paths
from ..auth.session import require_login
from ..db.engine import session_scope
from ..db.models import AuditLog, Client, utcnow


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
                       detail_json=f'{{"display_name":"{display_name}"}}'))
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
                       detail_json=f'{{"display_name":"{new_name}"}}'))
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
