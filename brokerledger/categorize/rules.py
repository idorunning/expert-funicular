"""Rule store — exact and fuzzy merchant → category lookups."""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import MerchantRule, utcnow


@dataclass(frozen=True)
class RuleHit:
    category: str
    weight: int
    scope: str
    score: float  # 100 for exact, 0..100 for fuzzy


def find_exact(session: Session, merchant: str, client_id: int | None) -> RuleHit | None:
    """Client-scoped rules beat global ones."""
    if client_id is not None:
        row = session.execute(
            select(MerchantRule).where(
                MerchantRule.merchant_normalized == merchant,
                MerchantRule.scope == "client",
                MerchantRule.client_id == client_id,
            ).order_by(MerchantRule.weight.desc()).limit(1)
        ).scalar_one_or_none()
        if row is not None:
            return RuleHit(category=row.category, weight=row.weight, scope="client", score=100.0)
    row = session.execute(
        select(MerchantRule).where(
            MerchantRule.merchant_normalized == merchant,
            MerchantRule.scope == "global",
        ).order_by(MerchantRule.weight.desc()).limit(1)
    ).scalar_one_or_none()
    if row is not None:
        return RuleHit(category=row.category, weight=row.weight, scope="global", score=100.0)
    return None


def fuzzy_topk(session: Session, merchant: str, k: int = 5) -> list[RuleHit]:
    rows = session.execute(
        select(MerchantRule.merchant_normalized, MerchantRule.category, MerchantRule.weight, MerchantRule.scope)
    ).all()
    if not rows:
        return []
    # Build candidate list: prefer the best-weighted entry per (merchant, scope).
    best: dict[tuple[str, str], tuple[str, int]] = {}
    for m, cat, w, scope in rows:
        key = (m, scope)
        cur = best.get(key)
        if cur is None or w > cur[1]:
            best[key] = (cat, w)
    strings = list({m for (m, _scope) in best.keys()})
    matches = process.extract(merchant, strings, scorer=fuzz.token_set_ratio, limit=k)
    hits: list[RuleHit] = []
    seen: set[str] = set()
    for match_str, score, _idx in matches:
        if match_str in seen:
            continue
        seen.add(match_str)
        # Choose client scope entry if available for this string, else global.
        client_entry = best.get((match_str, "client"))
        global_entry = best.get((match_str, "global"))
        entry = client_entry or global_entry
        if entry is None:
            continue
        cat, w = entry
        scope = "client" if client_entry is not None else "global"
        hits.append(RuleHit(category=cat, weight=w, scope=scope, score=float(score)))
    return hits


def touch_rule_last_seen(session: Session, merchant: str, category: str) -> None:
    row = session.execute(
        select(MerchantRule).where(
            MerchantRule.merchant_normalized == merchant,
            MerchantRule.category == category,
        ).order_by(MerchantRule.scope.asc())
    ).scalars().first()
    if row is not None:
        row.last_seen_at = utcnow()
