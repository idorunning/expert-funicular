"""Rule → LLM → confidence orchestration for transaction categorisation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings, get_threshold
from ..db.engine import session_scope
from ..db.models import Transaction, utcnow
from ..utils.logging import logger
from .flags import (
    FLAG_FAST_PAYMENT,
    FLAG_GAMBLING,
    detect_flags,
    serialize_flags,
    smart_default_category,
)
from .llm_client import LLMClient, LLMError, get_llm_client
from .memory import retrieve_few_shot
from .rules import find_exact, fuzzy_topk, touch_rule_last_seen
from .taxonomy import (
    GROUP_DISCRETIONARY,
    GROUP_EXCLUDED,
    GROUP_INCOME,
    RISK_CATEGORIES,
    group_of,
)


@dataclass
class Decision:
    category: str
    group: str
    confidence: float
    source: str
    reason: str
    needs_review: bool
    thinking: str = ""


def _finalise_for_flags(d: Decision, *, risk_flags: list[str]) -> Decision:
    """Force ``needs_review`` + annotate the reason when a risk flag is present.

    The flag-based smart-default branch already sets ``needs_review=True``
    but other branches (register hits, LLM) don't know the row was flagged.
    This keeps flagged transactions visible in Review without dictating
    their category — the category comes from whatever branch classified it.
    """
    if not risk_flags:
        return d
    d.needs_review = True
    if d.source == "flag_default":
        return d
    suffix = f" [flagged: {', '.join(risk_flags)}]"
    if suffix not in (d.reason or ""):
        d.reason = (d.reason or "") + suffix
    return d


def _escalate_if_risk(d: Decision) -> Decision:
    """High-risk categories always land in Review; cap confidence below High tier."""
    if d.category not in RISK_CATEGORIES:
        return d
    suffix = " [auto-flagged: high-risk category]"
    reason = d.reason if suffix in d.reason else (d.reason + suffix)
    return Decision(
        category=d.category,
        group=d.group,
        confidence=min(d.confidence, 0.84),
        source=d.source,
        reason=reason,
        needs_review=True,
        thinking=d.thinking,
    )


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
    is_credit = direction == "credit"
    flags_list = detect_flags(description_raw, merchant, direction=direction)
    risk_flags = [f for f in flags_list if f != "inbound"]

    # 0. Flag-based smart default — only short-circuit when the flag has a
    # concrete category on this direction (Gambling debit → Entertainment,
    # FP credit → Other income). A Fast Payment DEBIT has no default
    # category because "Liam Tracey (Pocket money) FASTER PAYMENT" is still
    # obviously Child care from the description; letting the pipeline run
    # means the register and LLM get to classify it, while the flag chip
    # still prompts the broker to confirm the recipient. The flag forces
    # ``needs_review=True`` at the end regardless of where the pipeline
    # lands.
    if risk_flags:
        smart = smart_default_category(risk_flags, is_credit=is_credit)
        if smart:
            return _finalise_for_flags(
                Decision(
                    category=smart,
                    group=group_of(smart),
                    confidence=0.85,
                    source="flag_default",
                    reason=f"flagged: {', '.join(risk_flags)}; smart default applied",
                    needs_review=True,
                ),
                risk_flags=risk_flags,
            )

    # 1. Exact rule.
    exact = find_exact(session, merchant, client_id)
    if exact is not None and exact.weight >= get_threshold("confirm_weight_threshold"):
        touch_rule_last_seen(session, merchant, exact.category)
        return _finalise_for_flags(_escalate_if_risk(Decision(
            category=exact.category,
            group=group_of(exact.category),
            confidence=0.99,
            source="rule",
            reason=f"exact match (scope={exact.scope}, weight={exact.weight})",
            needs_review=False,
        )), risk_flags=risk_flags)

    # 2. Fuzzy rule.
    fuzzy = fuzzy_topk(session, merchant, k=5)
    top = fuzzy[0] if fuzzy else None
    if top is not None and top.score >= get_threshold("fuzzy_high"):
        touch_rule_last_seen(session, merchant, top.category)
        return _finalise_for_flags(_escalate_if_risk(Decision(
            category=top.category,
            group=group_of(top.category),
            confidence=0.9,
            source="rule",
            reason=f"fuzzy match score={top.score:.0f}",
            needs_review=False,
        )), risk_flags=risk_flags)
    # 2b. Fuzzy-medium — register match but below the auto-apply bar. Use
    #     the register's answer as a strong hint but keep the row in Review
    #     so the broker can confirm or override.
    if top is not None and top.score >= get_threshold("fuzzy_medium"):
        touch_rule_last_seen(session, merchant, top.category)
        return _finalise_for_flags(_escalate_if_risk(Decision(
            category=top.category,
            group=group_of(top.category),
            confidence=0.75,
            source="register_fuzzy",
            reason=f"fuzzy match (medium) score={top.score:.0f}",
            needs_review=True,
        )), risk_flags=risk_flags)

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
        # All LLM attempts failed — use keyword-based fallback so every
        # transaction still gets a useful category instead of Transfer/Excluded.
        logger.warning("LLM unavailable for '{}'; applying keyword fallback", merchant)
        from .llm_client import FakeLLMClient
        kw = FakeLLMClient().classify(
            description_raw=description_raw,
            merchant_normalized=merchant,
            amount=amount,
            direction=direction,
            posted_date=posted_date,
            few_shot=few_shot,
        )
        return _finalise_for_flags(_escalate_if_risk(Decision(
            category=kw.category,
            group=kw.group,
            confidence=min(kw.confidence, 0.35),
            source="llm",
            reason=f"keyword fallback (AI unavailable: {last_err})",
            needs_review=True,
        )), risk_flags=risk_flags)

    # 3b. Conditional web-lookup fallback — only when the first pass gave up
    # (low confidence OR landed on a generic bucket) AND the admin has
    # opted into merchant web lookup. Fires at most once per transaction.
    from . import web_lookup
    GENERIC_CATEGORIES = {"Other", "Other income"}
    fallback_threshold = settings.llm_web_fallback_threshold
    thinking_combined = getattr(out, "thinking", "") or ""
    web_reason_suffix = ""
    if (
        web_lookup.is_enabled()
        and (
            (out.confidence or 0.0) < fallback_threshold
            or out.category in GENERIC_CATEGORIES
        )
    ):
        hint = web_lookup.lookup_merchant(merchant)
        if hint:
            logger.info("web fallback firing for {!r} (conf={:.2f} cat={!r})",
                        merchant, out.confidence or 0.0, out.category)
            try:
                retry = llm.classify(
                    description_raw=description_raw,
                    merchant_normalized=merchant,
                    amount=amount,
                    direction=direction,
                    posted_date=posted_date,
                    few_shot=few_shot,
                    web_hint=hint,
                )
            except LLMError as e:
                logger.warning("web-fallback retry failed: {}", e)
            else:
                if (retry.confidence or 0.0) > (out.confidence or 0.0):
                    logger.info(
                        "web fallback promoted {!r}: {:.2f} {!r} -> {:.2f} {!r}",
                        merchant, out.confidence or 0.0, out.category,
                        retry.confidence or 0.0, retry.category,
                    )
                    thinking_combined = (
                        (thinking_combined + "\n[after web] " if thinking_combined else "")
                        + (getattr(retry, "thinking", "") or "")
                    )
                    out = retry
                    web_reason_suffix = " [web-assisted]"

    # 4. Flag-for-review logic.
    fuzzy_low = get_threshold("fuzzy_low")
    fuzzy_high = get_threshold("fuzzy_high")
    needs_review = False
    if out.confidence < get_threshold("llm_confidence_threshold"):
        needs_review = True
    if top is not None and fuzzy_low <= top.score < fuzzy_high and top.category != out.category:
        needs_review = True
    if exact is None and top is None:
        needs_review = True

    source = "rule+llm" if top is not None else "llm"
    return _finalise_for_flags(_escalate_if_risk(Decision(
        category=out.category,
        group=out.group,
        confidence=out.confidence,
        source=source,
        reason=out.reason + web_reason_suffix,
        needs_review=needs_review,
        thinking=thinking_combined,
    )), risk_flags=risk_flags)


def categorize_statement(
    statement_id: int,
    *,
    llm: LLMClient | None = None,
    progress_cb=None,
    tx_cb=None,
    tx_id_cb=None,
) -> int:
    """Categorise every transaction on a statement. Returns # rows updated.

    ``tx_cb(category, group, amount, direction)`` — optional callback invoked
    after each decision so the UI can stream running totals while the LLM
    still processes the rest of the file.

    ``tx_id_cb(client_id, tx_id)`` — optional callback fired once each row is
    flushed to the DB so UI views (e.g. Review) can refresh a specific row
    live while ingest continues.
    """
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
            if tx.direction == "credit" and decision.group not in {GROUP_INCOME, GROUP_EXCLUDED}:
                decision = Decision(
                    category="Other income",
                    group=GROUP_INCOME,
                    confidence=max(decision.confidence, 0.6),
                    source=decision.source,
                    reason="credit defaulted to income; " + decision.reason,
                    needs_review=True,
                    thinking=decision.thinking,
                )
            tx.category = decision.category
            tx.category_group = decision.group
            tx.confidence = decision.confidence
            tx.source = decision.source
            tx.reason = decision.reason
            tx.reasoning = decision.thinking or None
            tx.flags = serialize_flags(detect_flags(tx.description_raw, tx.merchant_normalized, direction=tx.direction or "debit"))
            tx.needs_review = 1 if decision.needs_review else 0
            tx.updated_at = utcnow()
            updated += 1
            if progress_cb is not None:
                progress_cb(idx + 1, total)
            if tx_cb is not None:
                tx_cb(decision.category, decision.group, tx.amount, tx.direction)
            s.flush()
            if tx_id_cb is not None:
                tx_id_cb(tx.client_id, tx.id)
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
        tx.reasoning = decision.thinking or None
        tx.flags = serialize_flags(detect_flags(tx.description_raw, tx.merchant_normalized, direction=tx.direction or "debit"))
        tx.needs_review = 1 if decision.needs_review else 0
        tx.updated_at = utcnow()
        s.commit()
        return decision


def recategorize_client(
    client_id: int,
    *,
    llm: LLMClient | None = None,
    progress_cb=None,
    tx_cb=None,
    tx_id_cb=None,
) -> int:
    """Re-run AI categorisation on all non-user transactions for a client.

    Skips rows with source == 'user' so human corrections are never
    overwritten. Returns the number of rows updated.
    """
    llm = llm or get_llm_client()
    with session_scope() as s:
        txs = s.execute(
            select(Transaction).where(
                Transaction.client_id == client_id,
                Transaction.source != "user",
            )
        ).scalars().all()
        total = len(txs)
        if total == 0:
            return 0
        updated = 0
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
            if tx.direction == "credit" and decision.group not in {GROUP_INCOME, GROUP_EXCLUDED}:
                decision = Decision(
                    category="Other income",
                    group=GROUP_INCOME,
                    confidence=max(decision.confidence, 0.6),
                    source=decision.source,
                    reason="credit defaulted to income; " + decision.reason,
                    needs_review=True,
                    thinking=decision.thinking,
                )
            tx.category = decision.category
            tx.category_group = decision.group
            tx.confidence = decision.confidence
            tx.source = decision.source
            tx.reason = decision.reason
            tx.reasoning = decision.thinking or None
            tx.flags = serialize_flags(detect_flags(tx.description_raw, tx.merchant_normalized, direction=tx.direction or "debit"))
            tx.needs_review = 1 if decision.needs_review else 0
            tx.updated_at = utcnow()
            updated += 1
            if progress_cb is not None:
                progress_cb(idx + 1, total)
            if tx_cb is not None:
                tx_cb(decision.category, decision.group, tx.amount, tx.direction)
            s.flush()
            if tx_id_cb is not None:
                tx_id_cb(tx.client_id, tx.id)
        s.commit()
    return updated
