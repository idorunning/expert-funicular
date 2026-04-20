"""Login view."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..auth.password_reset import (
    submit_reset_code,
    submit_reset_request,
    verify_and_reset,
)
from ..auth.service import AuthError, InvalidCredentials, login
from ..mail import smtp as smtp_mod
from .theme import load_logo_pixmap


class _EmailResetDialog(QDialog):
    """Two-step emailed reset: send code, then enter code + new password."""

    def __init__(self, parent=None, initial_email: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Reset password by email")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        layout.addWidget(QLabel(
            "Enter the email address on your account. If it matches, we'll "
            "send a 6-digit code. The code expires in 15 minutes."
        ))

        form = QFormLayout()
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        if initial_email:
            self.email.setText(initial_email)
        form.addRow("Email", self.email)
        layout.addLayout(form)

        send_row = QHBoxLayout()
        self.send_btn = QPushButton("Send code")
        self.send_btn.clicked.connect(self._send_code)
        send_row.addStretch(1)
        send_row.addWidget(self.send_btn)
        layout.addLayout(send_row)

        self.second_form = QFormLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("6-digit code")
        self.code.setMaxLength(6)
        self.second_form.addRow("Code", self.code)
        self.new_password = QLineEdit()
        self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password.setPlaceholderText("At least 10 characters with a digit")
        self.second_form.addRow("New password", self.new_password)
        layout.addLayout(self.second_form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Set new password")
        btns.accepted.connect(self._finish)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        self.code.setEnabled(False)
        self.new_password.setEnabled(False)

    def _send_code(self) -> None:
        email = self.email.text().strip()
        if not email:
            QMessageBox.warning(self, "Email required", "Enter your email.")
            return
        try:
            _req_id, code = submit_reset_code(email)
        except AuthError as e:
            QMessageBox.warning(self, "Couldn't send code", str(e))
            return
        if code is not None:
            try:
                smtp_mod.send_reset_code(email, code)
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(
                    self, "Could not send email",
                    f"The SMTP server refused the message:\n{e}\n\n"
                    "Ask an administrator to check Settings → Email (SMTP)."
                )
                return
        # Enumeration-safe: same confirmation regardless of whether the email
        # matched a real user.
        QMessageBox.information(
            self, "Code sent",
            "If that email is registered, a code is on its way. "
            "Enter it below along with your new password."
        )
        self.code.setEnabled(True)
        self.new_password.setEnabled(True)
        self._ok_btn.setEnabled(True)
        self.code.setFocus()

    def _finish(self) -> None:
        email = self.email.text().strip()
        code = self.code.text().strip()
        new_pw = self.new_password.text()
        try:
            verify_and_reset(email, code, new_pw)
        except AuthError as e:
            QMessageBox.warning(self, "Couldn't reset password", str(e))
            return
        QMessageBox.information(
            self, "Password updated",
            "Your password has been reset. Log in with the new password."
        )
        self.accept()


class _ForgotPasswordDialog(QDialog):
    def __init__(self, parent=None, initial_email: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Request password reset")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        intro = QLabel(
            "<p>This application runs fully offline, so we can't email you a reset link. "
            "Submit a request and an administrator will set a new password for you "
            "and tell you what it is.</p>"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        if initial_email:
            self.email.setText(initial_email)
        form.addRow("Email", self.email)

        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Optional note to the administrator")
        self.note.setFixedHeight(80)
        form.addRow("Note", self.note)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Submit request")
        btns.accepted.connect(self._submit)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _submit(self) -> None:
        email = self.email.text().strip()
        if not email:
            QMessageBox.warning(self, "Email required", "Enter the email address for your account.")
            return
        try:
            submit_reset_request(email, note=self.note.toPlainText().strip() or None)
        except AuthError as e:
            QMessageBox.warning(self, "Couldn't submit request", str(e))
            return
        QMessageBox.information(
            self,
            "Request submitted",
            "Your request has been recorded. An administrator will reset your "
            "password and contact you with the new one.",
        )
        self.accept()


class LoginView(QWidget):
    logged_in = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mortgage Broker Affordability Assistant")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)

        box = QWidget()
        box.setMaximumWidth(420)
        form = QFormLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        logo_pm = load_logo_pixmap(height=72)
        if not logo_pm.isNull():
            logo_label = QLabel()
            logo_label.setPixmap(logo_pm)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            form.addRow(logo_label)

        title = QLabel(
            "<p style='color:#666;margin:8px 0 0 0;text-align:center'>"
            "Local mortgage affordability analyser</p>"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow(title)

        self.identifier = QLineEdit()
        self.identifier.setPlaceholderText("you@example.com or username")
        form.addRow("Email or username", self.identifier)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Password")
        form.addRow("Password", self.password)

        self.login_btn = QPushButton("Log in")
        self.login_btn.clicked.connect(self._try_login)
        form.addRow(self.login_btn)

        self.forgot_btn = QPushButton("Forgot password?")
        self.forgot_btn.setFlat(True)
        self.forgot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forgot_btn.setStyleSheet(
            "QPushButton { color:#5a3dc6; text-align:center; border:none; padding:4px; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self.forgot_btn.clicked.connect(self._open_forgot)
        form.addRow(self.forgot_btn)

        self.password.returnPressed.connect(self._try_login)
        self.identifier.returnPressed.connect(self.password.setFocus)

        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(box)
        centered.addStretch(1)
        outer.addLayout(centered)
        outer.addStretch(2)

    # Back-compat for any callers that still reach for `.username`.
    @property
    def username(self) -> QLineEdit:
        return self.identifier

    def focus_default(self) -> None:
        self.identifier.setFocus()

    def _try_login(self) -> None:
        try:
            login(self.identifier.text().strip(), self.password.text())
        except InvalidCredentials:
            QMessageBox.warning(self, "Login failed", "Invalid email/username or password.")
            return
        except AuthError as e:
            QMessageBox.warning(self, "Login failed", str(e))
            return
        self.password.clear()
        self.logged_in.emit()

    def _open_forgot(self) -> None:
        initial = self.identifier.text().strip()
        if smtp_mod.is_configured():
            dlg = _EmailResetDialog(self, initial_email=initial)
        else:
            dlg = _ForgotPasswordDialog(self, initial_email=initial)
        dlg.exec()
