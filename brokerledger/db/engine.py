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
    _ensure_password_reset_code_columns(_engine)
    _ensure_statement_verified_columns(_engine)
    _ensure_audit_log_indexes(_engine)
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


def _ensure_password_reset_code_columns(engine: Engine) -> None:
    """Add SMTP-reset code columns to pre-existing password_reset_requests tables."""
    with engine.begin() as c:
        cols = {
            row[1]
            for row in c.exec_driver_sql(
                "PRAGMA table_info(password_reset_requests)"
            ).fetchall()
        }
        if "code_hash" not in cols:
            c.exec_driver_sql(
                "ALTER TABLE password_reset_requests ADD COLUMN code_hash TEXT"
            )
        if "code_expires_at" not in cols:
            c.exec_driver_sql(
                "ALTER TABLE password_reset_requests ADD COLUMN code_expires_at DATETIME"
            )


def _ensure_statement_verified_columns(engine: Engine) -> None:
    """Add broker sign-off columns to pre-existing statements tables."""
    with engine.begin() as c:
        cols = {
            row[1]
            for row in c.exec_driver_sql("PRAGMA table_info(statements)").fetchall()
        }
        if "verified_at" not in cols:
            c.exec_driver_sql("ALTER TABLE statements ADD COLUMN verified_at DATETIME")
        if "verified_by" not in cols:
            c.exec_driver_sql("ALTER TABLE statements ADD COLUMN verified_by INTEGER")


def _ensure_audit_log_indexes(engine: Engine) -> None:
    """Create helpful indexes on audit_log for the admin viewer."""
    with engine.begin() as c:
        c.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_audit_user_at ON audit_log (user_id, at)"
        )
        c.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_audit_action ON audit_log (action)"
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
