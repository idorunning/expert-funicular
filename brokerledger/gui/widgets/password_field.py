"""Reusable password-entry widgets with show/hide toggle and optional confirm."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class PasswordField(QWidget):
    """A single password line edit with an inline eye-icon toggle.

    By default the field masks input; clicking the eye reveals the text until
    clicked again.  Use this everywhere a password is entered — login,
    password-reset, change-my-password, etc.
    """

    text_changed = Signal(str)
    return_pressed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        placeholder: str | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.EchoMode.Password)
        if placeholder:
            self._edit.setPlaceholderText(placeholder)
        self._edit.textChanged.connect(self.text_changed.emit)
        self._edit.returnPressed.connect(self.return_pressed.emit)
        row.addWidget(self._edit, 1)

        self._toggle = QToolButton()
        self._toggle.setText("👁")
        self._toggle.setCheckable(True)
        self._toggle.setToolTip("Show / hide password")
        self._toggle.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.toggled.connect(self._on_toggled)
        row.addWidget(self._toggle)

    def _on_toggled(self, visible: bool) -> None:
        self._edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )

    def text(self) -> str:
        return self._edit.text()

    def setText(self, value: str) -> None:  # noqa: N802 — Qt naming
        self._edit.setText(value)

    def setPlaceholderText(self, value: str) -> None:  # noqa: N802
        self._edit.setPlaceholderText(value)

    def clear(self) -> None:
        self._edit.clear()

    def line_edit(self) -> QLineEdit:
        """Expose the underlying edit so callers can style errors etc."""
        return self._edit

    @property
    def returnPressed(self):  # noqa: N802 — Qt naming, signal proxy
        return self._edit.returnPressed

    def setFocus(self, reason: Qt.FocusReason = Qt.FocusReason.OtherFocusReason) -> None:  # noqa: N802
        self._edit.setFocus(reason)


class PasswordPair(QWidget):
    """Two stacked :class:`PasswordField`s with a live match/error helper.

    The helper text below the fields stays hidden until either field has
    content and the two disagree.  ``is_valid(min_length)`` returns a
    ``(bool, error_message)`` pair so callers can block form submission and
    surface the reason.
    """

    mismatch_changed = Signal(bool)

    _OK_STYLE = ""
    _ERR_STYLE = "QLineEdit { border: 2px solid #A52D1E; background: #FFF5F3; }"
    _HINT_STYLE = "color:#A52D1E;font-size:11px;"

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        label_new: str = "New password",
        label_confirm: str = "Confirm password",
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._label_new = QLabel(label_new)
        layout.addWidget(self._label_new)
        self.new_field = PasswordField()
        layout.addWidget(self.new_field)

        self._label_confirm = QLabel(label_confirm)
        layout.addWidget(self._label_confirm)
        self.confirm_field = PasswordField()
        layout.addWidget(self.confirm_field)

        self._hint = QLabel("")
        self._hint.setStyleSheet(self._HINT_STYLE)
        self._hint.setVisible(False)
        layout.addWidget(self._hint)

        self.new_field.text_changed.connect(self._revalidate)
        self.confirm_field.text_changed.connect(self._revalidate)

    # ------------------------------------------------------------------

    def _revalidate(self, *_args) -> None:
        a = self.new_field.text()
        b = self.confirm_field.text()
        if a and b and a != b:
            self._hint.setText("Passwords don't match — please re-enter.")
            self._hint.setVisible(True)
            self.new_field.line_edit().setStyleSheet(self._ERR_STYLE)
            self.confirm_field.line_edit().setStyleSheet(self._ERR_STYLE)
            self.mismatch_changed.emit(True)
        else:
            self._hint.setVisible(False)
            self.new_field.line_edit().setStyleSheet(self._OK_STYLE)
            self.confirm_field.line_edit().setStyleSheet(self._OK_STYLE)
            self.mismatch_changed.emit(False)

    def value(self) -> str:
        """Return the agreed password (empty string if blank or mismatched)."""
        a = self.new_field.text()
        b = self.confirm_field.text()
        return a if a and a == b else ""

    def is_valid(self, min_length: int, *, required: bool = True) -> tuple[bool, str]:
        """Return ``(ok, error_message)``.

        * ``required=False`` allows both fields blank to pass (used by
          "leave blank to keep current password" admin flows).
        """
        a = self.new_field.text()
        b = self.confirm_field.text()
        if not a and not b:
            if required:
                return False, "Password is required."
            return True, ""
        if a != b:
            return False, "Passwords don't match — please re-enter."
        if len(a) < min_length:
            return False, f"Password must be at least {min_length} characters."
        return True, ""

    def clear(self) -> None:
        self.new_field.clear()
        self.confirm_field.clear()
        self._hint.setVisible(False)

    def set_required_hint(self, placeholder: str | None = None) -> None:
        if placeholder:
            self.new_field.setPlaceholderText(placeholder)
            self.confirm_field.setPlaceholderText(placeholder)

    def setFocus(self, reason: Qt.FocusReason = Qt.FocusReason.OtherFocusReason) -> None:  # noqa: N802
        self.new_field.setFocus(reason)
