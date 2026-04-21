"""ComboBox delegate for amending a transaction's category in-grid.

Improvements over the original:
- Calls showPopup() immediately so the dropdown opens on first click.
- Auto-commits when the user selects a value, so they never need to
  press Enter or click elsewhere to apply the change.
"""
from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QComboBox,
    QStyledItemDelegate,
    QWidget,
)

from ...categorize.taxonomy import includes_for, user_visible_categories


class CategoryDelegate(QStyledItemDelegate):
    def createEditor(self, parent: QWidget, option, index: QModelIndex) -> QWidget:  # noqa: N802
        combo = QComboBox(parent)
        combo.setEditable(False)
        combo.setMaxVisibleItems(22)
        for cat in user_visible_categories():
            combo.addItem(cat)
            hint = includes_for(cat)
            if hint:
                combo.setItemData(
                    combo.count() - 1,
                    f"{cat}\n\n{hint}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        # Open the dropdown the moment the editor appears.
        combo.showPopup()
        # Commit immediately when the user picks a value — no extra click needed.
        combo.activated.connect(lambda _idx, c=combo: self._commit_and_close(c))
        return combo

    def setEditorData(self, editor: QComboBox, index: QModelIndex) -> None:  # noqa: N802
        current = index.data(Qt.ItemDataRole.EditRole) or ""
        pos = editor.findText(current)
        if pos >= 0:
            editor.setCurrentIndex(pos)

    def setModelData(self, editor: QComboBox, model, index: QModelIndex) -> None:  # noqa: N802
        value = editor.currentText()
        if value:
            model.setData(index, value, Qt.ItemDataRole.EditRole)

    def _commit_and_close(self, combo: QComboBox) -> None:
        self.commitData.emit(combo)
        self.closeEditor.emit(combo, QAbstractItemDelegate.EndEditHint.NoHint)
