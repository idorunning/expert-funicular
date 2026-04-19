"""Persistent key/value settings backed by the AppSetting table."""
from __future__ import annotations

from .engine import session_scope
from .models import AppSetting


def get(key: str, default: str | None = None) -> str | None:
    with session_scope() as s:
        row = s.get(AppSetting, key)
        return row.value if row is not None else default


def put(key: str, value: str) -> None:
    with session_scope() as s:
        row = s.get(AppSetting, key)
        if row is None:
            s.add(AppSetting(key=key, value=value))
        else:
            row.value = value
        s.commit()


def delete(key: str) -> None:
    with session_scope() as s:
        row = s.get(AppSetting, key)
        if row is not None:
            s.delete(row)
            s.commit()
