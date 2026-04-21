"""Sibling-learning — when a user corrects a transaction's category,
propagate the correction to semantically similar merchants.

A sibling is another transaction for the same client whose ``description_raw``
has a high fuzzy similarity to the corrected transaction's description (e.g.
``Faster Payment Olivia Grace Tracey`` → ``Faster Payment Abigail Tracey``).

Two tiers:
- auto ≥ ``SIBLING_AUTO_THRESHOLD`` (default 90) — apply correction
  automatically, source="sibling_auto".
- confirm in [ ``SIBLING_CONFIRM_THRESHOLD`` , ``SIBLING_AUTO_THRESHOLD`` ) —
  return to caller so a confirmation dialog can be shown.

Rows where ``source == 'user'`` are always excluded so prior manual
corrections are never overwritten.
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz.fuzz import token_set_ratio
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Transaction

SIBLING_AUTO_THRESHOLD = 90
SIBLING_CONFIRM_THRESHOLD = 70


@dataclass(frozen=True)
class SiblingCandidate:
    tx_id: int
    description: str
    merchant: str
    current_category: str | None
    score: int


@dataclass(frozen=True)
class SiblingScan:
    auto: list[SiblingCandidate]
    confirm: list[SiblingCandidate]


def find_siblings(
    session: Session,
    *,
    source_tx: Transaction,
    new_category: str,
) -> SiblingScan:
    """Scan the client's transactions for siblings of ``source_tx``.

    Returns two lists — candidates above ``SIBLING_AUTO_THRESHOLD`` that can
    be auto-applied, and candidates in the confirm window that the caller
    should prompt about.
    """
    base_desc = (source_tx.description_raw or "").strip()
    base_merchant = (source_tx.merchant_normalized or "").strip()
    if not base_desc and not base_merchant:
        return SiblingScan(auto=[], confirm=[])

    rows = session.execute(
        select(Transaction).where(
            Transaction.client_id == source_tx.client_id,
            Transaction.id != source_tx.id,
            Transaction.source != "user",
        )
    ).scalars().all()

    auto: list[SiblingCandidate] = []
    confirm: list[SiblingCandidate] = []

    for tx in rows:
        # Skip rows already on the target category.
        if (tx.category or "") == new_category:
            continue
        cand_desc = tx.description_raw or ""
        cand_merchant = tx.merchant_normalized or ""
        # Combine description and merchant for a more forgiving match on
        # bank-statement strings that embed names inside the description.
        base_blob = f"{base_desc} {base_merchant}".strip()
        cand_blob = f"{cand_desc} {cand_merchant}".strip()
        score = int(token_set_ratio(base_blob, cand_blob))
        if score >= SIBLING_AUTO_THRESHOLD:
            auto.append(SiblingCandidate(
                tx_id=tx.id,
                description=cand_desc,
                merchant=cand_merchant,
                current_category=tx.category,
                score=score,
            ))
        elif score >= SIBLING_CONFIRM_THRESHOLD:
            confirm.append(SiblingCandidate(
                tx_id=tx.id,
                description=cand_desc,
                merchant=cand_merchant,
                current_category=tx.category,
                score=score,
            ))

    auto.sort(key=lambda c: c.score, reverse=True)
    confirm.sort(key=lambda c: c.score, reverse=True)
    return SiblingScan(auto=auto, confirm=confirm)


def apply_auto_siblings(
    session: Session,
    *,
    source_tx: Transaction,
    new_category: str,
    candidates: list[SiblingCandidate],
) -> int:
    """Apply ``new_category`` to every candidate row. Returns count updated."""
    if not candidates:
        return 0
    from ..db.models import utcnow
    from .taxonomy import group_of

    group = group_of(new_category)
    updated = 0
    for cand in candidates:
        tx = session.get(Transaction, cand.tx_id)
        if tx is None:
            continue
        tx.category = new_category
        tx.category_group = group
        tx.source = "sibling_auto"
        tx.confidence = min(1.0, cand.score / 100.0)
        tx.needs_review = 0
        tx.reason = f"auto-applied from sibling tx#{source_tx.id} (score={cand.score})"
        tx.updated_at = utcnow()
        updated += 1
    return updated
