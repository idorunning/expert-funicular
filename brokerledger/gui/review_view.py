"""Review view — table of transactions with inline category editing."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from ..auth.session import require_login
from ..categorize.memory import apply_correction
from ..categorize.taxonomy import group_of, user_visible_categories
from ..db.engine import session_scope
from ..db.models import Transaction
from .widgets.category_delegate import CategoryDelegate


COLUMNS = [
    ("Date", 90),
    ("Description", 340),
    ("Merchant", 200),
    ("Amount (GBP)", 100),
    ("Category", 200),
    ("Group", 110),
    ("Confidence", 110),
    ("Needs review", 90),
]


def confidence_tier(confidence: float | None, source: str | None) -> str:
    if confidence is None:
        return "—"
    if source in ("rule", "user") and confidence >= 0.99:
        return "Confirmed"
    if confidence >= 0.85:
        return "High"
    if confidence >= 0.70:
        return "Medium"
    return "Low"


_TIER_BG = {
    "Confirmed": QColor(210, 240, 215),   # green
    "High":      QColor(225, 245, 220),   # pale green
    "Medium":    QColor(255, 244, 214),   # amber
    "Low":       QColor(250, 220, 210),   # red/pink
}
_TIER_FG = {
    "Confirmed": QColor(20, 95, 40),
    "High":      QColor(50, 110, 55),
    "Medium":    QColor(150, 100, 10),
    "Low":       QColor(165, 45, 30),
}


class TransactionsModel(QAbstractTableModel):
    def __init__(self, client_id: int, flagged_only: bool = False) -> None:
        super().__init__()
        self.client_id = client_id
        self.flagged_only = flagged_only
        self._rows: list[dict] = []
        self.reload()

    def reload(self) -> None:
        with session_scope() as s:
            q = select(Transaction).where(Transaction.client_id == self.client_id)
            if self.flagged_only:
                q = q.where(Transaction.needs_review == 1)
            q = q.order_by(Transaction.posted_date.asc(), Transaction.id.asc())
            rows = s.execute(q).scalars().all()
            self.beginResetModel()
            self._rows = [
                {
                    "id": r.id,
                    "date": r.posted_date,
                    "desc": r.description_raw,
                    "merchant": r.merchant_normalized,
                    "amount": r.amount,
                    "category": r.category or "",
                    "group": r.category_group or "",
                    "confidence": r.confidence,
                    "source": r.source,
                    "needs_review": bool(r.needs_review),
                    "direction": r.direction,
                }
                for r in rows
            ]
            self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section][0]
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == 0:
                return row["date"]
            if col == 1:
                return row["desc"]
            if col == 2:
                return row["merchant"]
            if col == 3:
                v: Decimal = row["amount"]
                return f"{v:+.2f}"
            if col == 4:
                return row["category"]
            if col == 5:
                return row["group"]
            if col == 6:
                return confidence_tier(row["confidence"], row["source"])
            if col == 7:
                return "!" if row["needs_review"] else ""
        if role == Qt.ItemDataRole.BackgroundRole:
            if col == 6:
                tier = confidence_tier(row["confidence"], row["source"])
                if tier in _TIER_BG:
                    return QBrush(_TIER_BG[tier])
            if row["needs_review"]:
                return QBrush(QColor(255, 246, 225))
        if role == Qt.ItemDataRole.ForegroundRole and col == 6:
            tier = confidence_tier(row["confidence"], row["source"])
            if tier in _TIER_FG:
                return QBrush(_TIER_FG[tier])
        if role == Qt.ItemDataRole.FontRole and col in (6, 7):
            tier = confidence_tier(row["confidence"], row["source"])
            if col == 6 and tier in _TIER_BG:
                f = QFont()
                f.setBold(True)
                return f
            if col == 7 and row["needs_review"]:
                f = QFont()
                f.setBold(True)
                return f
        if role == Qt.ItemDataRole.ForegroundRole and col == 7 and row["needs_review"]:
            return QBrush(QColor(180, 90, 0))
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 3:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 6:
            return Qt.AlignmentFlag.AlignCenter
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == 4:  # Category editable
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or index.column() != 4:
            return False
        new_category = str(value).strip()
        if not new_category or new_category not in user_visible_categories():
            return False
        row = self._rows[index.row()]
        user = require_login()
        with session_scope() as s:
            tx = s.get(Transaction, row["id"])
            if tx is None:
                return False
            apply_correction(s, tx=tx, new_category=new_category, user_id=user.id)
            s.commit()
        row["category"] = new_category
        row["group"] = group_of(new_category)
        row["needs_review"] = False
        row["confidence"] = 1.0
        row["source"] = "user"
        top_left = self.index(index.row(), 0)
        bottom_right = self.index(index.row(), self.columnCount() - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])
        return True


class ReviewView(QWidget):
    back_requested = Signal()
    export_requested = Signal()
    affordability_requested = Signal()

    def __init__(
        self,
        client_id: int,
        client_name: str,
        *,
        flagged_only: bool = False,
        flagged_count: int | None = None,
    ) -> None:
        super().__init__()
        self.client_id = client_id
        self.setWindowTitle(f"Review — {client_name}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Magenta call-out when we arrive here because the importer flagged rows
        # for human review. Hidden by default.
        self.flagged_banner = QLabel()
        self.flagged_banner.setWordWrap(True)
        self.flagged_banner.setStyleSheet(
            "QLabel { background-color: #F5E6F0; color: #5A1044;"
            " border: 1px solid #C33E8F; border-radius: 6px;"
            " padding: 10px 14px; font-weight: 600; }"
        )
        self.flagged_banner.setVisible(False)
        layout.addWidget(self.flagged_banner)

        header = QHBoxLayout()
        header.setSpacing(12)
        self.title = QLabel(f"Transactions — {client_name}")
        self.title.setStyleSheet(
            "QLabel { font-size: 20px; font-weight: 600; color: #1F1030; }"
        )
        header.addWidget(self.title)
        header.addStretch(1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        header.addWidget(self.summary)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self.back_requested.emit)
        toolbar.addWidget(back)
        self.flag_only = QCheckBox("Flagged only")
        self.flag_only.toggled.connect(self._on_flag_toggled)
        toolbar.addWidget(self.flag_only)
        toolbar.addStretch(1)
        afford = QPushButton("Affordability")
        afford.clicked.connect(self.affordability_requested.emit)
        toolbar.addWidget(afford)
        export = QPushButton("Export XLSX…")
        export.clicked.connect(self.export_requested.emit)
        toolbar.addWidget(export)
        layout.addLayout(toolbar)

        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.SelectedClicked |
                                   QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setItemDelegateForColumn(4, CategoryDelegate(self.table))
        self.model = TransactionsModel(client_id=client_id, flagged_only=flagged_only)
        self.table.setModel(self.model)
        for i, (_name, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, width)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.refresh_summary()
        self.model.dataChanged.connect(lambda *_: self.refresh_summary())

        if flagged_only:
            # Silence the toggled signal so it doesn't re-reload the model
            # we just built with the right filter.
            self.flag_only.blockSignals(True)
            self.flag_only.setChecked(True)
            self.flag_only.blockSignals(False)
        if flagged_count is not None and flagged_count > 0:
            noun = "transaction" if flagged_count == 1 else "transactions"
            self.flagged_banner.setText(
                f"⚑ {flagged_count} {noun} need your review. "
                "Everything else was auto-categorised with high confidence."
            )
            self.flagged_banner.setVisible(True)

    def _on_flag_toggled(self, checked: bool) -> None:
        self.model.flagged_only = checked
        self.model.reload()
        self.refresh_summary()
        # Once the user steers away from the flagged view, drop the banner —
        # it's a one-shot call-out, not a persistent status bar.
        if not checked:
            self.flagged_banner.setVisible(False)

    def refresh_summary(self) -> None:
        with session_scope() as s:
            total = s.execute(
                select(Transaction).where(Transaction.client_id == self.client_id)
            ).scalars().all()
        flagged = sum(1 for t in total if t.needs_review)
        tiers = {"Confirmed": 0, "High": 0, "Medium": 0, "Low": 0, "—": 0}
        for t in total:
            tiers[confidence_tier(t.confidence, t.source)] += 1
        self.summary.setText(
            f"<span style='color:#555'>{len(total)} transactions &middot; "
            f"<span style='color:#145f28'><b>{tiers['Confirmed']}</b> confirmed</span> &middot; "
            f"<span style='color:#326e37'><b>{tiers['High']}</b> high</span> &middot; "
            f"<span style='color:#96640a'><b>{tiers['Medium']}</b> medium</span> &middot; "
            f"<span style='color:#a52d1e'><b>{tiers['Low']}</b> low</span> &middot; "
            f"<b style='color:#b4580a'>{flagged} flagged</b></span>"
        )
