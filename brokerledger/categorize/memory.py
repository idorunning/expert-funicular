"""Learning store — promote corrections into merchant_rules for future reuse."""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import AuditLog, MerchantRule, Transaction, utcnow
from . import corrections_cache
from .prompts import FewShotExample
from .taxonomy import group_of


@dataclass
class CorrectionOutcome:
    new_category: str
    rule_upserted: bool
    promoted_to_global: bool


def _upsert(
    session: Session,
    *,
    merchant: str,
    category: str,
    scope: str,
    client_id: int | None,
    created_by: int | None,
    delta_weight: int = 2,
) -> bool:
    row = session.execute(
        select(MerchantRule).where(
            MerchantRule.merchant_normalized == merchant,
            MerchantRule.category == category,
            MerchantRule.scope == scope,
            MerchantRule.client_id == client_id,
        )
    ).scalar_one_or_none()
    now = utcnow()
    if row is None:
        session.add(MerchantRule(
            merchant_normalized=merchant,
            category=category,
            weight=delta_weight,
            scope=scope,
            client_id=client_id,
            created_by=created_by,
            created_at=now,
            last_seen_at=now,
        ))
        return True
    row.weight += delta_weight
    row.last_seen_at = now
    return False


def _decay_other_categories(session: Session, *, merchant: str, winner: str,
                            scope: str, client_id: int | None) -> None:
    rows = session.execute(
        select(MerchantRule).where(
            MerchantRule.merchant_normalized == merchant,
            MerchantRule.scope == scope,
            MerchantRule.client_id == client_id,
            MerchantRule.category != winner,
        )
    ).scalars().all()
    for r in rows:
        r.weight = max(0, r.weight - 1)


def apply_correction(
    session: Session,
    *,
    tx: Transaction,
    new_category: str,
    user_id: int | None,
) -> CorrectionOutcome:
    """Called when a user amends a transaction's category."""
    merchant = tx.merchant_normalized
    settings = get_settings()

    # 1. Client-scope rule (strong personal preference).
    _upsert(session, merchant=merchant, category=new_category, scope="client",
            client_id=tx.client_id, created_by=user_id, delta_weight=+2)
    _decay_other_categories(session, merchant=merchant, winner=new_category,
                            scope="client", client_id=tx.client_id)
    corrections_cache.append(
        merchant=merchant,
        category=new_category,
        group=group_of(new_category),
        scope="client",
        client_id=tx.client_id,
        weight=2,
    )

    # 2. Promote to global if N distinct clients have confirmed the same mapping.
    distinct_clients = session.execute(
        select(func.count(func.distinct(MerchantRule.client_id))).where(
            MerchantRule.merchant_normalized == merchant,
            MerchantRule.category == new_category,
            MerchantRule.scope == "client",
        )
    ).scalar_one()
    promoted = False
    if distinct_clients >= settings.global_promote_threshold:
        upserted = _upsert(
            session,
            merchant=merchant,
            category=new_category,
            scope="global",
            client_id=None,
            created_by=user_id,
            delta_weight=+1,
        )
        _decay_other_categories(session, merchant=merchant, winner=new_category,
                                scope="global", client_id=None)
        corrections_cache.append(
            merchant=merchant,
            category=new_category,
            group=group_of(new_category),
            scope="global",
            client_id=None,
            weight=1,
        )
        promoted = upserted

    # 3. Update the transaction itself.
    tx.category = new_category
    tx.category_group = group_of(new_category)
    tx.source = "user"
    tx.confidence = 1.0
    tx.needs_review = 0
    tx.reason = "corrected by user"
    tx.updated_at = utcnow()

    # 4. Audit.
    session.add(AuditLog(
        user_id=user_id,
        action="correct_category",
        entity_type="transaction",
        entity_id=tx.id,
        detail_json=json.dumps({"merchant": merchant, "to": new_category}),
    ))

    return CorrectionOutcome(new_category=new_category, rule_upserted=True, promoted_to_global=promoted)


def retrieve_few_shot(
    session: Session,
    *,
    merchant: str,
    client_id: int | None,
    k: int = 8,
) -> list[FewShotExample]:
    """Pull the best nearby rules as few-shot examples for the LLM prompt."""
    from rapidfuzz import fuzz, process

    rows = session.execute(
        select(
            MerchantRule.merchant_normalized,
            MerchantRule.category,
            MerchantRule.weight,
            MerchantRule.scope,
            MerchantRule.client_id,
        )
    ).all()
    if not rows:
        return []
    # Rank candidates: exact merchant for same client first, else global weight.
    candidates: list[tuple[str, str, int]] = []
    for m, cat, w, scope, cid in rows:
        if scope == "client" and cid == client_id:
            candidates.append((m, cat, w + 5))
        elif scope == "global":
            candidates.append((m, cat, w))
    if not candidates:
        return []
    unique_strings = list({c[0] for c in candidates})
    matches = process.extract(merchant, unique_strings, scorer=fuzz.token_set_ratio, limit=k * 2)
    by_string: dict[str, tuple[str, int]] = {}
    for m, cat, w in candidates:
        cur = by_string.get(m)
        if cur is None or w > cur[1]:
            by_string[m] = (cat, w)
    out: list[FewShotExample] = []
    for match_str, score, _idx in matches:
        if score < 55:
            continue
        entry = by_string.get(match_str)
        if entry is None:
            continue
        out.append(FewShotExample(merchant=match_str, category=entry[0]))
        if len(out) >= k:
            break
    return out
