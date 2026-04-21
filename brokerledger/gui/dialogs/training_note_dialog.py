"""Training-note dialog — broker writes guidance against an AI decision.

The note is saved to ``training_notes`` immediately when the dialog is
accepted. It doesn't change the transaction at the time of save — the
broker opens the AI Training Zone and clicks "Start Training" to apply
accumulated notes in one pass.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from ...categorize.taxonomy import user_visible_categories


class TrainingNoteDialog(QDialog):
    def __init__(
        self,
        *,
        description: str,
        merchant: str,
        current_category: str,
        reasoning: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add training note")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(10)

        header = QLabel(
            f"<b>{merchant or '(unknown merchant)'}</b>"
            f"<br><span style='color:#6B6679;'>{description}</span>"
        )
        header.setWordWrap(True)
        root.addWidget(header)

        ai_box = QLabel(
            f"<span style='color:#4A1766;'><b>AI said:</b> "
            f"{current_category or '(uncategorised)'}</span>"
        )
        ai_box.setWordWrap(True)
        root.addWidget(ai_box)

        if reasoning:
            reason_label = QLabel("<b>AI reasoning</b>")
            root.addWidget(reason_label)
            reason_view = QPlainTextEdit(reasoning)
            reason_view.setReadOnly(True)
            reason_view.setMaximumHeight(120)
            reason_view.setStyleSheet(
                "QPlainTextEdit { background-color: #F7F3FB; border: 1px solid #D8CCE5;"
                " border-radius: 6px; padding: 8px 10px; color: #2A0A3E;"
                " font-family: 'Consolas','Menlo',monospace; font-size: 12px; }"
            )
            root.addWidget(reason_view)

        form = QFormLayout()
        form.setSpacing(6)
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText(
            "e.g. 'Pocket money is a child allowance — always map to Child Care'"
        )
        self.note_edit.setMinimumHeight(90)
        form.addRow("Your guidance", self.note_edit)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(False)
        self.category_combo.addItem("— leave for later —", userData=None)
        for cat in user_visible_categories():
            self.category_combo.addItem(cat, userData=cat)
        # Preselect the current category if possible so the broker can tweak easily.
        if current_category:
            idx = self.category_combo.findData(current_category)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
        form.addRow("Correct category", self.category_combo)
        root.addLayout(form)

        hint = QLabel(
            "<span style='color:#6B6679;font-size:12px;'>"
            "The note is stored right away. It will only change the model's "
            "behaviour once you run <b>Start Training</b> in the AI Training Zone."
            "</span>"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> tuple[str, str | None]:
        note = self.note_edit.toPlainText().strip()
        suggested = self.category_combo.currentData()
        return note, suggested

    def accept(self) -> None:  # noqa: A003
        note, suggested = self.values()
        if not note and suggested is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Nothing to save",
                "Write a note or pick a corrected category before saving.",
            )
            return
        super().accept()
