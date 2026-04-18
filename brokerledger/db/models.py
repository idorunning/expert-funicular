"""SQLAlchemy 2.x ORM models."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC datetime. SQLite stores DateTime without tz, so keep the whole
    app consistently naive to avoid aware/naive comparison errors."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (CheckConstraint("role IN ('admin','broker')", name="ck_user_role"),)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80), unique=True)
    folder_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column()

    statements: Mapped[list["Statement"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Statement(Base):
    __tablename__ = "statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name: Mapped[str] = mapped_column(String(400), nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_profile: Mapped[str | None] = mapped_column(String(40))
    period_start: Mapped[str | None] = mapped_column(String(10))
    period_end: Mapped[str | None] = mapped_column(String(10))
    imported_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    page_count: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)

    client: Mapped[Client] = relationship(back_populates="statements")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="statement", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("client_id", "file_sha256", name="uq_statement_client_hash"),
        CheckConstraint(
            "file_kind IN ('pdf_text','pdf_ocr','csv','xlsx','other')",
            name="ck_statement_kind",
        ),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    posted_date: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO date
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_normalized: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    direction: Mapped[str] = mapped_column(String(6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    category_group: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(80))
    confidence: Mapped[float | None] = mapped_column()
    needs_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="llm")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, onupdate=utcnow)

    client: Mapped[Client] = relationship(back_populates="transactions")
    statement: Mapped[Statement] = relationship(back_populates="transactions")

    __table_args__ = (
        CheckConstraint("direction IN ('debit','credit')", name="ck_tx_direction"),
        CheckConstraint(
            "source IN ('rule','llm','user','rule+llm','seed')",
            name="ck_tx_source",
        ),
    )


class Category(Base):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    group_name: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "group_name IN ('committed','discretionary','income','excluded')",
            name="ck_category_group",
        ),
    )


class MerchantRule(Base):
    __tablename__ = "merchant_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    merchant_normalized: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    category: Mapped[str] = mapped_column(ForeignKey("categories.name"), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "merchant_normalized", "category", "scope", "client_id", name="uq_rule_key"
        ),
        CheckConstraint("scope IN ('global','client')", name="ck_rule_scope"),
        Index("ix_rules_merchant_scope", "merchant_normalized", "scope"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail_json: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
