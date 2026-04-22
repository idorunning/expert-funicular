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
    QSizePolicy,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from ..auth.session import require_login
from ..categorize.flags import deserialize_flags, flag_display_name
from ..categorize.memory import apply_correction
from ..categorize.siblings import apply_auto_siblings
from ..categorize.taxonomy import group_of, user_visible_categories
from ..db.engine import session_scope
from ..db.models import Transaction, utcnow
from .widgets.category_delegate import CategoryDelegate
from .widgets.category_grid_picker import CategoryGridDelegate
from .widgets.sibling_confirm_dialog import SiblingConfirmDialog


COLUMNS = [
    ("Date",         90),
    ("Description", 320),
    ("Merchant",    200),
    ("Amount",      100),
    ("Flags",       120),
    ("Category",    210),
    ("Group",       120),
    ("Certainty",   110),
    ("Flagged",      70),
]

COL_DATE    = 0
COL_DESC    = 1
COL_MERC    = 2
COL_AMT     = 3
COL_FLAGS   = 4
COL_CAT     = 5
COL_GROUP   = 6
COL_CERT    = 7
COL_FLAGGED = 8


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
                    "flags":       deserialize_flags(r.flags),
                    "reasoning":   r.reasoning or "",
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
            if col == COL_FLAGS:
                return " ".join(flag_display_name(f) for f in row["flags"])
            if col == COL_CAT:     return row["category"]
            if col == COL_GROUP:   return row["group"]
            if col == COL_CERT:    return confidence_tier(row["confidence"], row["source"])
            if col == COL_FLAGGED: return "!" if row["needs_review"] else ""
        if role == Qt.ItemDataRole.BackgroundRole:
            if col == COL_CERT:
                tier = confidence_tier(row["confidence"], row["source"])
                if tier in _TIER_BG:
                    return QBrush(_TIER_BG[tier])
            if col == COL_FLAGS and row["flags"]:
                from brokerledger.categorize.flags import FLAG_INBOUND
                only_inbound = row["flags"] == [FLAG_INBOUND]
                if only_inbound:
                    return QBrush(QColor(224, 242, 241))  # soft teal for inbound
                return QBrush(QColor(239, 231, 245))       # soft purple for risk flags
            if row["needs_review"]:
                return QBrush(QColor(255, 246, 225))
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == COL_CERT:
                tier = confidence_tier(row["confidence"], row["source"])
                if tier in _TIER_FG:
                    return QBrush(_TIER_FG[tier])
            if col == COL_FLAGS and row["flags"]:
                from brokerledger.categorize.flags import FLAG_INBOUND
                only_inbound = row["flags"] == [FLAG_INBOUND]
                if only_inbound:
                    return QBrush(QColor("#0B6E6E"))  # teal for inbound
                return QBrush(QColor("#4A1766"))       # purple for risk flags
            if col == COL_FLAGGED and row["needs_review"]:
                return QBrush(QColor(180, 90, 0))
        if role == Qt.ItemDataRole.FontRole:
            tier = confidence_tier(row["confidence"], row["source"])
            if col == COL_CERT and tier in _TIER_BG:
                f = QFont(); f.setBold(True); return f
            if col == COL_FLAGS and row["flags"]:
                f = QFont(); f.setBold(True); return f
            if col == COL_FLAGGED and row["needs_review"]:
                f = QFont(); f.setBold(True); return f
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == COL_AMT:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            if col in (COL_FLAGS, COL_CERT, COL_FLAGGED):
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
        outcome = None
        with session_scope() as s:
            tx = s.get(Transaction, row["id"])
            if tx is None:
                return False
            outcome = apply_correction(s, tx=tx, new_category=new_category, user_id=user.id)
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

        # Auto-siblings were already applied in apply_correction — reflect in UI.
        if outcome is not None and outcome.auto_siblings_count:
            self._reload_rows_by_id({c.tx_id for c in self._recent_auto_ids(outcome)})

        # Stash confirm candidates for the view to display a dialog.
        if outcome is not None and outcome.confirm_siblings:
            self._pending_confirm_siblings = (row["desc"], new_category, outcome.confirm_siblings)
        else:
            self._pending_confirm_siblings = None

        return True

    # ------------------------------------------------------------------
    # Sibling helpers
    # ------------------------------------------------------------------

    def _recent_auto_ids(self, outcome) -> list:
        # apply_auto_siblings doesn't echo back the candidate list; re-fetch
        # the relevant rows from DB in a follow-up pass. We keep it simple by
        # reloading any row in this client whose source is sibling_auto.
        with session_scope() as s:
            rows = s.execute(
                select(Transaction).where(
                    Transaction.client_id == self.client_id,
                    Transaction.source == "sibling_auto",
                )
            ).scalars().all()
            from ..categorize.siblings import SiblingCandidate
            return [
                SiblingCandidate(
                    tx_id=r.id,
                    description=r.description_raw,
                    merchant=r.merchant_normalized,
                    current_category=r.category,
                    score=int((r.confidence or 0) * 100),
                )
                for r in rows
            ]

    def _reload_rows_by_id(self, ids: set[int]) -> None:
        if not ids:
            return
        with session_scope() as s:
            rows = s.execute(
                select(Transaction).where(Transaction.id.in_(ids))
            ).scalars().all()
            by_id = {r.id: r for r in rows}
        for i, row in enumerate(self._rows):
            fresh = by_id.get(row["id"])
            if fresh is None:
                continue
            row["category"]     = fresh.category or ""
            row["group"]        = fresh.category_group or ""
            row["source"]       = fresh.source
            row["confidence"]   = fresh.confidence
            row["needs_review"] = bool(fresh.needs_review)
            row["flags"]        = deserialize_flags(fresh.flags)
            tl = self.index(i, 0)
            br = self.index(i, self.columnCount() - 1)
            self.dataChanged.emit(tl, br, [Qt.ItemDataRole.DisplayRole])

    def take_pending_confirm_siblings(self):
        """Return any pending confirm-siblings tuple and clear it."""
        val = getattr(self, "_pending_confirm_siblings", None)
        self._pending_confirm_siblings = None
        return val

    def on_tx_persisted(self, client_id: int, tx_id: int) -> None:
        """Live-update hook: called while an ingest/recategorize run is still
        in-flight. If the row belongs to this client, either update the
        existing entry or append a new one."""
        if client_id != self.client_id:
            return
        with session_scope() as s:
            tx = s.get(Transaction, tx_id)
            if tx is None:
                return
            payload = {
                "id":          tx.id,
                "date":        tx.posted_date,
                "desc":        tx.description_raw,
                "merchant":    tx.merchant_normalized,
                "amount":      tx.amount,
                "category":    tx.category or "",
                "group":       tx.category_group or "",
                "confidence":  tx.confidence,
                "source":      tx.source,
                "needs_review": bool(tx.needs_review),
                "direction":   tx.direction,
                "flags":       deserialize_flags(tx.flags),
                "reasoning":   tx.reasoning or "",
            }
        # Respect the flagged-only filter.
        if self.flagged_only and not payload["needs_review"]:
            return
        for i, row in enumerate(self._rows):
            if row["id"] == tx_id:
                self._rows[i] = payload
                tl = self.index(i, 0)
                br = self.index(i, self.columnCount() - 1)
                self.dataChanged.emit(tl, br, [Qt.ItemDataRole.DisplayRole])
                return
        insert_at = len(self._rows)
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self._rows.append(payload)
        self.endInsertRows()

    def apply_confirmed_siblings(self, new_category: str, candidates: list) -> int:
        """Apply ``new_category`` to user-confirmed sibling candidates."""
        if not candidates:
            return 0
        user = require_login()
        with session_scope() as s:
            tx_ids = [c.tx_id for c in candidates]
            first = s.get(Transaction, tx_ids[0]) if tx_ids else None
            if first is not None:
                apply_auto_siblings(
                    s, source_tx=first, new_category=new_category, candidates=candidates,
                )
            s.commit()
        self._reload_rows_by_id({c.tx_id for c in candidates})
        return len(candidates)

    # ------------------------------------------------------------------
    # Bulk confirm (no category change, just mark as user-confirmed)
    # ------------------------------------------------------------------

    def bulk_apply_category(self, row_indices: list[int], new_category: str) -> int:
        """Apply ``new_category`` to every row in ``row_indices``.

        Mirrors ``setData`` for the Category column but loops over many
        rows in one pass. Each change fires ``apply_correction`` so the
        merchant rules pick up the new mapping and siblings propagate.
        Returns the count of rows updated.
        """
        if not new_category or new_category not in user_visible_categories():
            return 0
        updated = 0
        user = require_login()
        for i in row_indices:
            if i < 0 or i >= len(self._rows):
                continue
            row = self._rows[i]
            if (row["category"] or "") == new_category and row["source"] == "user":
                continue
            with session_scope() as s:
                tx = s.get(Transaction, row["id"])
                if tx is None:
                    continue
                apply_correction(s, tx=tx, new_category=new_category, user_id=user.id)
                s.commit()
            self._apply_in_memory(i, new_category, source="user", confidence=1.0,
                                   needs_review=False)
            top_left = self.index(i, 0)
            bottom_right = self.index(i, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])
            self._propagate_to_matching_merchant(row["merchant"], new_category)
            updated += 1
        return updated

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

    def disregard_rows(self, row_indices: list[int]) -> int:
        """Mark selected transactions as professionally disregarded.

        Sets category to ``Transfer/Excluded`` (excluded from affordability)
        and source to ``'user'``, so the merchant rule is learned and future
        imports from the same source auto-disregard. Returns count updated.
        """
        _DISREGARD_CAT = "Transfer/Excluded"
        return self.bulk_apply_category(row_indices, _DISREGARD_CAT)

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

        # Apply category to selected — bulk-assigns a chosen category to
        # every selected row.
        self.apply_category_btn = QPushButton("🏷  Apply category to selected…")
        self.apply_category_btn.setToolTip(
            "Pick one category and apply it to every selected transaction."
        )
        self.apply_category_btn.setEnabled(False)
        self.apply_category_btn.clicked.connect(self._apply_category_to_selected)
        toolbar.addWidget(self.apply_category_btn)

        # Disregard selected — marks rows as Transfer/Excluded (excluded from
        # affordability). Commonly used for personal bank-to-bank transfers.
        self.disregard_btn = QPushButton("⊘  Disregard selected")
        self.disregard_btn.setToolTip(
            "Professionally disregard the selected transactions — sets them to "
            "'Transfer/Excluded' so they don't appear in affordability totals. "
            "The system learns from this so future imports auto-disregard the "
            "same source."
        )
        self.disregard_btn.setEnabled(False)
        self.disregard_btn.clicked.connect(self._disregard_selected)
        toolbar.addWidget(self.disregard_btn)

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

        self._delegate = CategoryGridDelegate(self.table)
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

        layout.addWidget(self.table, 1)

        # ── Reasoning detail panel ────────────────────────────────────────
        reasoning_row = QHBoxLayout()
        reasoning_row.setSpacing(8)
        self.reasoning_header = QLabel("Select a row to see the AI's reasoning")
        self.reasoning_header.setStyleSheet(
            "QLabel { color: #4A1766; font-weight: 600; padding-top: 6px; }"
        )
        reasoning_row.addWidget(self.reasoning_header, 1)
        self.training_note_btn = QPushButton("+  Add training note…")
        self.training_note_btn.setToolTip(
            "Save broker guidance for this categorisation. The note is stored now "
            "and applied later from the AI Training Zone."
        )
        self.training_note_btn.setEnabled(False)
        self.training_note_btn.clicked.connect(self._open_training_note_dialog)
        reasoning_row.addWidget(self.training_note_btn)
        layout.addLayout(reasoning_row)

        self.reasoning_text = QTextEdit()
        self.reasoning_text.setReadOnly(True)
        self.reasoning_text.setMinimumHeight(70)
        self.reasoning_text.setMaximumHeight(140)
        self.reasoning_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.reasoning_text.setStyleSheet(
            "QTextEdit { background-color: #F7F3FB; border: 1px solid #D8CCE5; "
            "border-radius: 6px; padding: 8px 10px; color: #2A0A3E; "
            "font-family: 'Consolas', 'Menlo', monospace; font-size: 12px; }"
        )
        self.reasoning_text.setPlaceholderText(
            "The AI's chain-of-thought reasoning will appear here when a row is selected."
        )
        layout.addWidget(self.reasoning_text)

        # ── Wire up signals ───────────────────────────────────────────────
        self.model.dataChanged.connect(lambda *_: self.refresh_summary())
        self.model.dataChanged.connect(lambda *_: self._maybe_prompt_siblings())
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
        indices = self.table.selectionModel().selectedRows()
        has = bool(indices)
        self.confirm_btn.setEnabled(has)
        self.apply_category_btn.setEnabled(has)
        self.disregard_btn.setEnabled(has)
        # Reasoning panel: only makes sense for a single-row selection.
        single = indices[0] if len(indices) == 1 else None
        if single is None:
            self.training_note_btn.setEnabled(False)
            self.reasoning_header.setText("Select a single row to see the AI's reasoning")
            self.reasoning_text.clear()
            return
        row = self.model._rows[single.row()]
        self.training_note_btn.setEnabled(True)
        merchant = row.get("merchant") or row.get("desc") or "(unknown)"
        self.reasoning_header.setText(
            f"AI reasoning — {merchant} → {row.get('category') or '(uncategorised)'}"
        )
        trace = row.get("reasoning") or ""
        if not trace:
            self.reasoning_text.setPlaceholderText(
                "No reasoning stored for this row — it was decided by a register/rule match "
                "rather than the LLM, or was saved before chain-of-thought was enabled."
            )
            self.reasoning_text.clear()
        else:
            self.reasoning_text.setPlainText(trace)

    def _open_training_note_dialog(self) -> None:
        indices = self.table.selectionModel().selectedRows()
        if len(indices) != 1:
            return
        row = self.model._rows[indices[0].row()]
        from .dialogs.training_note_dialog import TrainingNoteDialog
        dlg = TrainingNoteDialog(
            description=row["desc"],
            merchant=row["merchant"],
            current_category=row["category"],
            reasoning=row.get("reasoning", ""),
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        note_text, suggested = dlg.values()
        from ..auth.session import require_login
        from ..categorize.training import save_note
        user = require_login()
        try:
            save_note(
                transaction_id=row["id"],
                user_id=user.id,
                note=note_text,
                suggested_category=suggested,
            )
        except ValueError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Could not save note", str(e))
            return
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Training note saved",
            "Your guidance is stored. Open the AI Training Zone and click "
            "<b>Start Training</b> when you're ready to apply it.",
        )

    def _disregard_selected(self) -> None:
        indices = self.table.selectionModel().selectedRows()
        if not indices:
            return
        row_nums = sorted({i.row() for i in indices})
        updated = self.model.disregard_rows(row_nums)
        if updated:
            self.refresh_summary()

    def _apply_category_to_selected(self) -> None:
        indices = self.table.selectionModel().selectedRows()
        if not indices:
            return
        row_nums = sorted({i.row() for i in indices})

        from .widgets.category_grid_picker import CategoryGridPicker

        picker = CategoryGridPicker(current=None)
        # Position the picker underneath the button that was clicked, clamped
        # to the screen so it doesn't spill off the right/bottom edge.
        btn = self.apply_category_btn
        preferred = btn.mapToGlobal(btn.rect().bottomLeft())

        def _commit(category: str) -> None:
            updated = self.model.bulk_apply_category(row_nums, category)
            if updated:
                self.refresh_summary()

        picker.category_selected.connect(_commit)
        picker.show_at(preferred)
        # Keep a reference so the popup isn't garbage-collected before the user clicks.
        self._bulk_picker = picker

    def _maybe_prompt_siblings(self) -> None:
        pending = self.model.take_pending_confirm_siblings()
        if not pending:
            return
        source_desc, new_category, candidates = pending
        if not candidates:
            return
        dlg = SiblingConfirmDialog(
            candidates,
            new_category=new_category,
            source_description=source_desc,
            parent=self,
        )
        if dlg.exec() == SiblingConfirmDialog.DialogCode.Accepted:
            selected = dlg.accepted_candidates()
            updated = self.model.apply_confirmed_siblings(new_category, selected)
            if updated:
                self.refresh_summary()

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
