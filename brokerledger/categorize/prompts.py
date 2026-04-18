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
        "You are a categorisation engine for UK personal bank transactions "
        "used by a mortgage broker for an affordability assessment.",
        "You MUST return a single JSON object and nothing else.",
        "You MUST pick a category strictly from the provided taxonomy.",
        "If unsure, pick the closest and return a low confidence (0..0.5).",
        "",
        "Taxonomy (group :: category):",
    ]
    for c in COMMITTED_CATEGORIES:
        lines.append(f"committed :: {c}")
    for c in DISCRETIONARY_CATEGORIES:
        lines.append(f"discretionary :: {c}")
    for c in INCOME_CATEGORIES:
        lines.append(f"income :: {c}")
    for c in EXCLUDED_CATEGORIES:
        lines.append(f"excluded :: {c}")
    lines.extend([
        "",
        'Output JSON schema: {"category": "<exact string>", '
        '"group": "committed|discretionary|income|excluded", '
        '"confidence": <float 0..1>, '
        '"reason": "<<=140 chars>"}',
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
