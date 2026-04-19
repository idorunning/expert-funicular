"""SQLite engine, sessionmaker, and initialiser."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .. import paths
from ..utils.logging import logger
from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


@event.listens_for(Engine, "connect")
def _enable_sqlite_fk(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:  # noqa: BLE001 — some drivers don't need/accept this
        pass


def init_engine(db_file: Path | None = None, echo: bool = False) -> Engine:
    """Initialise the process-global engine. Safe to call multiple times."""
    global _engine, _SessionFactory
    if _engine is not None:
        return _engine
    paths.ensure_dirs()
    target = db_file or paths.db_path()
    url = f"sqlite:///{target}"
    logger.debug("Opening SQLite at {}", target)
    _engine = create_engine(url, echo=echo, future=True)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    _ensure_user_photo_column(_engine)
    return _engine


def _ensure_user_photo_column(engine: Engine) -> None:
    """Idempotent backfills on the users table so pre-existing DBs upgrade cleanly."""
    with engine.begin() as c:
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(users)").fetchall()}
        if "photo_path" not in cols:
            c.exec_driver_sql("ALTER TABLE users ADD COLUMN photo_path TEXT")
        if "email" not in cols:
            c.exec_driver_sql("ALTER TABLE users ADD COLUMN email TEXT")
            c.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"
            )


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


def session_scope() -> Session:
    if _SessionFactory is None:
        init_engine()
    assert _SessionFactory is not None
    return _SessionFactory()


def reset_for_tests() -> None:
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
