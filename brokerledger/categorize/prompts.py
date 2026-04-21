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
        "Worked examples — reuse the same mappings when the new transaction "
        "matches one of these patterns. Examples use the exact same input "
        "format you will receive below.",
        "",
        'Description (as printed): "POCKET MONEY"',
        'Normalised merchant: "POCKET MONEY"',
        "Amount (GBP, sign = direction): -15.00",
        "Direction: debit",
        "{",
        '  "thinking": "Pocket money is the UK term for a weekly allowance '
        "a parent gives to a child. £15 fits a child's allowance. The broker "
        "tracks this under Child care because it is a household cost of "
        'raising children, not Entertainment or a bank transfer.",',
        '  "category": "Child care",',
        '  "group": "discretionary",',
        '  "confidence": 0.85,',
        '  "reason": "Pocket money = child allowance -> Child care"',
        "}",
        "",
        'Description (as printed): "SALARY HSBC"',
        'Normalised merchant: "SALARY HSBC"',
        "Amount (GBP, sign = direction): 2400.00",
        "Direction: credit",
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
        'Description (as printed): "JUST EAT LONDON"',
        'Normalised merchant: "JUST EAT"',
        "Amount (GBP, sign = direction): -22.40",
        "Direction: debit",
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
        'Description (as printed): "ALLOWANCE"',
        'Normalised merchant: "ALLOWANCE"',
        "Amount (GBP, sign = direction): -20.00",
        "Direction: debit",
        "{",
        '  "thinking": "A debit labelled ALLOWANCE with no other context is '
        "the same pattern as POCKET MONEY — a recurring small payment a "
        'parent gives to a child. Classify as Child care.",',
        '  "category": "Child care",',
        '  "group": "discretionary",',
        '  "confidence": 0.78,',
        '  "reason": "Allowance = child allowance -> Child care"',
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
