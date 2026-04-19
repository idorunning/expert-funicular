"""Seed reference data (taxonomy) and sensible bootstrap rules."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..categorize.taxonomy import all_categories
from .engine import session_scope
from .models import Category, MerchantRule, utcnow


# High-confidence starter rules for very common UK merchants.
# Keys are the normalized merchant (uppercase, simplified).
_BOOTSTRAP_RULES: dict[str, str] = {
    "THAMES WATER": "Water",
    "ANGLIAN WATER": "Water",
    "SEVERN TRENT": "Water",
    "YORKSHIRE WATER": "Water",
    "SCOTTISH WATER": "Water",
    "UNITED UTILITIES": "Water",
    "BRITISH GAS": "Electricity / Gas / Oil",
    "OCTOPUS ENERGY": "Electricity / Gas / Oil",
    "EDF ENERGY": "Electricity / Gas / Oil",
    "EON": "Electricity / Gas / Oil",
    "E.ON": "Electricity / Gas / Oil",
    "SCOTTISH POWER": "Electricity / Gas / Oil",
    "OVO ENERGY": "Electricity / Gas / Oil",
    "BULB ENERGY": "Electricity / Gas / Oil",
    "COUNCIL TAX": "Council tax",
    "TV LICENCE": "Television",
    "TV LICENSING": "Television",
    "SKY DIGITAL": "Television",
    "NETFLIX": "Entertainment",
    "SPOTIFY": "Entertainment",
    "DISNEY PLUS": "Entertainment",
    "AMAZON PRIME": "Entertainment",
    "BT GROUP": "Communications",
    "VIRGIN MEDIA": "Communications",
    "EE LIMITED": "Communications",
    "VODAFONE": "Communications",
    "O2": "Communications",
    "THREE": "Communications",
    "TESCO": "Food",
    "SAINSBURYS": "Food",
    "SAINSBURY'S": "Food",
    "ASDA": "Food",
    "MORRISONS": "Food",
    "WAITROSE": "Food",
    "ALDI": "Food",
    "LIDL": "Food",
    "MARKS SPENCER": "Food",
    "M AND S": "Food",
    "CO-OP": "Food",
    "TFL": "Other transport costs",
    "TRANSPORT FOR LONDON": "Other transport costs",
    "UBER": "Other transport costs",
    "NATIONAL RAIL": "Other transport costs",
    "TRAINLINE": "Other transport costs",
    "BP": "Car costs",
    "SHELL": "Car costs",
    "ESSO": "Car costs",
    "TEXACO": "Car costs",
    "DVLA": "Car costs",
    "AVIVA": "Insurances",
    "DIRECT LINE": "Insurances",
    "ADMIRAL": "Insurances",
    "LV INSURANCE": "Insurances",
    "NEST PENSION": "Pension contributions",
    "ROYAL LONDON": "Pension contributions",
    "VANGUARD": "Investments",
    "HARGREAVES LANSDOWN": "Investments",
    "AJ BELL": "Investments",
}


def seed_categories(session: Session) -> int:
    existing = set(session.execute(select(Category.name)).scalars())
    added = 0
    for idx, cat in enumerate(all_categories()):
        if cat.name in existing:
            continue
        session.add(Category(name=cat.name, group_name=cat.group, sort_order=idx))
        added += 1
    session.commit()
    return added


def seed_bootstrap_rules(session: Session) -> int:
    existing = {
        (m, c)
        for m, c in session.execute(
            select(MerchantRule.merchant_normalized, MerchantRule.category).where(
                MerchantRule.scope == "global"
            )
        ).all()
    }
    added = 0
    now = utcnow()
    for merchant, category in _BOOTSTRAP_RULES.items():
        if (merchant, category) in existing:
            continue
        session.add(
            MerchantRule(
                merchant_normalized=merchant,
                category=category,
                weight=2,
                scope="global",
                client_id=None,
                created_by=None,
                created_at=now,
                last_seen_at=now,
            )
        )
        added += 1
    session.commit()
    return added


def run_all_seeds() -> None:
    with session_scope() as s:
        seed_categories(s)
        seed_bootstrap_rules(s)
