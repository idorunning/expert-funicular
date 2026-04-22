"""Portable JSON mirror of user corrections.

`merchant_rules` remains the source of truth and is consulted by the
categoriser before the LLM (see ``rules.find_exact`` and
``rules.fuzzy_topk``). This module keeps a human-readable JSON file alongside
the DB so the broker can see every correction on disk and move them between
installs — on startup the file is replayed into ``merchant_rules`` so any
entries added externally (or restored from backup) are picked up.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import paths
from ..db.models import Client, MerchantRule, utcnow
from ..utils.logging import logger

_PATH_ENV = "BROKERLEDGER_CORRECTIONS_CACHE"


def cache_path() -> Path:
    override = os.environ.get(_PATH_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return paths.corrections_cache_path()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> list[dict[str, Any]]:
    p = cache_path()
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Corrections cache at {} unreadable: {}", p, e)
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _atomic_write(entries: list[dict[str, Any]]) -> None:
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".corrections-", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append(
    *,
    merchant: str,
    category: str,
    group: str,
    scope: str,
    client_id: int | None,
    weight: int,
) -> None:
    """Append (or replace) the matching correction in the JSON mirror."""
    entries = load()
    key = (merchant, scope, client_id)
    filtered = [
        e for e in entries
        if (e.get("merchant"), e.get("scope"), e.get("client_id")) != key
    ]
    filtered.append({
        "merchant": merchant,
        "category": category,
        "group": group,
        "scope": scope,
        "client_id": client_id,
        "weight": weight,
        "updated_at": _iso_now(),
    })
    _atomic_write(filtered)


def backup_to(destination: Path) -> Path:
    """Copy the current cache (if any) to ``destination``. Creates parent dirs.

    Returns the absolute path written. If no cache exists yet an empty-list
    JSON file is written so the user still gets a file they can restore
    from later.
    """
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries = load()
    with destination.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, sort_keys=True)
    return destination


def restore_from(source: Path) -> int:
    """Replace the active cache with the contents of ``source``.

    Validates that the file parses as a JSON list of dicts before overwriting.
    Returns the number of entries restored. Raises ``ValueError`` for malformed
    files so the UI can surface a friendly error.
    """
    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"Backup file not found: {source}")
    try:
        with source.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Backup file is not valid JSON: {e}") from e
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        raise ValueError("Backup file must be a JSON list of correction entries.")
    cleaned = [e for e in data if e.get("merchant") and e.get("category")]
    _atomic_write(cleaned)
    return len(cleaned)


def sync_into_db(session: Session) -> int:
    """Import any JSON entry whose (merchant, scope, client_id) is missing from
    merchant_rules. Returns how many rows were inserted.

    Orphaned client-scoped entries (whose ``client_id`` no longer exists —
    typically because the client was deleted after the correction was cached)
    used to be skipped on every startup and logged as a warning.  They are
    now pruned from the JSON file in-place so the warning fires at most once
    per orphaned entry — subsequent startups are clean.

    Promoting an orphaned client-scoped rule to the global scope would change
    categorisation behaviour for other clients, so we drop the entries
    entirely rather than rewriting their scope.
    """
    entries = load()
    if not entries:
        return 0
    live_client_ids = set(
        session.execute(select(Client.id)).scalars().all()
    )
    inserted = 0
    orphans_pruned = 0
    kept: list[dict[str, Any]] = []
    now = utcnow()
    for e in entries:
        merchant = (e.get("merchant") or "").strip()
        category = (e.get("category") or "").strip()
        scope = (e.get("scope") or "").strip()
        client_id = e.get("client_id")
        if not merchant or not category or scope not in {"client", "global"}:
            # Keep malformed entries out of the pruned file too.
            orphans_pruned += 1
            continue
        if scope == "client" and client_id not in live_client_ids:
            orphans_pruned += 1
            continue
        kept.append(e)
        existing = session.execute(
            select(MerchantRule).where(
                MerchantRule.merchant_normalized == merchant,
                MerchantRule.category == category,
                MerchantRule.scope == scope,
                MerchantRule.client_id == client_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        weight = int(e.get("weight") or 2)
        session.add(MerchantRule(
            merchant_normalized=merchant,
            category=category,
            weight=weight,
            scope=scope,
            client_id=client_id,
            created_by=None,
            created_at=now,
            last_seen_at=now,
        ))
        inserted += 1
    if orphans_pruned:
        # One-line INFO (not WARNING) — now that we prune, this is expected
        # housekeeping the first time a user starts up after deleting clients.
        logger.info(
            "Corrections cache: pruned {} stale entr{}",
            orphans_pruned,
            "y" if orphans_pruned == 1 else "ies",
        )
        try:
            _atomic_write(kept)
        except OSError as e:
            logger.warning("Could not rewrite corrections cache after prune: {}", e)
    if inserted:
        session.commit()
    return inserted
