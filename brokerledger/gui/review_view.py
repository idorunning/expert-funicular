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
    ("Date",         90),
    ("Description", 320),
    ("Merchant",    200),
    ("Amount",      100),
    ("Category",    210),
    ("Group",       120),
    ("Certainty",   110),
    ("Flagged",      70),
]

COL_DATE    = 0
COL_DESC    = 1
COL_MERC    = 2
COL_AMT     = 3
COL_CAT     = 4
COL_GROUP   = 5
COL_CERT    = 6
COL_FLAGGED = 7


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
    "Confirmed": QColor(210, 240, 215),
    "High":      QColor(225, 245, 220),
    "Medium":    QColor(255, 244, 214),
    "Low":       QColor(250, 220, 210),
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
                    "id":          r.id,
                    "date":        r.posted_date,
                    "desc":        r.description_raw,
                    "merchant":    r.merchant_normalized,
                    "amount":      r.amount,
                    "category":    r.category or "",
                    "group":       r.category_group or "",
                    "confidence":  r.confidence,
                    "source":      r.source,
                    "needs_review": bool(r.needs_review),
                    "direction":   r.direction,
                }
                for r in rows
            ]
            self.endResetModel()

    # ------------------------------------------------------------------
    # Qt model interface
    # ------------------------------------------------------------------

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
            if col == COL_DATE:    return row["date"]
            if col == COL_DESC:    return row["desc"]
            if col == COL_MERC:    return row["merchant"]
            if col == COL_AMT:
                v: Decimal = row["amount"]
                return f"{v:+.2f}"
            if col == COL_CAT:     return row["category"]
            if col == COL_GROUP:   return row["group"]
            if col == COL_CERT:    return confidence_tier(row["confidence"], row["source"])
            if col == COL_FLAGGED: return "!" if row["needs_review"] else ""
        if role == Qt.ItemDataRole.BackgroundRole:
            if col == COL_CERT:
                tier = confidence_tier(row["confidence"], row["source"])
                if tier in _TIER_BG:
                    return QBrush(_TIER_BG[tier])
            if row["needs_review"]:
                return QBrush(QColor(255, 246, 225))
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == COL_CERT:
                tier = confidence_tier(row["confidence"], row["source"])
                if tier in _TIER_FG:
                    return QBrush(_TIER_FG[tier])
            if col == COL_FLAGGED and row["needs_review"]:
                return QBrush(QColor(180, 90, 0))
        if role == Qt.ItemDataRole.FontRole:
            tier = confidence_tier(row["confidence"], row["source"])
            if col == COL_CERT and tier in _TIER_BG:
                f = QFont(); f.setBold(True); return f
            if col == COL_FLAGGED and row["needs_review"]:
                f = QFont(); f.setBold(True); return f
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == COL_AMT:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            if col in (COL_CERT, COL_FLAGGED):
                return Qt.AlignmentFlag.AlignCenter
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() == COL_CAT:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value,  # noqa: N802
                role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or index.column() != COL_CAT:
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

        # Update the edited row in memory.
        self._apply_in_memory(index.row(), new_category, source="user", confidence=1.0,
                               needs_review=False)
        top_left     = self.index(index.row(), 0)
        bottom_right = self.index(index.row(), self.columnCount() - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])

        # Auto-propagate to every other non-user row with the same merchant.
        merchant = row["merchant"]
        self._propagate_to_matching_merchant(merchant, new_category)
        return True

    # ------------------------------------------------------------------
    # Bulk confirm (no category change, just mark as user-confirmed)
    # ------------------------------------------------------------------

    def confirm_rows(self, row_indices: list[int]) -> int:
        """Mark the given row indices as user-confirmed.

        Only rows where the category is already assigned are affected.
        Returns the number of rows updated.
        """
        updated = 0
        user = require_login()
        for i in row_indices:
            if i < 0 or i >= len(self._rows):
                continue
            row = self._rows[i]
            category = row["category"]
            if not category:
                continue
            if row["source"] == "user" and row["confidence"] >= 1.0:
                continue  # already confirmed
            with session_scope() as s:
                tx = s.get(Transaction, row["id"])
                if tx is None:
                    continue
                apply_correction(s, tx=tx, new_category=category, user_id=user.id)
                s.commit()
            self._apply_in_memory(i, category, source="user", confidence=1.0,
                                   needs_review=False)
            top_left     = self.index(i, 0)
            bottom_right = self.index(i, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])
            updated += 1
            # Propagate to matching merchant rows too.
            self._propagate_to_matching_merchant(row["merchant"], category)
        return updated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_in_memory(self, row_idx: int, category: str, *, source: str,
                          confidence: float, needs_review: bool) -> None:
        row = self._rows[row_idx]
        row["category"]     = category
        row["group"]        = group_of(category)
        row["source"]       = source
        row["confidence"]   = confidence
        row["needs_review"] = needs_review

    def _propagate_to_matching_merchant(self, merchant: str, new_category: str) -> None:
        """Update all non-user rows sharing the same normalised merchant name.

        Writes the new category to the DB for each affected row so the
        rule is consistently applied, then updates the in-memory cache
        and emits dataChanged for the affected rows.
        """
        if not merchant:
            return
        affected: list[int] = []
        user = require_login()
        for i, row in enumerate(self._rows):
            if row["merchant"] != merchant:
                continue
            if row["source"] == "user":
                continue   # never overwrite human corrections
            if row["category"] == new_category and not row["needs_review"]:
                continue   # already correct
            with session_scope() as s:
                tx = s.get(Transaction, row["id"])
                if tx is None:
                    continue
                apply_correction(s, tx=tx, new_category=new_category, user_id=user.id)
                s.commit()
            self._apply_in_memory(i, new_category, source="rule", confidence=0.99,
                                   needs_review=False)
            affected.append(i)

        if affected:
            for i in affected:
                tl = self.index(i, 0)
                br = self.index(i, self.columnCount() - 1)
                self.dataChanged.emit(tl, br, [Qt.ItemDataRole.DisplayRole])


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

        # ── Flagged-row banner ────────────────────────────────────────────
        self.flagged_banner = QLabel()
        self.flagged_banner.setWordWrap(True)
        self.flagged_banner.setStyleSheet(
            "QLabel { background-color: #F5E6F0; color: #5A1044;"
            " border: 1px solid #C33E8F; border-radius: 6px;"
            " padding: 10px 14px; font-weight: 600; }"
        )
        self.flagged_banner.setVisible(False)
        layout.addWidget(self.flagged_banner)

        # ── Top header ────────────────────────────────────────────────────
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

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        back = QPushButton("← Back")
        back.clicked.connect(self.back_requested.emit)
        toolbar.addWidget(back)

        self.flag_only = QCheckBox("Flagged only")
        self.flag_only.toggled.connect(self._on_flag_toggled)
        toolbar.addWidget(self.flag_only)

        toolbar.addStretch(1)

        # Confirm selected — marks the selected rows as user-confirmed without
        # changing their current category.
        self.confirm_btn = QPushButton("✓  Confirm selected")
        self.confirm_btn.setToolTip(
            "Mark the selected transaction(s) as confirmed — keeps the current "
            "category and tells the system you agree with it."
        )
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self._confirm_selected)
        toolbar.addWidget(self.confirm_btn)

        # Refresh — re-read all rows from the database.
        refresh_btn = QPushButton("↻  Refresh")
        refresh_btn.setToolTip("Reload all transactions from the database.")
        refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(refresh_btn)

        afford = QPushButton("Affordability")
        afford.clicked.connect(self.affordability_requested.emit)
        toolbar.addWidget(afford)

        export = QPushButton("Export XLSX…")
        export.clicked.connect(self.export_requested.emit)
        toolbar.addWidget(export)

        layout.addLayout(toolbar)

        # ── Hint label ────────────────────────────────────────────────────
        hint = QLabel(
            "<span style='color:#6B6679;font-size:12px'>"
            "Click a row to select it, then pick a category from the dropdown that "
            "appears — or click <b>Confirm selected</b> if the AI already got it right. "
            "Matching merchants update automatically."
            "</span>"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── Table ─────────────────────────────────────────────────────────
        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)

        # Open the category dropdown on a single click (CurrentChanged fires
        # when the selected row changes) as well as on a direct cell click.
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.DoubleClicked
        )

        self._delegate = CategoryDelegate(self.table)
        self.table.setItemDelegateForColumn(COL_CAT, self._delegate)

        self.model = TransactionsModel(client_id=client_id, flagged_only=flagged_only)
        self.table.setModel(self.model)

        for i, (_name, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, width)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_DESC, QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setSectionResizeMode(
            COL_MERC, QHeaderView.ResizeMode.Interactive
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)

        layout.addWidget(self.table)

        # ── Wire up signals ───────────────────────────────────────────────
        self.model.dataChanged.connect(lambda *_: self.refresh_summary())
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self.refresh_summary()

        # Apply initial filter state without retriggering signals.
        if flagged_only:
            self.flag_only.blockSignals(True)
            self.flag_only.setChecked(True)
            self.flag_only.blockSignals(False)

        if flagged_count is not None and flagged_count > 0:
            noun = "transaction" if flagged_count == 1 else "transactions"
            self.flagged_banner.setText(
                f"⚑ {flagged_count} {noun} need your review. "
                "Everything else was assigned automatically with high certainty."
            )
            self.flagged_banner.setVisible(True)

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _confirm_selected(self) -> None:
        indices = self.table.selectionModel().selectedRows()
        if not indices:
            return
        row_nums = sorted({i.row() for i in indices})
        updated = self.model.confirm_rows(row_nums)
        if updated:
            self.refresh_summary()

    def _refresh(self) -> None:
        self.model.reload()
        self.refresh_summary()

    def _on_flag_toggled(self, checked: bool) -> None:
        self.model.flagged_only = checked
        self.model.reload()
        self.refresh_summary()
        if not checked:
            self.flagged_banner.setVisible(False)

    def _on_selection_changed(self, _selected, _deselected) -> None:
        has = bool(self.table.selectionModel().selectedRows())
        self.confirm_btn.setEnabled(has)

    # ------------------------------------------------------------------
    # Summary bar
    # ------------------------------------------------------------------

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
        # Update the banner count if it's still visible.
        if self.flagged_banner.isVisible() and flagged == 0:
            self.flagged_banner.setText(
                "✓ All flagged transactions have been reviewed — nicely done!"
            )
            self.flagged_banner.setStyleSheet(
                "QLabel { background-color: #E6F4EA; color: #145f28;"
                " border: 1px solid #6AC87A; border-radius: 6px;"
                " padding: 10px 14px; font-weight: 600; }"
            )
