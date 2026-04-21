"""Prompt templates for the local LLM categoriser."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .taxonomy import (
    COMMITTED_CATEGORIES,
    DISCRETIONARY_CATEGORIES,
    EXCLUDED_CATEGORIES,
    INCOME_CATEGORIES,
)


@dataclass(frozen=True)
class FewShotExample:
    merchant: str
    category: str
    amount: Decimal | None = None


def build_system_prompt() -> str:
    lines = [
        "You are a UK mortgage broker's categorisation assistant. For every "
        "transaction, think step by step about what the description most "
        "likely represents in a UK household context, then choose exactly "
        "one category from the taxonomy.",
        "",
        "You MUST return a single JSON object and nothing else.",
        "You MUST pick a category strictly from the provided taxonomy (the "
        '"category" value is ONLY the right-hand side after "::", never the '
        "group name or the whole line).",
        "If genuinely unsure, pick the closest fit and return a low "
        "confidence (< 0.45).",
        "",
        "Output JSON schema with these EXACT keys (all mandatory):",
        '  "thinking":   3-6 sentences of plain reasoning. Walk through the '
        "clues you used (merchant words, amount, direction, date). "
        "Acknowledge uncertainty explicitly when you feel it.",
        '  "category":   one of the taxonomy categories below (exact string, '
        "right-hand side only).",
        '  "group":      committed | discretionary | income | excluded',
        '  "confidence": float 0.0-1.0. Use < 0.45 when you are genuinely '
        "unsure.",
        '  "reason":     <= 140 char broker-facing summary.',
        "",
        "Worked examples (follow this shape, do NOT copy verbatim):",
        "",
        'Tx: "POCKET MONEY" debit £15.00',
        "{",
        '  "thinking": "Pocket money is the common UK term for a weekly '
        "allowance a parent gives to a child. £15 fits typical amounts for "
        "a child's allowance. It is a household cost of raising children "
        'rather than Entertainment or a bank transfer.",',
        '  "category": "Child care",',
        '  "group": "committed",',
        '  "confidence": 0.82,',
        '  "reason": "Pocket money = child allowance -> Child care"',
        "}",
        "",
        'Tx: "SALARY HSBC" credit £2,400.00',
        "{",
        '  "thinking": "A credit labelled SALARY is a wage payment from an '
        "employer; HSBC is the paying bank. Direction is credit, amount is "
        'consistent with a monthly net salary.",',
        '  "category": "Salary/Wages",',
        '  "group": "income",',
        '  "confidence": 0.95,',
        '  "reason": "Salary credit from employer"',
        "}",
        "",
        'Tx: "JUST EAT LONDON" debit £22.40',
        "{",
        '  "thinking": "Just Eat is a takeaway food delivery platform. '
        "Debits to it are discretionary food spending rather than weekly "
        'groceries.",',
        '  "category": "Food",',
        '  "group": "discretionary",',
        '  "confidence": 0.88,',
        '  "reason": "Takeaway via Just Eat"',
        "}",
        "",
        "Taxonomy (group :: category) — pick category string only:",
    ]
    for c in COMMITTED_CATEGORIES:
        lines.append(f"committed :: {c}")
    for c in DISCRETIONARY_CATEGORIES:
        lines.append(f"discretionary :: {c}")
    for c in INCOME_CATEGORIES:
        lines.append(f"income :: {c}")
    for c in EXCLUDED_CATEGORIES:
        lines.append(f"excluded :: {c}")
    return "\n".join(lines)


def build_user_prompt(
    description_raw: str,
    merchant_normalized: str,
    amount: Decimal,
    direction: str,
    posted_date: str,
    few_shot: list[FewShotExample],
) -> str:
    lines: list[str] = []
    if few_shot:
        lines.append("Few-shot examples from this broker's history (trusted corrections):")
        for ex in few_shot:
            if ex.amount is not None:
                lines.append(f'- "{ex.merchant}" GBP {ex.amount} -> {ex.category}')
            else:
                lines.append(f'- "{ex.merchant}" -> {ex.category}')
        lines.append("")
    lines.extend([
        "Now categorise this transaction:",
        f'Description (as printed): "{description_raw}"',
        f'Normalised merchant: "{merchant_normalized}"',
        f"Amount (GBP, sign = direction): {amount}",
        f"Date: {posted_date}",
        f"Direction: {direction}",
        "",
        "Return only the JSON object.",
    ])
    return "\n".join(lines)
