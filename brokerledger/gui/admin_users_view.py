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
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
from ..auth.session import can_manage_user, get_current, require_admin, require_login
from ..db.engine import session_scope
from ..db.models import User
from ..users import service as users_service
from .widgets.avatar import AvatarLabel
from .widgets.password_field import PasswordField, PasswordPair


class _UserDialog(QDialog):
    """Shared create/edit dialog. Pass ``existing=`` to edit.

    ``allowed_roles`` restricts which roles the role dropdown offers — used so
    brokers can only create ``admin_staff`` users.  Defaults to all three.
    """

    _ERR_STYLE = "QLineEdit { border: 2px solid #A52D1E; background: #FFF5F3; }"
    _OK_STYLE  = ""

    def __init__(
        self,
        parent=None,
        *,
        existing: User | None = None,
        allowed_roles: tuple[str, ...] = ("broker", "admin_staff", "admin"),
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(440)
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
        self.role.addItems(list(allowed_roles))
        # Disable the role dropdown entirely when there's nothing to pick from
        # (e.g. brokers creating admin_staff — only one choice).
        if len(allowed_roles) == 1:
            self.role.setEnabled(False)
        # Password pair (new + confirm) with show/hide toggle.
        self.password_pair = PasswordPair(
            label_new="Password", label_confirm="Confirm password",
        )
        if existing is not None:
            self.full_name.setText(existing.full_name or "")
            self.email.setText(existing.email or "")
            self.username.setText(existing.username)
            if self.role.findText(existing.role) >= 0:
                self.role.setCurrentText(existing.role)
            self.password_pair.set_required_hint("Leave blank to keep current password")

        self._err_username = QLabel("")
        self._err_username.setStyleSheet("color:#A52D1E;font-size:11px;")
        self._err_username.setVisible(False)
        self._err_password = QLabel("")
        self._err_password.setStyleSheet("color:#A52D1E;font-size:11px;")
        self._err_password.setVisible(False)

        self.username.textChanged.connect(lambda: self._clear_err(self.username, self._err_username))
        self.password_pair.mismatch_changed.connect(self._on_pw_mismatch)

        form.addRow("Full name", self.full_name)
        form.addRow("Email (used to log in)", self.email)
        form.addRow("Username", self.username)
        form.addRow("", self._err_username)
        form.addRow("Role", self.role)
        form.addRow(self.password_pair)
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

    def _password_error(self, message: str) -> None:
        self._err_password.setText(message)
        self._err_password.setVisible(True)
        self.password_pair.setFocus()

    def _clear_err(self, field: QLineEdit, label: QLabel) -> None:
        field.setStyleSheet(self._OK_STYLE)
        label.setVisible(False)

    def _on_pw_mismatch(self, mismatch: bool) -> None:
        if not mismatch:
            self._err_password.setVisible(False)

    def accept(self) -> None:  # noqa: A003
        from ..config import get_settings
        username = self.username.text().strip()

        if not username:
            self._field_error(self.username, self._err_username, "Username is required.")
            return

        is_new = self._existing is None
        ok, err = self.password_pair.is_valid(
            min_length=get_settings().password_min_length,
            required=is_new,
        )
        if not ok:
            self._password_error(err)
            return

        super().accept()

    def values(self) -> dict[str, str]:
        return {
            "full_name": self.full_name.text().strip(),
            "email": self.email.text().strip(),
            "username": self.username.text().strip(),
            "role": self.role.currentText(),
            "password": self.password_pair.value(),
        }

    # Backwards-compat hooks for external error callers.
    @property
    def password(self) -> PasswordPair:  # noqa: D401
        """Password pair (used by outer code when showing server errors)."""
        return self.password_pair


class _BrokerAllocationsDialog(QDialog):
    """Pick which brokers an admin-staff user should be allowed to act for."""

    def __init__(self, admin_user: User, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Broker allocations for {admin_user.username}")
        self.setMinimumWidth(420)
        self._admin_user_id = admin_user.id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "Select the brokers this admin-staff user can support.  They "
            "will see clients belonging to every ticked broker and nothing else."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        brokers = users_service.list_brokers()
        already = set(users_service.get_admin_broker_ids(admin_user.id))

        self.listw = QListWidget()
        for b in brokers:
            label = f"{b.full_name or b.username}  ·  @{b.username}"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, b.id)
            it.setCheckState(
                Qt.CheckState.Checked if b.id in already else Qt.CheckState.Unchecked
            )
            self.listw.addItem(it)
        if self.listw.count() == 0:
            self.listw.addItem("(No active brokers — create one first.)")
            self.listw.setEnabled(False)
        layout.addWidget(self.listw, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_broker_ids(self) -> list[int]:
        out: list[int] = []
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                bid = it.data(Qt.ItemDataRole.UserRole)
                if bid is not None:
                    out.append(int(bid))
        return out


class _ResolveResetDialog(QDialog):
    def __init__(self, email: str, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(460)
        self.setWindowTitle("Resolve password reset")
        form = QFormLayout(self)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)
        form.addRow(QLabel(
            f"<p>Reset the password for <b>{email}</b>.<br>"
            "Tell the user the new password out-of-band (in person, secure chat, etc).</p>"
        ))
        self.password_pair = PasswordPair(
            label_new="New password", label_confirm="Confirm password",
        )
        form.addRow(self.password_pair)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._try_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _try_accept(self) -> None:
        from ..config import get_settings
        ok, _err = self.password_pair.is_valid(
            min_length=get_settings().password_min_length, required=True,
        )
        if ok:
            self.accept()

    def password(self) -> str:
        return self.password_pair.value()


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
        self.title_label = QLabel("<h1>Manage staff</h1>")
        header.addWidget(self.title_label)
        header.addStretch(1)
        outer.addLayout(header)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.new_btn = QPushButton("+ New user")
        self.new_btn.clicked.connect(self._new_user)
        actions.addWidget(self.new_btn)
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.clicked.connect(self._edit_selected)
        actions.addWidget(self.edit_btn)
        self.pwd_btn = QPushButton("Reset password…")
        self.pwd_btn.clicked.connect(self._reset_password)
        actions.addWidget(self.pwd_btn)
        self.photo_btn = QPushButton("Change photo…")
        self.photo_btn.clicked.connect(self._change_photo)
        actions.addWidget(self.photo_btn)
        self.remove_photo_btn = QPushButton("Remove photo")
        self.remove_photo_btn.setObjectName("GhostButton")
        self.remove_photo_btn.clicked.connect(self._remove_photo)
        actions.addWidget(self.remove_photo_btn)
        self.toggle_btn = QPushButton("Enable / Disable")
        self.toggle_btn.setObjectName("GhostButton")
        self.toggle_btn.clicked.connect(self._toggle_active)
        actions.addWidget(self.toggle_btn)
        self.allocs_btn = QPushButton("Broker allocations…")
        self.allocs_btn.setToolTip(
            "Admin-staff users must be allocated to one or more brokers. "
            "They see only those brokers' clients."
        )
        self.allocs_btn.clicked.connect(self._edit_allocations)
        actions.addWidget(self.allocs_btn)
        self.delete_btn = QPushButton("Delete…")
        self.delete_btn.setObjectName("GhostButton")
        self.delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_btn)
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
        self.reset_box = QWidget()
        reset_box = self.reset_box
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
        actor = require_login()
        if actor.role not in ("admin", "broker"):
            raise PermissionError("Only administrators and brokers can manage staff.")

        is_admin = actor.role == "admin"
        # Only admins can delete users, manage broker allocations, or see
        # pending password-reset requests from the login screen.
        self.delete_btn.setVisible(is_admin)
        self.allocs_btn.setVisible(is_admin)
        if hasattr(self, "reset_box"):
            self.reset_box.setVisible(is_admin)
        self.title_label.setText(
            "<h1>Manage staff</h1>" if is_admin else "<h1>My staff</h1>"
        )

        manageable = users_service.list_manageable_users(actor)
        self.table.setRowCount(len(manageable))
        for row, u in enumerate(manageable):
            avatar = AvatarLabel(size=40)
            avatar.set_photo(u.photo_path, u.username or "", u.full_name)
            self.table.setCellWidget(row, 0, avatar)
            self.table.setItem(row, 1, QTableWidgetItem(u.full_name or ""))
            self.table.setItem(row, 2, QTableWidgetItem(""))  # email hidden for brokers; see below
            self.table.setItem(row, 3, QTableWidgetItem(u.username))
            self.table.setItem(row, 4, QTableWidgetItem(u.role))
            self.table.setItem(row, 5, QTableWidgetItem("Yes" if u.is_active else "No"))
            id_item = QTableWidgetItem(str(u.id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, id_item)

        # Fill in email column for visible users — a second cheap query keeps
        # UserRow dataclass unchanged.
        if manageable:
            ids = [u.id for u in manageable]
            with session_scope() as s:
                rows = s.execute(
                    select(User.id, User.email).where(User.id.in_(ids))
                ).all()
            email_map = {r[0]: (r[1] or "") for r in rows}
            for row, u in enumerate(manageable):
                self.table.item(row, 2).setText(email_map.get(u.id, ""))

        # Pending reset requests — admins only.
        if is_admin:
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

    def _selected_user_role(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        try:
            return self.table.item(row, 4).text()
        except AttributeError:
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
        actor_cu = require_login()
        # Brokers can only create admin_staff; admins can create any role.
        allowed_roles = (
            ("admin_staff",)
            if actor_cu.role == "broker"
            else ("broker", "admin_staff", "admin")
        )
        dlg = _UserDialog(self, allowed_roles=allowed_roles)
        new_user_id: int | None = None
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            v = dlg.values()
            if actor_cu.role == "broker" and v["role"] != "admin_staff":
                QMessageBox.warning(
                    self, "Role not allowed",
                    "Brokers can only create admin_staff accounts.",
                )
                continue
            try:
                new_user_id = create_user(
                    v["username"], v["password"],
                    role=v["role"],
                    full_name=v["full_name"] or None,
                    email=v["email"] or None,
                    actor_id=actor_cu.id,
                )
                break
            except AuthError as e:
                err = str(e)
                low = err.lower()
                if "username" in low or "already" in low or "taken" in low:
                    dlg._field_error(dlg.username, dlg._err_username, err)
                elif "password" in low:
                    dlg._password_error(err)
                    dlg.password_pair.clear()
                else:
                    QMessageBox.warning(self, "Could not create user", err)
                    return
        # Auto-allocate newly created admin_staff users to the broker that
        # created them so they immediately see the broker's clients.
        if new_user_id is not None:
            actor = get_current()
            if actor is not None and actor.role == "broker" and v["role"] == "admin_staff":
                try:
                    users_service.allocate_admin_staff_to_broker(new_user_id, actor.id)
                except ValueError as e:
                    QMessageBox.warning(
                        self, "Allocation failed",
                        f"User created but allocation to you failed: {e}",
                    )
        self.refresh()

    def _edit_selected(self) -> None:
        uid = self._selected_user_id()
        if uid is None:
            QMessageBox.information(self, "No selection", "Select a user first.")
            return
        actor = require_login()
        target_role = self._selected_user_role()
        if target_role is None or not can_manage_user(actor, target_role, uid):
            QMessageBox.warning(
                self, "Not allowed",
                "You don't have permission to edit this user.",
            )
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
        # Brokers can only edit admin_staff; role dropdown is locked.
        allowed_roles = (
            ("admin_staff",)
            if actor.role == "broker"
            else ("broker", "admin_staff", "admin")
        )
        dlg = _UserDialog(self, existing=snapshot, allowed_roles=allowed_roles)
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
        try:
            actor = require_admin()
        except PermissionError:
            QMessageBox.warning(
                self, "Administrators only",
                "Only administrators can delete user accounts.",
            )
            return
        if QMessageBox.question(
            self, "Delete user",
            "Permanently delete this user account? Their audit-log entries remain "
            "but any clients they created keep pointing at their now-removed id."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_user(uid, actor_id=actor.id)
        except AuthError as e:
            QMessageBox.warning(self, "Could not delete user", str(e))
            return
        self.refresh()

    def _reset_password(self) -> None:
        from ..config import get_settings
        uid = self._selected_user_id()
        if uid is None:
            QMessageBox.information(self, "No selection", "Select a user first.")
            return
        # Permission check — use the manageability helper so brokers can reset
        # passwords for their own admin_staff but not for other users.
        actor = require_login()
        target_role = self._selected_user_role()
        if target_role is None or not can_manage_user(actor, target_role, uid):
            QMessageBox.warning(
                self, "Not allowed",
                "You don't have permission to reset this user's password.",
            )
            return

        dlg = QDialog(self)
        dlg.setMinimumWidth(440)
        dlg.setWindowTitle("Reset password")
        form = QFormLayout(dlg)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)
        pair = PasswordPair(
            label_new="New password", label_confirm="Confirm password",
        )
        form.addRow(pair)
        err_lbl = QLabel("")
        err_lbl.setStyleSheet("color:#A52D1E;font-size:11px;")
        err_lbl.setVisible(False)
        form.addRow("", err_lbl)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )

        def _try_accept() -> None:
            ok, msg = pair.is_valid(
                min_length=get_settings().password_min_length, required=True,
            )
            if ok:
                dlg.accept()
            else:
                err_lbl.setText(msg)
                err_lbl.setVisible(True)

        btns.accepted.connect(_try_accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            change_password(uid, pair.value(), actor_id=actor.id)
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

    def _edit_allocations(self) -> None:
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
        if snapshot.role != "admin_staff":
            QMessageBox.information(
                self, "Not an admin-staff user",
                "Broker allocations only apply to users whose role is "
                "'admin_staff'.  Change the role in Edit… first.",
            )
            return
        dlg = _BrokerAllocationsDialog(snapshot, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            users_service.set_admin_broker_ids(uid, dlg.selected_broker_ids())
        except (ValueError, PermissionError) as e:
            QMessageBox.warning(self, "Could not save allocations", str(e))
            return
        QMessageBox.information(self, "Done", "Broker allocations updated.")

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
            resolve_request(rid, dlg.password(), actor_id=actor.id)
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
