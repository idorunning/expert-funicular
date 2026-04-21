"""Icon-grid category picker — a compact popup that replaces the plain
ComboBox dropdown in the Review view.

Rendered as a small framed popup with a grid of tool buttons. Each button
shows the category's Mortgage Oasis SVG icon above its name. Clicking a
button emits :class:`CategoryGridPicker.category_selected` and closes the
popup; that commits the choice via :class:`CategoryGridDelegate`.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QFrame,
    QGridLayout,
    QStyledItemDelegate,
    QToolButton,
    QWidget,
)

from ...categorize.icons import icon_path_for
from ...categorize.taxonomy import (
    COMMITTED_CATEGORIES,
    DISCRETIONARY_CATEGORIES,
    includes_for,
    user_visible_categories,
)


_BRAND_PURPLE = "#4A1766"
_BRAND_MAGENTA = "#D63A91"
_BRAND_PURPLE_SOFT = "#EFE7F5"


_GRID_STYLESHEET = f"""
QFrame#CategoryGridPickerFrame {{
    background: #FFFFFF;
    border: 1px solid {_BRAND_PURPLE};
    border-radius: 8px;
}}
QToolButton {{
    background: #FFFFFF;
    color: {_BRAND_PURPLE};
    border: 1px solid {_BRAND_PURPLE_SOFT};
    border-radius: 6px;
    padding: 6px 4px;
    font-size: 11px;
}}
QToolButton:hover {{
    background: {_BRAND_PURPLE_SOFT};
    border: 1px solid {_BRAND_MAGENTA};
}}
QToolButton:checked {{
    background: {_BRAND_MAGENTA};
    color: #FFFFFF;
    border: 1px solid {_BRAND_MAGENTA};
}}
"""


class CategoryGridPicker(QFrame):
    """Popup grid of category icon-buttons."""

    category_selected = Signal(str)

    def __init__(
        self,
        current: str | None = None,
        categories: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CategoryGridPickerFrame")
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(_GRID_STYLESHEET)

        cats = categories if categories is not None else user_visible_categories()

        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(6)

        cols = 4
        committed = set(COMMITTED_CATEGORIES)
        discretionary = set(DISCRETIONARY_CATEGORIES)

        # Stable order: committed first, then discretionary, then anything else.
        ordered: list[str] = []
        for c in COMMITTED_CATEGORIES:
            if c in cats:
                ordered.append(c)
        for c in DISCRETIONARY_CATEGORIES:
            if c in cats:
                ordered.append(c)
        for c in cats:
            if c not in committed and c not in discretionary:
                ordered.append(c)

        for i, cat in enumerate(ordered):
            btn = QToolButton(self)
            btn.setText(cat)
            btn.setCheckable(True)
            hint = includes_for(cat)
            btn.setToolTip(f"{cat}\n\n{hint}" if hint else cat)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(28, 28))
            btn.setMinimumSize(QSize(108, 78))
            btn.setMaximumSize(QSize(140, 90))

            icon_path = icon_path_for(cat)
            if icon_path.exists():
                btn.setIcon(QIcon(str(icon_path)))

            if cat == current:
                btn.setChecked(True)

            btn.clicked.connect(lambda _checked=False, c=cat: self._select(c))
            layout.addWidget(btn, i // cols, i % cols)

        self.adjustSize()

    def _select(self, category: str) -> None:
        self.category_selected.emit(category)
        self.close()


class CategoryGridDelegate(QStyledItemDelegate):
    """Delegate that pops up a :class:`CategoryGridPicker` as its editor.

    Because the picker is a top-level ``Qt.WindowType.Popup`` (so it can
    float outside the table's viewport), Qt's default delegate plumbing
    doesn't reliably route ``commitData`` back to ``setModelData``. We
    therefore write the chosen category to the model directly via a
    :class:`QPersistentModelIndex` captured at edit-open time.
    """

    def createEditor(self, parent: QWidget, option, index: QModelIndex) -> QWidget:  # noqa: N802
        current = index.data(Qt.ItemDataRole.EditRole) or ""
        # Keep a zero-size placeholder so Qt's editor machinery is satisfied.
        placeholder = QWidget(parent)
        placeholder.setFixedSize(0, 0)

        picker = CategoryGridPicker(current=str(current))
        global_pos = parent.mapToGlobal(option.rect.bottomLeft())
        picker.move(global_pos)

        model = index.model()
        persistent = QPersistentModelIndex(index)

        def _commit(category: str) -> None:
            if persistent.isValid():
                target = model.index(persistent.row(), persistent.column())
                model.setData(target, category, Qt.ItemDataRole.EditRole)
            self.closeEditor.emit(
                placeholder, QAbstractItemDelegate.EndEditHint.NoHint
            )

        picker.category_selected.connect(_commit)
        # Keep the picker alive until the placeholder is destroyed.
        placeholder._picker = picker  # type: ignore[attr-defined]
        picker.show()
        picker.raise_()
        picker.activateWindow()
        return placeholder

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # noqa: N802
        return

    def setModelData(self, editor: QWidget, model, index: QModelIndex) -> None:  # noqa: N802
        # Writes happen in the picker's ``category_selected`` slot.
        return
