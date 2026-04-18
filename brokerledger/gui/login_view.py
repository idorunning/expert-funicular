"""Login view."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..auth.service import AuthError, InvalidCredentials, login


class LoginView(QWidget):
    logged_in = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BrokerLedger — Login")

        outer = QVBoxLayout(self)
        outer.addStretch(1)

        box = QWidget()
        box.setMaximumWidth(380)
        form = QFormLayout(box)
        title = QLabel("<h1>BrokerLedger</h1><p style='color:#666'>Local mortgage affordability analyser</p>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow(title)

        self.username = QLineEdit()
        self.username.setPlaceholderText("Username")
        form.addRow("Username", self.username)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Password")
        form.addRow("Password", self.password)

        self.login_btn = QPushButton("Log in")
        self.login_btn.clicked.connect(self._try_login)
        form.addRow(self.login_btn)

        self.password.returnPressed.connect(self._try_login)
        self.username.returnPressed.connect(self.password.setFocus)

        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(box)
        centered.addStretch(1)
        outer.addLayout(centered)
        outer.addStretch(2)

    def focus_default(self) -> None:
        self.username.setFocus()

    def _try_login(self) -> None:
        try:
            login(self.username.text().strip(), self.password.text())
        except InvalidCredentials:
            QMessageBox.warning(self, "Login failed", "Invalid username or password.")
            return
        except AuthError as e:
            QMessageBox.warning(self, "Login failed", str(e))
            return
        self.password.clear()
        self.logged_in.emit()
