"""Icon-grid category picker — a compact popup that replaces the plain
ComboBox dropdown in the Review view.

Rendered as a small framed popup with a grid of tool buttons. Each button
shows the category's Mortgage Oasis SVG icon above its name. Clicking a
button emits :class:`CategoryGridPicker.category_selected` and closes the
popup; that commits the choice via :class:`CategoryGridDelegate`.

The popup also carries a "⊘ Disregard" footer button that short-circuits
categorisation for transactions which don't fit any category (small one-off
payments, personal transfers, etc.) — it emits the special
``"Transfer/Excluded"`` category that the review model treats as disregarded.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...categorize.icons import icon_path_for
from ...categorize.taxonomy import (
    COMMITTED_CATEGORIES,
    DISCRETIONARY_CATEGORIES,
    includes_for,
    user_visible_categories,
)


# The model treats this special category as "disregarded" — it excludes
# affected transactions from affordability totals.  Keep the string in sync
# with ``TransactionsModel.disregard_rows`` in ``review_view.py``.
DISREGARD_CATEGORY = "Transfer/Excluded"


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
QPushButton#DisregardButton {{
    background: #FFFFFF;
    color: {_BRAND_PURPLE};
    border: 1px solid {_BRAND_PURPLE};
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#DisregardButton:hover {{
    background: {_BRAND_PURPLE_SOFT};
    border: 1px solid {_BRAND_MAGENTA};
    color: {_BRAND_MAGENTA};
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

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

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
            grid.addWidget(btn, i // cols, i % cols)

        root.addLayout(grid)

        # Footer: Disregard button. Emits a special category that the review
        # model interprets as "excluded from affordability totals" so odd
        # one-off transactions don't force an arbitrary category choice.
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 4, 0, 0)
        footer.addStretch(1)
        self.disregard_btn = QPushButton("⊘  Disregard")
        self.disregard_btn.setObjectName("DisregardButton")
        self.disregard_btn.setToolTip(
            "Mark this transaction as 'Transfer/Excluded' — it will be ignored "
            "in affordability totals.  Use for odd one-off payments that don't "
            "fit any category or are too small to matter."
        )
        self.disregard_btn.clicked.connect(lambda: self._select(DISREGARD_CATEGORY))
        footer.addWidget(self.disregard_btn)
        root.addLayout(footer)

        self.adjustSize()

    def _select(self, category: str) -> None:
        self.category_selected.emit(category)
        self.close()

    # ------------------------------------------------------------------
    # Positioning
    # ------------------------------------------------------------------

    def show_at(self, preferred_top_left: "QPoint") -> None:
        """Show the popup near ``preferred_top_left``, clamped to its screen.

        If the preferred position would spill the popup off the right or
        bottom edge, slide it back into view so it remains fully visible.
        Uses the screen that contains the preferred point so multi-monitor
        setups behave sanely.
        """
        self.adjustSize()
        size = self.size()
        screen = QGuiApplication.screenAt(preferred_top_left) or QGuiApplication.primaryScreen()
        avail: QRect = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        x = preferred_top_left.x()
        y = preferred_top_left.y()
        if x + size.width() > avail.right():
            x = avail.right() - size.width()
        if y + size.height() > avail.bottom():
            y = avail.bottom() - size.height()
        if x < avail.left():
            x = avail.left()
        if y < avail.top():
            y = avail.top()

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()


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
        picker.show_at(global_pos)
        return placeholder

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # noqa: N802
        return

    def setModelData(self, editor: QWidget, model, index: QModelIndex) -> None:  # noqa: N802
        # Writes happen in the picker's ``category_selected`` slot.
        return
