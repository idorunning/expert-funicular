"""Mapping from category names to the SVG icon that illustrates them.

The icons live in ``brokerledger/gui/assets/icons/`` and are rendered in the
icon-grid category picker in the Review view.
"""
from __future__ import annotations

from pathlib import Path

ICONS_DIR = Path(__file__).resolve().parent.parent / "gui" / "assets" / "icons"

CATEGORY_ICON_FILES: dict[str, str] = {
    # Committed
    "Other mortgage / Rent":           "other_mortgage_rent.svg",
    "Spousal / Child maintenance":     "spousal_child_maintenance.svg",
    "Electricity / Gas / Oil":         "electricity_gas_oil.svg",
    "Water":                           "water.svg",
    "Communications":                  "communications.svg",
    "Television":                      "television.svg",
    "Council tax":                     "council_tax.svg",
    "Car costs":                       "car_costs.svg",
    "Other transport costs":           "other_transport_costs.svg",
    "Service charge / Ground rent":    "service_charge_ground_rent.svg",
    # Discretionary
    "Food":                            "food.svg",
    "Clothing":                        "clothing.svg",
    "Household maintenance":           "household_maintenance.svg",
    "Entertainment":                   "entertainment.svg",
    "Child care":                      "child_care.svg",
    "Holidays":                        "holidays.svg",
    "Pension contributions":           "pension_contributions.svg",
    "Investments":                     "investments.svg",
    "Insurances":                      "insurances.svg",
}

_FALLBACK = "other.svg"


def icon_path_for(category: str) -> Path:
    """Return the absolute path to the SVG icon for ``category``. Falls back
    to the generic "other" icon if the category has no dedicated artwork."""
    filename = CATEGORY_ICON_FILES.get(category, _FALLBACK)
    return ICONS_DIR / filename
