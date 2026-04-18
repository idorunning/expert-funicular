"""Rule → LLM → confidence orchestration for transaction categorisation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.engine import session_scope
from ..db.models import Transaction, utcnow
from ..utils.logging import logger
from .llm_client import LLMClient, LLMError, get_llm_client
from .memory import retrieve_few_shot
from .rules import find_exact, fuzzy_topk, touch_rule_last_seen
from .taxonomy import GROUP_EXCLUDED, GROUP_INCOME, group_of


@dataclass
class Decision:
    category: str
    group: str
    confidence: float
    source: str
    reason: str
    needs_review: bool


def _decide(
    session: Session,
    *,
    merchant: str,
    description_raw: str,
    amount: Decimal,
    direction: str,
    posted_date: str,
    client_id: int | None,
    llm: LLMClient,
) -> Decision:
    settings = get_settings()

    # 1. Exact rule.
    exact = find_exact(session, merchant, client_id)
    if exact is not None and exact.weight >= settings.confirm_weight_threshold:
        touch_rule_last_seen(session, merchant, exact.category)
        return Decision(
            category=exact.category,
            group=group_of(exact.category),
            confidence=0.99,
            source="rule",
            reason=f"exact match (scope={exact.scope}, weight={exact.weight})",
            needs_review=False,
        )

    # 2. Fuzzy rule.
    fuzzy = fuzzy_topk(session, merchant, k=5)
    top = fuzzy[0] if fuzzy else None
    if top is not None and top.score >= settings.fuzzy_high:
        touch_rule_last_seen(session, merchant, top.category)
        return Decision(
            category=top.category,
            group=group_of(top.category),
            confidence=0.9,
            source="rule",
            reason=f"fuzzy match score={top.score:.0f}",
            needs_review=False,
        )

    # 3. LLM with few-shot.
    few_shot = retrieve_few_shot(session, merchant=merchant, client_id=client_id, k=settings.few_shot_k)

    last_err: Exception | None = None
    for attempt in range(settings.llm_max_retries + 1):
        try:
            out = llm.classify(
                description_raw=description_raw,
                merchant_normalized=merchant,
                amount=amount,
                direction=direction,
                posted_date=posted_date,
                few_shot=few_shot,
            )
            break
        except LLMError as e:
            last_err = e
            logger.warning("LLM attempt {} failed: {}", attempt + 1, e)
    else:
        return Decision(
            category="Transfer/Excluded",
            group=GROUP_EXCLUDED,
            confidence=0.0,
            source="llm",
            reason=f"LLM failure: {last_err}",
            needs_review=True,
        )

    # 4. Flag-for-review logic.
    needs_review = False
    if out.confidence < settings.llm_confidence_threshold:
        needs_review = True
    if top is not None and settings.fuzzy_low <= top.score < settings.fuzzy_high and top.category != out.category:
        needs_review = True
    if exact is None and top is None:
        needs_review = True

    source = "rule+llm" if top is not None else "llm"
    return Decision(
        category=out.category,
        group=out.group,
        confidence=out.confidence,
        source=source,
        reason=out.reason,
        needs_review=needs_review,
    )


def categorize_statement(
    statement_id: int,
    *,
    llm: LLMClient | None = None,
    progress_cb=None,
) -> int:
    """Categorise every transaction on a statement. Returns # rows updated."""
    llm = llm or get_llm_client()
    updated = 0
    with session_scope() as s:
        txs = s.execute(
            select(Transaction).where(Transaction.statement_id == statement_id)
        ).scalars().all()
        total = len(txs)
        for idx, tx in enumerate(txs):
            decision = _decide(
                s,
                merchant=tx.merchant_normalized,
                description_raw=tx.description_raw,
                amount=tx.amount,
                direction=tx.direction,
                posted_date=tx.posted_date,
                client_id=tx.client_id,
                llm=llm,
            )
            # Credits default to income unless the rule/LLM said otherwise.
            if tx.direction == "credit" and decision.group not in {GROUP_INCOME, GROUP_EXCLUDED}:
                decision = Decision(
                    category="Other income",
                    group=GROUP_INCOME,
                    confidence=max(decision.confidence, 0.6),
                    source=decision.source,
                    reason="credit defaulted to income; " + decision.reason,
                    needs_review=True,
                )
            tx.category = decision.category
            tx.category_group = decision.group
            tx.confidence = decision.confidence
            tx.source = decision.source
            tx.reason = decision.reason
            tx.needs_review = 1 if decision.needs_review else 0
            tx.updated_at = utcnow()
            updated += 1
            if progress_cb is not None:
                progress_cb(idx + 1, total)
        s.commit()
    return updated


def recategorize_transaction(tx_id: int, *, llm: LLMClient | None = None) -> Decision:
    """Recompute a single transaction's category (used when ruleset changes)."""
    llm = llm or get_llm_client()
    with session_scope() as s:
        tx = s.get(Transaction, tx_id)
        if tx is None:
            raise ValueError(f"Transaction {tx_id} not found")
        decision = _decide(
            s,
            merchant=tx.merchant_normalized,
            description_raw=tx.description_raw,
            amount=tx.amount,
            direction=tx.direction,
            posted_date=tx.posted_date,
            client_id=tx.client_id,
            llm=llm,
        )
        tx.category = decision.category
        tx.category_group = decision.group
        tx.confidence = decision.confidence
        tx.source = decision.source
        tx.reason = decision.reason
        tx.needs_review = 1 if decision.needs_review else 0
        tx.updated_at = utcnow()
        s.commit()
        return decision
