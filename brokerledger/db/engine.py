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
    _ensure_transaction_flags_column(_engine)
    _ensure_transaction_reasoning_column(_engine)
    _ensure_transaction_source_check(_engine)
    _ensure_client_deleted_at_column(_engine)
    _ensure_user_role_check_includes_admin_staff(_engine)
    _migrate_retired_categories(_engine)
    _ensure_audit_log_indexes(_engine)
    return _engine


def _ensure_client_deleted_at_column(engine: Engine) -> None:
    """Add the admin-only soft-delete column to pre-existing clients tables."""
    with engine.begin() as c:
        cols = {
            row[1]
            for row in c.exec_driver_sql("PRAGMA table_info(clients)").fetchall()
        }
        if "deleted_at" not in cols:
            c.exec_driver_sql("ALTER TABLE clients ADD COLUMN deleted_at DATETIME")


def _ensure_user_role_check_includes_admin_staff(engine: Engine) -> None:
    """Rebuild the users table if its ck_user_role CHECK pre-dates 'admin_staff'.

    SQLite can't alter a CHECK in-place; copy the data into a fresh table and
    swap.  Matches the pattern used by ``_ensure_transaction_source_check``.
    """
    with engine.begin() as c:
        row = c.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not row or not row[0]:
            return
        if "admin_staff" in row[0]:
            return
        logger.info("Rebuilding users table to include admin_staff in role check")
        cols_info = c.exec_driver_sql("PRAGMA table_info(users)").fetchall()
        col_names = [r[1] for r in cols_info]
        col_list = ", ".join(col_names)
        c.exec_driver_sql("PRAGMA foreign_keys=OFF")
        c.exec_driver_sql("ALTER TABLE users RENAME TO _users_old")
    Base.metadata.tables["users"].create(engine)
    with engine.begin() as c:
        c.exec_driver_sql(
            f"INSERT INTO users ({col_list}) SELECT {col_list} FROM _users_old"
        )
        c.exec_driver_sql("DROP TABLE _users_old")
        c.exec_driver_sql("PRAGMA foreign_keys=ON")


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


def _ensure_transaction_flags_column(engine: Engine) -> None:
    """Add the Gambling/Fast-Payment flags column to pre-existing transactions tables."""
    with engine.begin() as c:
        cols = {
            row[1]
            for row in c.exec_driver_sql("PRAGMA table_info(transactions)").fetchall()
        }
        if "flags" not in cols:
            c.exec_driver_sql("ALTER TABLE transactions ADD COLUMN flags VARCHAR(64)")


def _ensure_transaction_reasoning_column(engine: Engine) -> None:
    """Add the chain-of-thought reasoning column to pre-existing transactions tables."""
    with engine.begin() as c:
        cols = {
            row[1]
            for row in c.exec_driver_sql("PRAGMA table_info(transactions)").fetchall()
        }
        if "reasoning" not in cols:
            c.exec_driver_sql("ALTER TABLE transactions ADD COLUMN reasoning TEXT")


def _ensure_transaction_source_check(engine: Engine) -> None:
    """Rebuild the transactions table if its ck_tx_source CHECK constraint
    pre-dates 'sibling_auto' / 'register_fuzzy' / 'flag_default' / 'seed'.

    SQLite doesn't let us alter a CHECK constraint in-place, so when the
    existing one is out of date we copy the data into a fresh table that
    carries the current constraint, then swap them.
    """
    required_tokens = (
        "sibling_auto",
        "register_fuzzy",
        "flag_default",
        "seed",
    )
    with engine.begin() as c:
        row = c.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()
        if not row or not row[0]:
            return
        ddl = row[0]
        if all(token in ddl for token in required_tokens):
            return

        logger.info("Rebuilding transactions table to refresh ck_tx_source")
        cols_info = c.exec_driver_sql("PRAGMA table_info(transactions)").fetchall()
        col_names = [r[1] for r in cols_info]
        col_list = ", ".join(col_names)

        c.exec_driver_sql("PRAGMA foreign_keys=OFF")
        c.exec_driver_sql("ALTER TABLE transactions RENAME TO _transactions_old")
    # Recreate the transactions table via SQLAlchemy metadata so we get the
    # current CHECK constraint definition, then copy data across.
    Base.metadata.tables["transactions"].create(engine)
    with engine.begin() as c:
        c.exec_driver_sql(
            f"INSERT INTO transactions ({col_list}) SELECT {col_list} FROM _transactions_old"
        )
        c.exec_driver_sql("DROP TABLE _transactions_old")
        c.exec_driver_sql("PRAGMA foreign_keys=ON")


_RETIRED_CATEGORIES = (
    "Fast payments / person-to-person",
    "Gambling",
)


def _migrate_retired_categories(engine: Engine) -> None:
    """Null out transactions still carrying retired categories from earlier
    releases. They'll be re-flagged as gambling/fast-payment on the next
    re-categorisation; leaving the category blank marks them for review.
    """
    with engine.begin() as c:
        placeholders = ",".join(["?"] * len(_RETIRED_CATEGORIES))
        c.exec_driver_sql(
            f"""
            UPDATE transactions
            SET category = NULL,
                category_group = NULL,
                needs_review = 1
            WHERE category IN ({placeholders})
            """,
            _RETIRED_CATEGORIES,
        )


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
