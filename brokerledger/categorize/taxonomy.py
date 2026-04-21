"""Fixed affordability taxonomy used throughout the app."""
from __future__ import annotations

from dataclasses import dataclass

GROUP_COMMITTED = "committed"
GROUP_DISCRETIONARY = "discretionary"
GROUP_INCOME = "income"
GROUP_EXCLUDED = "excluded"

ALL_GROUPS = (GROUP_COMMITTED, GROUP_DISCRETIONARY, GROUP_INCOME, GROUP_EXCLUDED)


@dataclass(frozen=True)
class CategoryDef:
    name: str
    group: str


# User-visible taxonomy — exactly as specified by the broker.
COMMITTED_CATEGORIES: tuple[str, ...] = (
    "Other mortgage / Rent",
    "Spousal / Child maintenance",
    "Electricity / Gas / Oil",
    "Water",
    "Communications",
    "Television",
    "Council tax",
    "Car costs",
    "Other transport costs",
    "Service charge / Ground rent",
)

DISCRETIONARY_CATEGORIES: tuple[str, ...] = (
    "Food",
    "Clothing",
    "Household maintenance",
    "Entertainment",
    "Child care",
    "Holidays",
    "Pension contributions",
    "Investments",
    "Insurances",
)

# Risk categories — reserved for flag-driven escalation. Gambling and
# Fast Payments are now transaction-level flags rather than categories, so
# this set is empty. Kept as a named constant so callers keep compiling.
RISK_CATEGORIES: frozenset[str] = frozenset()

# Internal categories — filtered out of committed/discretionary totals.
INCOME_CATEGORIES: tuple[str, ...] = ("Salary/Wages", "Other income")
EXCLUDED_CATEGORIES: tuple[str, ...] = ("Transfer/Excluded",)


def all_categories() -> list[CategoryDef]:
    out: list[CategoryDef] = []
    for name in COMMITTED_CATEGORIES:
        out.append(CategoryDef(name, GROUP_COMMITTED))
    for name in DISCRETIONARY_CATEGORIES:
        out.append(CategoryDef(name, GROUP_DISCRETIONARY))
    for name in INCOME_CATEGORIES:
        out.append(CategoryDef(name, GROUP_INCOME))
    for name in EXCLUDED_CATEGORIES:
        out.append(CategoryDef(name, GROUP_EXCLUDED))
    return out


def category_names() -> set[str]:
    return {c.name for c in all_categories()}


def group_of(category: str) -> str:
    # Unknown or retired categories fall back to "discretionary" so callers
    # (e.g. apply_correction when a user confirms a row whose category was
    # retired in a later release) don't crash. The row stays flagged for
    # re-review via the normal needs_review path.
    for c in all_categories():
        if c.name == category:
            return c.group
    return GROUP_DISCRETIONARY


def user_visible_categories() -> list[str]:
    """The 19 categories shown in the UI dropdown (Committed + Discretionary)."""
    return list(COMMITTED_CATEGORIES) + list(DISCRETIONARY_CATEGORIES)
