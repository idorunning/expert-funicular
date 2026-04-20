"""Admin: manage users — create, edit, delete, reset password, change photo,
approve pending password-reset requests."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from ..auth.password_reset import (
    dismiss_request,
    list_pending_requests,
    resolve_request,
)
from ..auth.service import (
    AuthError,
    change_password,
    create_user,
    delete_user,
    set_user_active,
    update_user,
)
from ..auth.session import require_admin
from ..db.engine import session_scope
from ..db.models import User
from ..users import service as users_service
from .widgets.avatar import AvatarLabel


class _UserDialog(QDialog):
    """Shared create/edit dialog. Pass ``existing=`` to edit."""

    _ERR_STYLE = "QLineEdit { border: 2px solid #A52D1E; background: #FFF5F3; }"
    _OK_STYLE  = ""

    def __init__(self, parent=None, *, existing: User | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(420)
        self.setWindowTitle("Edit user" if existing else "New user")
        self._existing = existing

        form = QFormLayout(self)
        form.setSpacing(6)
        form.setContentsMargins(16, 16, 16, 16)

        self.full_name = QLineEdit()
        self.email = QLineEdit()
        self.email.setPlaceholderText("name@example.com")
        self.username = QLineEdit()
        self.username.setPlaceholderText("Short handle, e.g. 'jsmith'")
        self.role = QComboBox()
        self.role.addItems(["broker", "admin"])
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        if existing is not None:
            self.full_name.setText(existing.full_name or "")
            self.email.setText(existing.email or "")
            self.username.setText(existing.username)
            self.role.setCurrentText(existing.role)
            self.password.setPlaceholderText("Leave blank to keep current password")

        # Inline error labels — hidden until validation fires.
        self._err_username = QLabel("")
        self._err_username.setStyleSheet("color:#A52D1E;font-size:11px;")
        self._err_username.setVisible(False)
        self._err_password = QLabel("")
        self._err_password.setStyleSheet("color:#A52D1E;font-size:11px;")
        self._err_password.setVisible(False)

        # Clear error styling when the user edits the field.
        self.username.textChanged.connect(lambda: self._clear_err(self.username, self._err_username))
        self.password.textChanged.connect(lambda: self._clear_err(self.password, self._err_password))

        form.addRow("Full name", self.full_name)
        form.addRow("Email (used to log in)", self.email)
        form.addRow("Username", self.username)
        form.addRow("", self._err_username)
        form.addRow("Role", self.role)
        form.addRow("Password", self.password)
        form.addRow("", self._err_password)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _field_error(self, field: QLineEdit, label: QLabel, message: str) -> None:
        field.setStyleSheet(self._ERR_STYLE)
        label.setText(message)
        label.setVisible(True)
        field.setFocus()

    def _clear_err(self, field: QLineEdit, label: QLabel) -> None:
        field.setStyleSheet(self._OK_STYLE)
        label.setVisible(False)

    def accept(self) -> None:  # noqa: A003
        from ..config import get_settings
        username = self.username.text().strip()
        pw = self.password.text()

        if not username:
            self._field_error(self.username, self._err_username, "Username is required.")
            return

        is_new = self._existing is None
        if is_new and not pw:
            self._field_error(self.password, self._err_password, "Password is required for new users.")
            return

        if pw:  # only validate when a password was actually entered
            min_len = get_settings().password_min_length
            if len(pw) < min_len:
                self._field_error(
                    self.password, self._err_password,
                    f"Password must be at least {min_len} characters — please try again.",
                )
                self.password.clear()
                return

        super().accept()

    def values(self) -> dict[str, str]:
        return {
            "full_name": self.full_name.text().strip(),
            "email": self.email.text().strip(),
            "username": self.username.text().strip(),
            "role": self.role.currentText(),
            "password": self.password.text(),
        }


class _ResolveResetDialog(QDialog):
    def __init__(self, email: str, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(420)
        self.setWindowTitle("Resolve password reset")
        form = QFormLayout(self)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)
        form.addRow(QLabel(
            f"<p>Reset the password for <b>{email}</b>.<br>"
            "Tell the user the new password out-of-band (in person, secure chat, etc).</p>"
        ))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password", self.password)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)


class AdminUsersView(QWidget):
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        back = QPushButton("← Back")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addSpacing(12)
        header.addWidget(QLabel("<h1>Users</h1>"))
        header.addStretch(1)
        outer.addLayout(header)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        new_btn = QPushButton("+ New user")
        new_btn.clicked.connect(self._new_user)
        actions.addWidget(new_btn)
        edit_btn = QPushButton("Edit…")
        edit_btn.clicked.connect(self._edit_selected)
        actions.addWidget(edit_btn)
        pwd_btn = QPushButton("Reset password…")
        pwd_btn.clicked.connect(self._reset_password)
        actions.addWidget(pwd_btn)
        photo_btn = QPushButton("Change photo…")
        photo_btn.clicked.connect(self._change_photo)
        actions.addWidget(photo_btn)
        remove_photo_btn = QPushButton("Remove photo")
        remove_photo_btn.setObjectName("GhostButton")
        remove_photo_btn.clicked.connect(self._remove_photo)
        actions.addWidget(remove_photo_btn)
        toggle_btn = QPushButton("Enable / Disable")
        toggle_btn.setObjectName("GhostButton")
        toggle_btn.clicked.connect(self._toggle_active)
        actions.addWidget(toggle_btn)
        delete_btn = QPushButton("Delete…")
        delete_btn.setObjectName("GhostButton")
        delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(delete_btn)
        actions.addStretch(1)
        outer.addLayout(actions)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # ---- Users table --------------------------------------------------
        users_box = QWidget()
        users_layout = QVBoxLayout(users_box)
        users_layout.setContentsMargins(0, 0, 0, 0)
        users_layout.addWidget(QLabel("<b>Users</b>"))
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["", "Full name", "Email", "Username", "Role", "Active", "ID"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 56)
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        users_layout.addWidget(self.table)
        splitter.addWidget(users_box)

        # ---- Pending reset requests --------------------------------------
        reset_box = QWidget()
        reset_layout = QVBoxLayout(reset_box)
        reset_layout.setContentsMargins(0, 8, 0, 0)
        reset_header = QHBoxLayout()
        self.reset_title = QLabel("<b>Pending password-reset requests</b>")
        reset_header.addWidget(self.reset_title)
        reset_header.addStretch(1)
        resolve_btn = QPushButton("Resolve selected…")
        resolve_btn.clicked.connect(self._resolve_selected_request)
        reset_header.addWidget(resolve_btn)
        dismiss_btn = QPushButton("Dismiss selected")
        dismiss_btn.setObjectName("GhostButton")
        dismiss_btn.clicked.connect(self._dismiss_selected_request)
        reset_header.addWidget(dismiss_btn)
        reset_layout.addLayout(reset_header)

        self.reset_table = QTableWidget()
        self.reset_table.setColumnCount(5)
        self.reset_table.setHorizontalHeaderLabels(
            ["Submitted", "Email", "Matched user", "Note", "ID"]
        )
        self.reset_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.reset_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.reset_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.reset_table.verticalHeader().setVisible(False)
        self.reset_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reset_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.reset_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.reset_table.setAlternatingRowColors(True)
        reset_layout.addWidget(self.reset_table)
        splitter.addWidget(reset_box)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

    # ---- Data loading ----------------------------------------------------

    def refresh(self) -> None:
        require_admin()
        with session_scope() as s:
            users = s.execute(select(User).order_by(User.username.asc())).scalars().all()
            rows = [
                (
                    u.id, u.username, u.email, u.full_name, u.role, u.is_active, u.photo_path
                )
                for u in users
            ]
        self.table.setRowCount(len(rows))
        for row, (uid, username, email, full_name, role, active, photo) in enumerate(rows):
            avatar = AvatarLabel(size=40)
            avatar.set_photo(photo, username or "", full_name)
            self.table.setCellWidget(row, 0, avatar)
            self.table.setItem(row, 1, QTableWidgetItem(full_name or ""))
            self.table.setItem(row, 2, QTableWidgetItem(email or ""))
            self.table.setItem(row, 3, QTableWidgetItem(username))
            self.table.setItem(row, 4, QTableWidgetItem(role))
            self.table.setItem(row, 5, QTableWidgetItem("Yes" if active else "No"))
            id_item = QTableWidgetItem(str(uid))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, id_item)

        # Reset requests.
        pending = list_pending_requests()
        self.reset_title.setText(
            f"<b>Pending password-reset requests</b>"
            + (f"  <span style='color:#A52D1E'>({len(pending)})</span>" if pending else "")
        )
        self.reset_table.setRowCount(len(pending))
        for row, r in enumerate(pending):
            self.reset_table.setItem(row, 0, QTableWidgetItem(r.created_at.strftime("%Y-%m-%d %H:%M")))
            self.reset_table.setItem(row, 1, QTableWidgetItem(r.email_submitted))
            self.reset_table.setItem(row, 2, QTableWidgetItem(r.username or "(no match)"))
            self.reset_table.setItem(row, 3, QTableWidgetItem(r.note or ""))
            id_item = QTableWidgetItem(str(r.id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.reset_table.setItem(row, 4, id_item)

    def _selected_user_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        try:
            return int(self.table.item(row, 6).text())
        except (AttributeError, ValueError):
            return None

    def _selected_request_id(self) -> int | None:
        row = self.reset_table.currentRow()
        if row < 0:
            return None
        try:
            return int(self.reset_table.item(row, 4).text())
        except (AttributeError, ValueError):
            return None

    # ---- Actions: users --------------------------------------------------

    def _new_user(self) -> None:
        dlg = _UserDialog(self)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            v = dlg.values()
            try:
                actor = require_admin()
                create_user(
                    v["username"], v["password"],
                    role=v["role"],
                    full_name=v["full_name"] or None,
                    email=v["email"] or None,
                    actor_id=actor.id,
                )
                break
            except AuthError as e:
                err = str(e)
                low = err.lower()
                if "username" in low or "already" in low or "taken" in low:
                    dlg._field_error(dlg.username, dlg._err_username, err)
                elif "password" in low:
                    dlg._field_error(dlg.password, dlg._err_password, err)
                    dlg.password.clear()
                else:
                    QMessageBox.warning(self, "Could not create user", err)
                    return
        self.refresh()

    def _edit_selected(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            QMessageBox.information(self, "No selection", "Select a user first.")
            return
        with session_scope() as s:
            u = s.get(User, uid)
            if u is None:
                return
            snapshot = User(
                id=u.id, username=u.username, email=u.email,
                role=u.role, full_name=u.full_name,
                password_hash=u.password_hash,
            )
        dlg = _UserDialog(self, existing=snapshot)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        try:
            actor = require_admin()
            update_user(
                uid,
                username=v["username"] or None,
                full_name=v["full_name"],
                email=v["email"],
                role=v["role"],
                actor_id=actor.id,
            )
            if v["password"]:
                change_password(uid, v["password"], actor_id=actor.id)
        except AuthError as e:
            QMessageBox.warning(self, "Update failed", str(e))
            return
        self.refresh()

    def _delete_selected(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            return
        if QMessageBox.question(
            self, "Delete user",
            "Permanently delete this user account? Their audit-log entries remain "
            "but any clients they created keep pointing at their now-removed id."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            actor = require_admin()
            delete_user(uid, actor_id=actor.id)
        except AuthError as e:
            QMessageBox.warning(self, "Could not delete user", str(e))
            return
        self.refresh()

    def _reset_password(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            QMessageBox.information(self, "No selection", "Select a user first.")
            return
        dlg = QDialog(self)
        dlg.setMinimumWidth(400)
        dlg.setWindowTitle("Reset password")
        form = QFormLayout(dlg)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password", pw)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            actor = require_admin()
            change_password(uid, pw.text(), actor_id=actor.id)
        except AuthError as e:
            QMessageBox.warning(self, "Could not reset password", str(e))
            return
        QMessageBox.information(self, "Password reset", "Password updated.")

    def _change_photo(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            QMessageBox.information(self, "No selection", "Select a user first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select profile photo", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        try:
            users_service.set_user_photo(uid, Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save photo", str(e))
            return
        self.refresh()

    def _remove_photo(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            return
        users_service.clear_user_photo(uid)
        self.refresh()

    def _toggle_active(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            return
        with session_scope() as s:
            u = s.get(User, uid)
            if u is None:
                return
            new_state = not bool(u.is_active)
        try:
            actor = require_admin()
            set_user_active(uid, new_state, actor_id=actor.id)
        except AuthError as e:
            QMessageBox.warning(self, "Error", str(e))
            return
        self.refresh()

    # ---- Actions: password-reset requests -------------------------------

    def _resolve_selected_request(self) -> None:
        rid = self._selected_request_id()
        if rid is None:
            QMessageBox.information(self, "No selection", "Select a request first.")
            return
        row = self.reset_table.currentRow()
        email = self.reset_table.item(row, 1).text() if row >= 0 else ""
        matched = self.reset_table.item(row, 2).text() if row >= 0 else ""
        if matched == "(no match)":
            if QMessageBox.question(
                self, "No matching user",
                f"The email '{email}' isn't registered. Dismiss the request instead?",
            ) == QMessageBox.StandardButton.Yes:
                self._dismiss_selected_request()
            return
        dlg = _ResolveResetDialog(email, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            actor = require_admin()
            resolve_request(rid, dlg.password.text(), actor_id=actor.id)
        except AuthError as e:
            QMessageBox.warning(self, "Could not resolve", str(e))
            return
        QMessageBox.information(self, "Done", f"Password updated for {email}.")
        self.refresh()

    def _dismiss_selected_request(self) -> None:
        rid = self._selected_request_id()
        if rid is None:
            return
        try:
            actor = require_admin()
            dismiss_request(rid, actor_id=actor.id)
        except AuthError as e:
            QMessageBox.warning(self, "Error", str(e))
            return
        self.refresh()
