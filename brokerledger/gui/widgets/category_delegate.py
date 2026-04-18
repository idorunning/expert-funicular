"""ComboBox delegate for amending a transaction's category in-grid."""
from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QWidget

from ...categorize.taxonomy import user_visible_categories


class CategoryDelegate(QStyledItemDelegate):
    def createEditor(self, parent: QWidget, option, index: QModelIndex) -> QWidget:  # noqa: N802
        combo = QComboBox(parent)
        combo.setEditable(False)
        combo.addItem("")  # empty = unassigned / keep current
        for cat in user_visible_categories():
            combo.addItem(cat)
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
