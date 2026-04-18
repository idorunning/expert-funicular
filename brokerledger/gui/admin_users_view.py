"""Admin: manage users (admin role only)."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from ..auth.service import (
    AuthError,
    change_password,
    create_user,
    set_user_active,
)
from ..auth.session import require_admin
from ..db.engine import session_scope
from ..db.models import User


class NewUserDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New user")
        form = QFormLayout(self)
        self.username = QLineEdit()
        form.addRow("Username", self.username)
        self.full_name = QLineEdit()
        form.addRow("Full name", self.full_name)
        self.role = QComboBox()
        self.role.addItems(["broker", "admin"])
        form.addRow("Role", self.role)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password", self.password)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)


class AdminUsersView(QWidget):
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        back = QPushButton("← Back")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addStretch(1)
        header.addWidget(QLabel("<h1>Users</h1>"))
        header.addStretch(1)
        layout.addLayout(header)

        actions = QHBoxLayout()
        new_btn = QPushButton("+ New user")
        new_btn.clicked.connect(self._new_user)
        actions.addWidget(new_btn)
        pwd_btn = QPushButton("Reset password")
        pwd_btn.clicked.connect(self._reset_password)
        actions.addWidget(pwd_btn)
        toggle_btn = QPushButton("Enable / Disable")
        toggle_btn.clicked.connect(self._toggle_active)
        actions.addWidget(toggle_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Username", "Full name", "Role", "Active", "ID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

    def refresh(self) -> None:
        require_admin()
        with session_scope() as s:
            users = s.execute(select(User).order_by(User.username.asc())).scalars().all()
        self.table.setRowCount(len(users))
        for row, u in enumerate(users):
            self.table.setItem(row, 0, QTableWidgetItem(u.username))
            self.table.setItem(row, 1, QTableWidgetItem(u.full_name or ""))
            self.table.setItem(row, 2, QTableWidgetItem(u.role))
            self.table.setItem(row, 3, QTableWidgetItem("Yes" if u.is_active else "No"))
            self.table.setItem(row, 4, QTableWidgetItem(str(u.id)))

    def _selected_user_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return int(self.table.item(row, 4).text())

    def _new_user(self) -> None:
        dlg = NewUserDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            actor = require_admin()
            create_user(
                dlg.username.text().strip(),
                dlg.password.text(),
                role=dlg.role.currentText(),
                full_name=dlg.full_name.text().strip() or None,
                actor_id=actor.id,
            )
        except AuthError as e:
            QMessageBox.warning(self, "Could not create user", str(e))
            return
        self.refresh()

    def _reset_password(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            QMessageBox.information(self, "No selection", "Select a user first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Reset password")
        form = QFormLayout(dlg)
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password", pw)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
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
