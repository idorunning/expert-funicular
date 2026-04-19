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
from ..db.models import MerchantRule, utcnow
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


def sync_into_db(session: Session) -> int:
    """Import any JSON entry whose (merchant, scope, client_id) is missing from
    merchant_rules. Returns how many rows were inserted."""
    entries = load()
    if not entries:
        return 0
    inserted = 0
    now = utcnow()
    for e in entries:
        merchant = (e.get("merchant") or "").strip()
        category = (e.get("category") or "").strip()
        scope = (e.get("scope") or "").strip()
        client_id = e.get("client_id")
        if not merchant or not category or scope not in {"client", "global"}:
            continue
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
    if inserted:
        session.commit()
    return inserted
