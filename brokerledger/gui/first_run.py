"""First-run dialog: create admin user, probe Ollama, accept legal terms."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..auth.service import AuthError, create_user, login
from ..categorize.llm_client import LLMError, OllamaClient
from ..config import get_settings
from ..db import app_settings
from .dialogs.legal_dialog import LegalDialog
from .theme import load_logo_pixmap


class FirstRunDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Mortgage Broker Affordability Assistant")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        logo_pm = load_logo_pixmap(height=64)
        if not logo_pm.isNull():
            logo_label = QLabel()
            logo_label.setPixmap(logo_pm)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        intro = QLabel(
            "<h2>Welcome</h2>"
            "<p>Create an administrator account. You'll use this to log in and to add more users later.</p>"
            "<p>This application runs entirely on this machine. Nothing is sent over the network except to your "
            "local Ollama instance at 127.0.0.1.</p>"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.full_name = QLineEdit()
        self.full_name.setPlaceholderText("Your name")
        form.addRow("Full name", self.full_name)
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        form.addRow("Email", self.email)
        self.username = QLineEdit()
        self.username.setText("admin")
        form.addRow("Username", self.username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password", self.password)
        self.password2 = QLineEdit()
        self.password2.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Confirm password", self.password2)
        layout.addLayout(form)

        self.ollama_status = QLabel("Checking Ollama…")
        self.ollama_status.setWordWrap(True)
        self.ollama_status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.ollama_status)

        legal_row = QHBoxLayout()
        legal_row.setContentsMargins(0, 8, 0, 0)
        self.accept_legal = QCheckBox(
            "I have read and accept the Privacy Policy and End-User Licence Agreement."
        )
        legal_row.addWidget(self.accept_legal, 1)
        view_legal_btn = QPushButton("View…")
        view_legal_btn.setFlat(True)
        view_legal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_legal_btn.clicked.connect(self._open_legal)
        legal_row.addWidget(view_legal_btn)
        layout.addLayout(legal_row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._probe_ollama()

    def _open_legal(self) -> None:
        LegalDialog(self).exec()

    def _probe_ollama(self) -> None:
        s = get_settings()
        try:
            client = OllamaClient()
            models = client.list_models()
        except LLMError as e:
            self.ollama_status.setText(
                f"<p style='color:#a33'><b>Ollama not reachable at {s.ollama_url}.</b></p>"
                f"<p style='color:#555'>You can still use the app in FAKE-LLM mode for testing, "
                f"but categorisation quality will be poor. Install Ollama and run "
                f"<code>ollama pull gemma3:4b</code> then restart.</p>"
                f"<p style='color:#999'><small>{e}</small></p>"
            )
            return
        if not models:
            self.ollama_status.setText(
                "<p style='color:#a67'><b>Ollama is running but no models are installed.</b></p>"
                "<p>Run <code>ollama pull gemma3:4b</code> in a terminal, then restart.</p>"
            )
            return
        picked = client.model
        model_list = ", ".join(models)
        self.ollama_status.setText(
            f"<p style='color:#063'><b>Ollama OK.</b> Using model: <code>{picked}</code></p>"
            f"<p style='color:#555'><small>Installed: {model_list}</small></p>"
        )

    def _on_ok(self) -> None:
        if not self.accept_legal.isChecked():
            QMessageBox.warning(
                self, "Legal acceptance required",
                "You must accept the Privacy Policy and End-User Licence "
                "Agreement to continue."
            )
            return
        if self.password.text() != self.password2.text():
            QMessageBox.warning(self, "Passwords don't match", "Please re-enter the password.")
            return
        try:
            create_user(
                self.username.text().strip(),
                self.password.text(),
                role="admin",
                full_name=self.full_name.text().strip() or None,
                email=self.email.text().strip() or None,
            )
            login(self.username.text().strip(), self.password.text())
        except AuthError as e:
            QMessageBox.warning(self, "Could not create admin", str(e))
            return
        app_settings.put(
            "legal_accepted_at",
            datetime.now(timezone.utc).isoformat(),
        )
        self.accept()
