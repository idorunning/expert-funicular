"""AI Training Zone backend — collect broker guidance, consume it on demand.

Flow:
- When the broker reads a transaction's chain-of-thought reasoning in Review
  and wants to correct it, they write a free-text note (and optionally pick a
  corrected category). ``save_note`` persists that guidance immediately.
- Notes accumulate in ``consumed_at IS NULL`` state.
- When the broker clicks "Start Training" in the AI Training Zone,
  ``run_training_pass`` iterates the unconsumed notes. For each note with a
  suggested category it upserts the merchant rule (same flow the existing
  register/feedback loop uses) and marks the note consumed.
- Notes without a suggested category stay pending — they're visible in the UI
  but require the broker to pick a target category before they can train.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.engine import session_scope
from ..db.models import (
    AuditLog,
    MerchantRule,
    Transaction,
    TrainingNote,
    utcnow,
)
from ..utils.logging import logger
from .memory import _decay_other_categories, _upsert
from .taxonomy import group_of, user_visible_categories
from . import corrections_cache


@dataclass
class TrainingHighlight:
    merchant: str
    category: str
    scope: str
    was_new_rule: bool
    note_id: int


@dataclass
class TrainingReport:
    notes_processed: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    skipped_no_category: int = 0
    skipped_dismissed: int = 0
    highlights: list[TrainingHighlight] = field(default_factory=list)


def save_note(
    *,
    transaction_id: int,
    user_id: int | None,
    note: str,
    suggested_category: str | None = None,
) -> int:
    """Persist a broker training note. Returns the new note id."""
    note = (note or "").strip()
    if not note and not suggested_category:
        raise ValueError("A note or a suggested category is required.")
    if suggested_category is not None:
        suggested_category = suggested_category.strip() or None
        if suggested_category and suggested_category not in user_visible_categories():
            raise ValueError(f"Unknown category: {suggested_category!r}")
    with session_scope() as s:
        tx = s.get(Transaction, transaction_id)
        if tx is None:
            raise ValueError(f"Transaction {transaction_id} not found")
        entry = TrainingNote(
            transaction_id=transaction_id,
            created_by=user_id,
            note=note,
            suggested_category=suggested_category,
            created_at=utcnow(),
        )
        s.add(entry)
        s.flush()
        note_id = entry.id
        s.commit()
    return note_id


def list_unconsumed(limit: int = 500) -> list[dict]:
    """Return unconsumed (and not-dismissed) notes with their tx context.

    The UI uses dicts rather than ORM objects so the session closes cleanly
    and Qt signals can carry the payload across threads.
    """
    with session_scope() as s:
        rows = s.execute(
            select(TrainingNote, Transaction)
            .join(Transaction, TrainingNote.transaction_id == Transaction.id)
            .where(
                TrainingNote.consumed_at.is_(None),
                TrainingNote.dismissed_at.is_(None),
            )
            .order_by(TrainingNote.created_at.asc())
            .limit(limit)
        ).all()
        out: list[dict] = []
        for note, tx in rows:
            out.append({
                "id": note.id,
                "tx_id": tx.id,
                "description": tx.description_raw,
                "merchant": tx.merchant_normalized,
                "current_category": tx.category,
                "current_confidence": tx.confidence,
                "current_source": tx.source,
                "reasoning": tx.reasoning or "",
                "note": note.note,
                "suggested_category": note.suggested_category,
                "created_at": note.created_at,
            })
    return out


def list_recent_consumed(limit: int = 25) -> list[dict]:
    """Return the most recently consumed notes for the "Recent learnings" panel."""
    with session_scope() as s:
        rows = s.execute(
            select(TrainingNote, Transaction)
            .join(Transaction, TrainingNote.transaction_id == Transaction.id)
            .where(TrainingNote.consumed_at.is_not(None))
            .order_by(TrainingNote.consumed_at.desc())
            .limit(limit)
        ).all()
        out: list[dict] = []
        for note, tx in rows:
            out.append({
                "id": note.id,
                "merchant": tx.merchant_normalized,
                "category": note.suggested_category or tx.category,
                "consumed_at": note.consumed_at,
                "note": note.note,
            })
    return out


def dismiss_note(note_id: int, user_id: int | None) -> bool:
    """Mark a note dismissed without applying it. Returns True on success."""
    with session_scope() as s:
        note = s.get(TrainingNote, note_id)
        if note is None or note.consumed_at is not None:
            return False
        note.dismissed_at = utcnow()
        s.add(AuditLog(
            user_id=user_id,
            action="dismiss_training_note",
            entity_type="training_note",
            entity_id=note.id,
        ))
        s.commit()
    return True


def _apply_note(session: Session, *, note: TrainingNote, tx: Transaction) -> TrainingHighlight | None:
    """Consume a single note: upsert the merchant rule and mark consumed.

    Returns a highlight describing the change, or None if the note lacks a
    category (skipped).
    """
    if not note.suggested_category:
        return None
    merchant = tx.merchant_normalized
    category = note.suggested_category
    # Client-scope rule; same delta weight the regular correction flow uses.
    was_new = _upsert(
        session,
        merchant=merchant,
        category=category,
        scope="client",
        client_id=tx.client_id,
        created_by=note.created_by,
        delta_weight=+2,
    )
    _decay_other_categories(
        session, merchant=merchant, winner=category,
        scope="client", client_id=tx.client_id,
    )
    corrections_cache.append(
        merchant=merchant,
        category=category,
        group=group_of(category),
        scope="client",
        client_id=tx.client_id,
        weight=2,
    )
    # Look up the rule id so we can link it from the note.
    rule = session.execute(
        select(MerchantRule).where(
            MerchantRule.merchant_normalized == merchant,
            MerchantRule.category == category,
            MerchantRule.scope == "client",
            MerchantRule.client_id == tx.client_id,
        )
    ).scalar_one_or_none()
    now = utcnow()
    note.consumed_at = now
    note.consumed_rule_id = rule.id if rule is not None else None
    note.consumed_confidence = 0.90
    # Also reclassify the source transaction so Review reflects the outcome.
    tx.category = category
    tx.category_group = group_of(category)
    tx.source = "user"
    tx.confidence = 1.0
    tx.needs_review = 0
    tx.reason = "trained via broker note"
    tx.updated_at = now
    return TrainingHighlight(
        merchant=merchant,
        category=category,
        scope="client",
        was_new_rule=was_new,
        note_id=note.id,
    )


def run_training_pass(user_id: int | None = None) -> TrainingReport:
    """Consume every unconsumed + not-dismissed note that has a suggested category.

    Notes without a suggested category are counted as ``skipped_no_category``
    and remain available for the broker to complete later. Notes that have
    already been consumed or dismissed are ignored.
    """
    report = TrainingReport()
    with session_scope() as s:
        rows = s.execute(
            select(TrainingNote, Transaction)
            .join(Transaction, TrainingNote.transaction_id == Transaction.id)
            .where(
                TrainingNote.consumed_at.is_(None),
                TrainingNote.dismissed_at.is_(None),
            )
            .order_by(TrainingNote.created_at.asc())
        ).all()
        for note, tx in rows:
            if not note.suggested_category:
                report.skipped_no_category += 1
                continue
            try:
                highlight = _apply_note(s, note=note, tx=tx)
            except Exception as e:  # noqa: BLE001 — don't let one bad note kill the batch
                logger.warning("Training note {} failed: {}", note.id, e)
                continue
            if highlight is None:
                continue
            report.notes_processed += 1
            if highlight.was_new_rule:
                report.rules_created += 1
            else:
                report.rules_updated += 1
            report.highlights.append(highlight)
        if report.notes_processed:
            s.add(AuditLog(
                user_id=user_id,
                action="training_pass",
                entity_type="training_note",
                entity_id=None,
                detail_json=_summary_json(report),
            ))
        s.commit()
    return report


def _summary_json(report: TrainingReport) -> str:
    import json
    return json.dumps({
        "notes_processed": report.notes_processed,
        "rules_created": report.rules_created,
        "rules_updated": report.rules_updated,
        "skipped_no_category": report.skipped_no_category,
    })
