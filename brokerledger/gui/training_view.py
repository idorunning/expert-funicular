"""AI Training Zone — review pending broker notes, run training pass.

Stylised with a dark "high-tech training zone" look to differentiate it from
the rest of the app. All actual writes are delegated to
``brokerledger.categorize.training``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..auth.session import require_login
from ..categorize import training
from ..categorize.taxonomy import user_visible_categories


_TRAINING_QSS = """
QWidget#TrainingRoot {
    background-color: #0A0818;
    color: #EFE7F5;
}
QLabel {
    color: #EFE7F5;
    background: transparent;
}
QLabel#TrainingTitle {
    font-size: 22px;
    font-weight: 700;
    color: #D63A91;
}
QLabel#TrainingSubtitle {
    color: #9C8FB5;
    font-size: 12px;
}
QFrame#StatusCard, QFrame#LearningsCard, QFrame#NotePanel {
    background-color: #130E24;
    border: 1px solid #4A1766;
    border-radius: 10px;
}
QLabel#StatusNumber {
    color: #D63A91;
    font-size: 28px;
    font-weight: 700;
    font-family: 'Consolas','Menlo',monospace;
}
QLabel#StatusLabel {
    color: #9C8FB5;
    font-size: 11px;
}
QTableWidget {
    background-color: #130E24;
    color: #EFE7F5;
    gridline-color: #2A0A3E;
    border: 1px solid #4A1766;
    border-radius: 8px;
    selection-background-color: #4A1766;
    selection-color: #FFFFFF;
    alternate-background-color: #1A102E;
}
QHeaderView::section {
    background-color: #2A0A3E;
    color: #D63A91;
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid #4A1766;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QPlainTextEdit {
    background-color: #1A102E;
    color: #EFE7F5;
    border: 1px solid #4A1766;
    border-radius: 6px;
    font-family: 'Consolas','Menlo',monospace;
    font-size: 12px;
    padding: 8px 10px;
}
QComboBox {
    background-color: #1A102E;
    color: #EFE7F5;
    border: 1px solid #4A1766;
    border-radius: 6px;
    padding: 6px 8px;
}
QPushButton {
    background-color: #4A1766;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 10px 22px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QPushButton:hover {
    background-color: #D63A91;
}
QPushButton:disabled {
    background-color: #2A0A3E;
    color: #6B6679;
}
QPushButton#TrainButton {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #D63A91, stop:1 #4A1766
    );
    padding: 12px 28px;
    font-size: 14px;
}
QPushButton#GhostTraining {
    background-color: transparent;
    color: #D63A91;
    border: 1px solid #D63A91;
}
QPushButton#GhostTraining:hover {
    background-color: rgba(214, 58, 145, 0.15);
}
"""


class TrainingView(QWidget):
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TrainingRoot")
        self.setStyleSheet(_TRAINING_QSS)

        self._notes: list[dict] = []
        self._recent: list[dict] = []
        self._selected_note_id: int | None = None
        self._thread = None
        self._worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        # ── Header row ─────────────────────────────────────────────────────
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("◉ AI TRAINING ZONE")
        title.setObjectName("TrainingTitle")
        subtitle = QLabel("Review the AI's reasoning. Guide it. Train. Improve.")
        subtitle.setObjectName("TrainingSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)

        back = QPushButton("← Back")
        back.setObjectName("GhostTraining")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)

        self.train_btn = QPushButton("▶  Start Training")
        self.train_btn.setObjectName("TrainButton")
        self.train_btn.clicked.connect(self._start_training)
        header.addWidget(self.train_btn)
        root.addLayout(header)

        # ── Status cards ───────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.card_pending = self._status_card("UNCONSUMED NOTES", "0")
        self.card_ready = self._status_card("READY TO TRAIN", "0")
        self.card_needs_cat = self._status_card("AWAITING CATEGORY", "0")
        self.card_learned = self._status_card("RULES LEARNED (SESSION)", "0")
        status_row.addWidget(self.card_pending)
        status_row.addWidget(self.card_ready)
        status_row.addWidget(self.card_needs_cat)
        status_row.addWidget(self.card_learned)
        root.addLayout(status_row)

        # ── Pending notes table ───────────────────────────────────────────
        body = QHBoxLayout()
        body.setSpacing(14)

        # Left: the pending notes list
        self.pending_table = QTableWidget()
        self.pending_table.setColumnCount(5)
        self.pending_table.setHorizontalHeaderLabels(
            ["Merchant", "AI said", "Broker note", "Correct category", "Tx"]
        )
        self.pending_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pending_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pending_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pending_table.setAlternatingRowColors(True)
        self.pending_table.verticalHeader().setVisible(False)
        hdr = self.pending_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.pending_table.currentCellChanged.connect(self._on_note_selected)
        body.addWidget(self.pending_table, 3)

        # Right: selected-note detail panel
        note_panel = QFrame()
        note_panel.setObjectName("NotePanel")
        panel_layout = QVBoxLayout(note_panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(8)

        self.detail_title = QLabel("Select a pending note")
        self.detail_title.setStyleSheet("color: #D63A91; font-size: 14px; font-weight: bold;")
        self.detail_title.setStyleSheet("color: #D63A91;")
        panel_layout.addWidget(self.detail_title)

        panel_layout.addWidget(QLabel("AI reasoning trace"))
        self.reason_view = QPlainTextEdit()
        self.reason_view.setReadOnly(True)
        self.reason_view.setMinimumHeight(110)
        panel_layout.addWidget(self.reason_view)

        panel_layout.addWidget(QLabel("Broker guidance"))
        self.note_view = QPlainTextEdit()
        self.note_view.setReadOnly(True)
        self.note_view.setMinimumHeight(90)
        panel_layout.addWidget(self.note_view)

        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("Corrected category"))
        self.cat_combo = QComboBox()
        self.cat_combo.addItem("— leave for later —", userData=None)
        for cat in user_visible_categories():
            self.cat_combo.addItem(cat, userData=cat)
        self.cat_combo.setEnabled(False)
        self.cat_combo.currentIndexChanged.connect(self._on_category_changed)
        cat_row.addWidget(self.cat_combo, 1)
        panel_layout.addLayout(cat_row)

        btn_row = QHBoxLayout()
        self.dismiss_btn = QPushButton("✕  Dismiss note")
        self.dismiss_btn.setObjectName("GhostTraining")
        self.dismiss_btn.setEnabled(False)
        self.dismiss_btn.clicked.connect(self._dismiss_selected)
        btn_row.addWidget(self.dismiss_btn)
        btn_row.addStretch(1)
        panel_layout.addLayout(btn_row)

        body.addWidget(note_panel, 2)
        root.addLayout(body, 1)

        # ── Recent learnings ──────────────────────────────────────────────
        learnings_card = QFrame()
        learnings_card.setObjectName("LearningsCard")
        ll = QVBoxLayout(learnings_card)
        ll.setContentsMargins(14, 12, 14, 12)
        ll.setSpacing(6)
        ll.addWidget(QLabel(
            "<b style='color:#D63A91;'>✨ Recent learnings</b>"
            "  <span style='color:#9C8FB5;'>(post-training)</span>"
        ))
        self.learnings_table = QTableWidget()
        self.learnings_table.setColumnCount(3)
        self.learnings_table.setHorizontalHeaderLabels(["Merchant", "→ Category", "When"])
        self.learnings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.learnings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.learnings_table.setMaximumHeight(160)
        self.learnings_table.verticalHeader().setVisible(False)
        lh = self.learnings_table.horizontalHeader()
        lh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        lh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        ll.addWidget(self.learnings_table)
        root.addWidget(learnings_card)

        self.refresh()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _status_card(self, label: str, number: str) -> QFrame:
        card = QFrame()
        card.setObjectName("StatusCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(2)
        num = QLabel(number)
        num.setObjectName("StatusNumber")
        lbl = QLabel(label)
        lbl.setObjectName("StatusLabel")
        v.addWidget(num)
        v.addWidget(lbl)
        card._number_label = num  # type: ignore[attr-defined]
        return card

    def _set_card(self, card: QFrame, value: int) -> None:
        getattr(card, "_number_label").setText(str(value))

    # ------------------------------------------------------------------
    # data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        self._notes = training.list_unconsumed(limit=500)
        self._recent = training.list_recent_consumed(limit=25)
        self._render_pending()
        self._render_learnings()
        self._update_cards()

    def _update_cards(self) -> None:
        pending = len(self._notes)
        ready = sum(1 for n in self._notes if n.get("suggested_category"))
        needs_cat = pending - ready
        learned_today = sum(
            1 for r in self._recent
            if r["consumed_at"] is not None
        )
        self._set_card(self.card_pending, pending)
        self._set_card(self.card_ready, ready)
        self._set_card(self.card_needs_cat, needs_cat)
        self._set_card(self.card_learned, learned_today)
        self.train_btn.setEnabled(ready > 0 and self._thread is None)

    def _render_pending(self) -> None:
        self.pending_table.setRowCount(len(self._notes))
        for i, n in enumerate(self._notes):
            merch_item = QTableWidgetItem(n["merchant"] or n["description"] or "(unknown)")
            ai_item = QTableWidgetItem(
                f"{n['current_category'] or '(none)'}  "
                f"{_confidence_emoji(n.get('current_confidence'))}"
            )
            note_item = QTableWidgetItem(_short(n["note"], 80) if n["note"] else "—")
            cat_item = QTableWidgetItem(n["suggested_category"] or "⚠ pick one")
            if not n["suggested_category"]:
                cat_item.setForeground(QColor("#E3B85C"))
            tx_item = QTableWidgetItem(str(n["tx_id"]))
            tx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.pending_table.setItem(i, 0, merch_item)
            self.pending_table.setItem(i, 1, ai_item)
            self.pending_table.setItem(i, 2, note_item)
            self.pending_table.setItem(i, 3, cat_item)
            self.pending_table.setItem(i, 4, tx_item)

    def _render_learnings(self) -> None:
        self.learnings_table.setRowCount(len(self._recent))
        for i, r in enumerate(self._recent):
            when = r["consumed_at"].strftime("%H:%M") if r["consumed_at"] else ""
            self.learnings_table.setItem(i, 0, QTableWidgetItem(r["merchant"]))
            arrow = QTableWidgetItem(f"→ {r['category'] or '(cleared)'}")
            arrow.setForeground(QColor("#4AE6C5"))
            self.learnings_table.setItem(i, 1, arrow)
            self.learnings_table.setItem(i, 2, QTableWidgetItem(when))

    # ------------------------------------------------------------------
    # selection + edits
    # ------------------------------------------------------------------

    def _on_note_selected(self, row: int, _col: int, *_args) -> None:
        if row < 0 or row >= len(self._notes):
            self._selected_note_id = None
            self.detail_title.setText("Select a pending note")
            self.reason_view.clear()
            self.note_view.clear()
            self.cat_combo.setEnabled(False)
            self.cat_combo.setCurrentIndex(0)
            self.dismiss_btn.setEnabled(False)
            return
        n = self._notes[row]
        self._selected_note_id = n["id"]
        self.detail_title.setText(
            f"◆ {n['merchant'] or '(unknown merchant)'}   ·   "
            f"tx #{n['tx_id']}"
        )
        self.reason_view.setPlainText(n["reasoning"] or "(no reasoning stored)")
        self.note_view.setPlainText(n["note"] or "(no broker note — only a suggested category)")
        self.cat_combo.blockSignals(True)
        self.cat_combo.setCurrentIndex(0)
        if n["suggested_category"]:
            idx = self.cat_combo.findData(n["suggested_category"])
            if idx >= 0:
                self.cat_combo.setCurrentIndex(idx)
        self.cat_combo.blockSignals(False)
        self.cat_combo.setEnabled(True)
        self.dismiss_btn.setEnabled(True)

    def _on_category_changed(self, _idx: int) -> None:
        if self._selected_note_id is None:
            return
        cat = self.cat_combo.currentData()
        # Update the underlying note row + refresh the table row in place.
        self._persist_category_choice(self._selected_note_id, cat)
        for n in self._notes:
            if n["id"] == self._selected_note_id:
                n["suggested_category"] = cat
                break
        self._render_pending()
        self._update_cards()

    def _persist_category_choice(self, note_id: int, cat: str | None) -> None:
        from ..db.engine import session_scope
        from ..db.models import TrainingNote
        with session_scope() as s:
            note = s.get(TrainingNote, note_id)
            if note is None:
                return
            note.suggested_category = cat
            s.commit()

    def _dismiss_selected(self) -> None:
        if self._selected_note_id is None:
            return
        if QMessageBox.question(
            self, "Dismiss note",
            "Dismiss this note without applying it? The row stays in the database "
            "for audit but won't be picked up by future training passes.",
        ) != QMessageBox.StandardButton.Yes:
            return
        user = require_login()
        training.dismiss_note(self._selected_note_id, user.id)
        self.refresh()

    # ------------------------------------------------------------------
    # training pass
    # ------------------------------------------------------------------

    def _start_training(self) -> None:
        ready = sum(1 for n in self._notes if n.get("suggested_category"))
        if not ready:
            QMessageBox.information(
                self, "Nothing to train",
                "No notes have a corrected category selected. Pick a category on "
                "each note you want to apply, then click Start Training again.",
            )
            return
        self.train_btn.setEnabled(False)
        self.train_btn.setText("◉ Training…")
        from .workers.training_worker import run_training_in_thread
        self._thread, self._worker = run_training_in_thread()
        self._worker.done.connect(self._on_training_done)
        self._worker.error.connect(self._on_training_error)
        self._thread.start()

    def _on_training_done(self, report) -> None:
        self._thread = None
        self._worker = None
        self.train_btn.setText("▶  Start Training")
        self.refresh()
        if report.notes_processed == 0:
            QMessageBox.information(
                self, "Training complete",
                "No notes processed this pass. "
                + (f"{report.skipped_no_category} still awaiting a corrected category."
                   if report.skipped_no_category else "")
            )
            return
        siblings_line = (
            f"<br><b>{report.siblings_updated}</b> other transaction(s) "
            "re-categorised to match."
            if report.siblings_updated else ""
        )
        QMessageBox.information(
            self, "Training complete",
            f"<b>{report.notes_processed}</b> note(s) applied.<br>"
            f"<b>{report.rules_created}</b> new rule(s) created, "
            f"<b>{report.rules_updated}</b> existing rule(s) reinforced."
            f"{siblings_line}"
        )

    def _on_training_error(self, message: str) -> None:
        self._thread = None
        self._worker = None
        self.train_btn.setEnabled(True)
        self.train_btn.setText("▶  Start Training")
        QMessageBox.warning(self, "Training failed", message)


def _short(text: str, limit: int) -> str:
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _confidence_emoji(c: float | None) -> str:
    if c is None:
        return ""
    if c >= 0.85:
        return "●"
    if c >= 0.70:
        return "◐"
    return "○"
