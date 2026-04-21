"""Fixed affordability taxonomy used throughout the app."""
from __future__ import annotations

from dataclasses import dataclass, field

GROUP_COMMITTED = "committed"
GROUP_DISCRETIONARY = "discretionary"
GROUP_INCOME = "income"
GROUP_EXCLUDED = "excluded"

ALL_GROUPS = (GROUP_COMMITTED, GROUP_DISCRETIONARY, GROUP_INCOME, GROUP_EXCLUDED)


@dataclass(frozen=True)
class CategoryDef:
    name: str
    group: str
    includes: str = ""


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


# One-line "includes" hints per category. These feed the LLM system prompt
# so a small local model can reason from the taxonomy itself — e.g. map
# "POCKET MONEY" to Child care via semantic similarity to "pocket money,
# allowance" in the description — instead of needing a worked example per
# household term. The same strings drive the category-picker tooltips so
# the broker and the model share one definition. Keep each value a single
# comma-separated line, plain-English, lowercase-friendly, UK-specific.
CATEGORY_INCLUDES: dict[str, str] = {
    # Committed ----------------------------------------------------------
    "Other mortgage / Rent": (
        "monthly mortgage payments, residential rent, buy-to-let mortgage, "
        "rent to landlord or letting agent"
    ),
    "Spousal / Child maintenance": (
        "court-ordered maintenance, CMS / Child Maintenance Service, "
        "spousal support, ex-partner maintenance"
    ),
    "Electricity / Gas / Oil": (
        "energy bills (British Gas, EDF, Octopus, E.ON, Scottish Power, "
        "Ovo, Bulb, SSE, EDF Energy), heating oil, LPG, dual-fuel tariffs"
    ),
    "Water": (
        "water rates and sewerage (Thames Water, Severn Trent, Anglian "
        "Water, United Utilities, Yorkshire Water, Southern Water, "
        "Northumbrian Water, Welsh Water / Dwr Cymru, Scottish Water)"
    ),
    "Communications": (
        "broadband, landline, mobile phone, SIM-only (BT, Sky, Virgin "
        "Media, Vodafone, EE, O2, Three, TalkTalk, Plusnet, Giffgaff)"
    ),
    "Television": (
        "TV licence, Sky TV, BT TV, Virgin Media TV package, Now TV, "
        "streaming bundles billed as TV"
    ),
    "Council tax": (
        "council tax payments to local authority (e.g. LBHF, Westminster, "
        "Manchester City Council, Bristol CC)"
    ),
    "Car costs": (
        "fuel / petrol / diesel (BP, Shell, Esso, Texaco, Asda fuel), "
        "car insurance premiums, road tax / DVLA, MOT, servicing, parking, "
        "congestion charge, ULEZ, car finance / PCP / HP, car lease"
    ),
    "Other transport costs": (
        "train tickets (Trainline, LNER, GWR, Avanti, Southeastern), "
        "Transport for London (TfL, Oyster, contactless tap), bus, tram, "
        "tube, taxi / Uber / Bolt, rail season ticket"
    ),
    "Service charge / Ground rent": (
        "leasehold service charge, ground rent to freeholder, "
        "block-management fees, estate-management charges"
    ),
    # Discretionary ------------------------------------------------------
    "Food": (
        "supermarkets (Tesco, Sainsbury's, Asda, Morrisons, Waitrose, "
        "M&S Food, Aldi, Lidl, Co-op, Iceland, Ocado), takeaways "
        "(Just Eat, Deliveroo, Uber Eats), cafes, restaurants, pub food"
    ),
    "Clothing": (
        "high-street clothing (Next, M&S, Primark, H&M, Zara, Uniqlo, "
        "John Lewis clothing), shoes, sportswear (JD Sports, Sports "
        "Direct), online fashion (ASOS, Boohoo)"
    ),
    "Household maintenance": (
        "DIY and home repairs (B&Q, Screwfix, Homebase, Wickes), "
        "furniture (IKEA, DFS, Dunelm), appliances, cleaning, gardening, "
        "Argos general household, Amazon general household"
    ),
    "Entertainment": (
        "streaming (Netflix, Disney+, Spotify, Apple Music, Amazon "
        "Prime), cinema, theatre, concerts, gym, subscriptions, games "
        "(Steam, PlayStation, Xbox), hobbies"
    ),
    "Child care": (
        "pocket money, child's allowance, nursery fees, childminder, "
        "school fees, after-school clubs, school uniform, school meals, "
        "tuition, kids' swimming / music / dance lessons, kids' birthday "
        "presents, baby essentials (nappies, formula)"
    ),
    "Holidays": (
        "flights (easyJet, Ryanair, British Airways, Jet2), hotels "
        "(Booking.com, Hotels.com, Airbnb, Premier Inn, Travelodge), "
        "package holidays (TUI, Jet2 Holidays, loveholidays), travel "
        "insurance bought for a trip, theme-park tickets"
    ),
    "Pension contributions": (
        "workplace or private pension contributions (NEST, People's "
        "Pension, Aviva Pension, Scottish Widows, Standard Life, Aegon, "
        "Legal & General Pension, SIPP)"
    ),
    "Investments": (
        "ISA contributions, share-dealing transfers (Vanguard, "
        "Hargreaves Lansdown, AJ Bell, Freetrade, Trading 212, "
        "interactive investor, Nutmeg, Moneybox), crypto exchange "
        "top-ups (Coinbase, Kraken, Binance)"
    ),
    "Insurances": (
        "home / contents / buildings insurance, life insurance, critical "
        "illness, income protection, pet insurance (Aviva, Direct Line, "
        "LV=, Admiral, Churchill, More Than, Petplan). Car insurance "
        "belongs under Car costs."
    ),
    # Income -------------------------------------------------------------
    "Salary/Wages": (
        "employer wage or payroll credits (descriptions usually contain "
        "SALARY, WAGES, PAYROLL, PAY, REMUNERATION, often with the "
        "employer or the paying bank name)"
    ),
    "Other income": (
        "benefits (Universal Credit, Child Benefit, Tax Credits, PIP, "
        "State Pension), HMRC tax refund, self-employed invoice credits, "
        "rental income, dividends, interest received, any other credit "
        "that isn't a salary"
    ),
    # Excluded -----------------------------------------------------------
    "Transfer/Excluded": (
        "transfers between the client's own accounts, standing orders to "
        "a savings pot, credit-card balance repayments, Monzo / Starling "
        "pot top-ups, one-time refunds from a merchant"
    ),
}


def all_categories() -> list[CategoryDef]:
    out: list[CategoryDef] = []
    for name in COMMITTED_CATEGORIES:
        out.append(CategoryDef(name, GROUP_COMMITTED, CATEGORY_INCLUDES.get(name, "")))
    for name in DISCRETIONARY_CATEGORIES:
        out.append(CategoryDef(name, GROUP_DISCRETIONARY, CATEGORY_INCLUDES.get(name, "")))
    for name in INCOME_CATEGORIES:
        out.append(CategoryDef(name, GROUP_INCOME, CATEGORY_INCLUDES.get(name, "")))
    for name in EXCLUDED_CATEGORIES:
        out.append(CategoryDef(name, GROUP_EXCLUDED, CATEGORY_INCLUDES.get(name, "")))
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


def includes_for(category: str) -> str:
    """Return the one-line 'includes' hint for a category, or '' if unknown.

    Used by the system prompt (so the model can reason about vocabulary)
    and the category-picker tooltip (so the broker sees the same
    definition the model sees).
    """
    return CATEGORY_INCLUDES.get(category, "")
