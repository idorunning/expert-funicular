"""Affordability calculator — per-category/group totals + monthly averaging."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from ..categorize.taxonomy import (
    COMMITTED_CATEGORIES,
    DISCRETIONARY_CATEGORIES,
    GROUP_COMMITTED,
    GROUP_DISCRETIONARY,
    GROUP_EXCLUDED,
    GROUP_INCOME,
    group_of,
)
from ..db.engine import session_scope
from ..db.models import Transaction


@dataclass
class CategoryTotal:
    category: str
    group: str
    count: int = 0
    total: Decimal = Decimal("0.00")  # positive outgoing, positive income


@dataclass
class AffordabilityReport:
    client_id: int
    period_start: date | None
    period_end: date | None
    months_in_window: float
    income_total: Decimal = Decimal("0.00")
    committed_total: Decimal = Decimal("0.00")
    discretionary_total: Decimal = Decimal("0.00")
    excluded_total: Decimal = Decimal("0.00")
    per_category: dict[str, CategoryTotal] = field(default_factory=dict)
    declared_income: Decimal | None = None

    @property
    def outgoings_total(self) -> Decimal:
        return self.committed_total + self.discretionary_total

    @property
    def net_disposable(self) -> Decimal:
        income = self.declared_income if self.declared_income is not None else self.income_total
        return income - self.outgoings_total

    @property
    def monthly_income(self) -> Decimal:
        if self.months_in_window <= 0:
            return self.income_total
        return (self.income_total / Decimal(str(self.months_in_window))).quantize(Decimal("0.01"))

    @property
    def monthly_committed(self) -> Decimal:
        if self.months_in_window <= 0:
            return self.committed_total
        return (self.committed_total / Decimal(str(self.months_in_window))).quantize(Decimal("0.01"))

    @property
    def monthly_discretionary(self) -> Decimal:
        if self.months_in_window <= 0:
            return self.discretionary_total
        return (self.discretionary_total / Decimal(str(self.months_in_window))).quantize(Decimal("0.01"))

    @property
    def monthly_net_disposable(self) -> Decimal:
        if self.months_in_window <= 0:
            return self.net_disposable
        return (self.net_disposable / Decimal(str(self.months_in_window))).quantize(Decimal("0.01"))


def _months_between(start: date, end: date) -> float:
    if end < start:
        return 0.0
    whole_months = (end.year - start.year) * 12 + (end.month - start.month)
    whole_months += (end.day - start.day) / 30.0
    return max(whole_months, 1.0)


def compute_for_client(
    client_id: int,
    declared_income: Decimal | None = None,
    *,
    date_start: date | None = None,
    date_end: date | None = None,
) -> AffordabilityReport:
    with session_scope() as s:
        q = select(Transaction).where(Transaction.client_id == client_id)
        if date_start is not None:
            q = q.where(Transaction.posted_date >= date_start.isoformat())
        if date_end is not None:
            q = q.where(Transaction.posted_date <= date_end.isoformat())
        rows = s.execute(q).scalars().all()

    if not rows:
        return AffordabilityReport(
            client_id=client_id,
            period_start=None,
            period_end=None,
            months_in_window=0.0,
            declared_income=declared_income,
        )

    dates = sorted({date.fromisoformat(r.posted_date) for r in rows})
    start, end = dates[0], dates[-1]
    months = _months_between(start, end)

    per_cat: dict[str, CategoryTotal] = {}
    for c in list(COMMITTED_CATEGORIES) + list(DISCRETIONARY_CATEGORIES):
        per_cat[c] = CategoryTotal(category=c, group=group_of(c))

    income = Decimal("0.00")
    committed = Decimal("0.00")
    discretionary = Decimal("0.00")
    excluded = Decimal("0.00")
    for r in rows:
        amount = r.amount or Decimal("0.00")
        category = r.category
        group = r.category_group or (group_of(category) if category else None)
        if group == GROUP_INCOME:
            income += amount if amount > 0 else -amount
            continue
        if group == GROUP_EXCLUDED or category is None:
            excluded += abs(amount)
            continue
        abs_amount = abs(amount)
        if category not in per_cat:
            per_cat[category] = CategoryTotal(category=category, group=group or "committed")
        per_cat[category].count += 1
        per_cat[category].total += abs_amount
        if group == GROUP_COMMITTED:
            committed += abs_amount
        elif group == GROUP_DISCRETIONARY:
            discretionary += abs_amount

    return AffordabilityReport(
        client_id=client_id,
        period_start=start,
        period_end=end,
        months_in_window=months,
        income_total=income,
        committed_total=committed,
        discretionary_total=discretionary,
        excluded_total=excluded,
        per_category=per_cat,
        declared_income=declared_income,
    )
