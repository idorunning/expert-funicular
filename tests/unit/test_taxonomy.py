"""Tests for the fixed taxonomy and its ``includes:`` semantic hints."""
from __future__ import annotations

from brokerledger.categorize.taxonomy import (
    CATEGORY_INCLUDES,
    all_categories,
    includes_for,
    user_visible_categories,
)


def test_every_user_visible_category_has_includes():
    """Every category shown in the broker UI dropdown must carry a
    non-empty ``includes:`` hint so the LLM can reason from the
    taxonomy description rather than memorising merchants per category."""
    for cat in user_visible_categories():
        hint = CATEGORY_INCLUDES.get(cat, "")
        assert hint, f"Category {cat!r} has no CATEGORY_INCLUDES entry"
        assert len(hint) >= 20, (
            f"Category {cat!r} includes hint is too short: {hint!r}"
        )


def test_income_and_excluded_categories_have_includes():
    """Internal categories also need hints so the model knows which credits
    belong in Salary/Wages vs Other income and which transactions to put
    in Transfer/Excluded."""
    for cat in ("Salary/Wages", "Other income", "Transfer/Excluded"):
        assert CATEGORY_INCLUDES.get(cat), (
            f"Internal category {cat!r} missing includes hint"
        )


def test_includes_for_unknown_category_returns_empty():
    assert includes_for("Nonexistent Category") == ""


def test_all_categories_carry_includes_field():
    """``CategoryDef`` now has an ``includes`` attribute populated from
    ``CATEGORY_INCLUDES``. Downstream code (prompt renderer, tooltips)
    relies on this."""
    for c in all_categories():
        if c.name in CATEGORY_INCLUDES:
            assert c.includes == CATEGORY_INCLUDES[c.name]


def test_child_care_includes_mentions_pocket_money():
    """This is the specific regression that drove the refactor: the model
    couldn't infer POCKET MONEY -> Child care because the taxonomy only
    exposed category names. The fix is the Child care description calling
    out allowance vocabulary explicitly."""
    assert "pocket money" in CATEGORY_INCLUDES["Child care"].lower()


def test_food_includes_mentions_supermarkets_and_takeaways():
    hint = CATEGORY_INCLUDES["Food"].lower()
    assert "tesco" in hint or "supermarket" in hint
    assert "deliveroo" in hint or "just eat" in hint or "takeaway" in hint
