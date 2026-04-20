"""Dialog: confirm which similar merchants to update after a correction.

Shown when the sibling-learning pass finds transactions whose description
fuzz-scores into the "confirm" band (70–89%) against the one the user just
corrected. The user ticks which siblings to re-categorise.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...categorize.siblings import SiblingCandidate


class SiblingConfirmDialog(QDialog):
    def __init__(
        self,
        candidates: list[SiblingCandidate],
        *,
        new_category: str,
        source_description: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Apply to similar transactions?")
        self.setMinimumSize(620, 360)
        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        intro = QLabel(
            f"<b>{len(candidates)}</b> similar transaction(s) look like they may also "
            f"belong to <b>{new_category}</b>. Based on: "
            f"<i>{source_description}</i>. Tick the ones you want to update."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #1F1030; font-size: 13px;")
        layout.addWidget(intro)

        select_row = QHBoxLayout()
        self.select_all = QCheckBox("Select all")
        self.select_all.stateChanged.connect(self._toggle_all)
        select_row.addWidget(self.select_all)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        self.table = QTableWidget(len(candidates), 4)
        self.table.setHorizontalHeaderLabels(["Apply", "Description", "Current category", "Match %"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self._checkboxes: list[QCheckBox] = []
        for row, cand in enumerate(candidates):
            cb = QCheckBox()
            cb.setChecked(cand.score >= 80)
            self._checkboxes.append(cb)
            self.table.setCellWidget(row, 0, cb)
            self.table.setItem(row, 1, QTableWidgetItem(cand.description))
            self.table.setItem(row, 2, QTableWidgetItem(cand.current_category or ""))
            item = QTableWidgetItem(str(cand.score))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, item)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(3, 80)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_all(self, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        for cb in self._checkboxes:
            cb.setChecked(checked)

    def accepted_candidates(self) -> list[SiblingCandidate]:
        return [
            cand
            for cand, cb in zip(self._candidates, self._checkboxes)
            if cb.isChecked()
        ]
