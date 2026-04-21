"""Prompt templates for the local LLM categoriser."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .taxonomy import (
    CATEGORY_INCLUDES,
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


def _render_taxonomy_line(group: str, category: str) -> str:
    hint = CATEGORY_INCLUDES.get(category, "")
    if hint:
        return f"{group} :: {category} — includes: {hint}"
    return f"{group} :: {category}"


def build_system_prompt() -> str:
    lines = [
        "You are a UK mortgage broker's categorisation assistant. For every "
        "transaction, think step by step about what the description most "
        "likely represents in a UK household context, then choose exactly "
        "one category from the taxonomy below.",
        "",
        "Reason from the taxonomy's 'includes:' descriptions — they tell you "
        "what each category covers. Do not rely on merchant names you have "
        "memorised; match the transaction's description against the category "
        "descriptions and pick the best semantic fit. If a description word "
        "(e.g. 'pocket money', 'nursery', 'takeaway', 'broadband') appears "
        "in a category's 'includes:' line, that is a strong signal.",
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
        "clues you used (merchant words, amount, direction, date) and which "
        "category 'includes:' line they match. Acknowledge uncertainty "
        "explicitly when you feel it.",
        '  "category":   one of the taxonomy categories below (exact string, '
        "right-hand side only).",
        '  "group":      committed | discretionary | income | excluded',
        '  "confidence": float 0.0-1.0. Use < 0.45 when you are genuinely '
        "unsure.",
        '  "reason":     <= 140 char broker-facing summary.',
        "",
        "Taxonomy (group :: category — includes: vocabulary that belongs in "
        "this category):",
    ]
    for c in COMMITTED_CATEGORIES:
        lines.append(_render_taxonomy_line("committed", c))
    for c in DISCRETIONARY_CATEGORIES:
        lines.append(_render_taxonomy_line("discretionary", c))
    for c in INCOME_CATEGORIES:
        lines.append(_render_taxonomy_line("income", c))
    for c in EXCLUDED_CATEGORIES:
        lines.append(_render_taxonomy_line("excluded", c))
    lines.extend([
        "",
        "Example of the output shape — follow this schema exactly, but "
        "reason from the taxonomy descriptions above for the actual "
        "category choice:",
        "",
        'Description (as printed): "SALARY HSBC"',
        'Normalised merchant: "SALARY HSBC"',
        "Amount (GBP, sign = direction): 2400.00",
        "Direction: credit",
        "{",
        '  "thinking": "A credit labelled SALARY is a wage payment from an '
        "employer; HSBC is the paying bank. Direction is credit, amount is "
        "consistent with a monthly net salary. The Salary/Wages 'includes:' "
        'line covers SALARY / WAGES / PAYROLL credits.",',
        '  "category": "Salary/Wages",',
        '  "group": "income",',
        '  "confidence": 0.95,',
        '  "reason": "Salary credit from employer"',
        "}",
    ])
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
