"""Clients list + "new client" dialog + management menu."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..auth.session import get_current
from ..clients.service import (
    ClientError,
    ClientRecord,
    archive_client,
    create_client,
    delete_client,
    list_clients,
    reassign_client,
    rename_client,
    restore_client,
)
from ..users import service as users_service
from .greetings import greeting_for, random_quote


class NewClientDialog(QDialog):
    _ERR_STYLE = "QLineEdit { border: 2px solid #A52D1E; background: #FFF5F3; }"
    _OK_STYLE  = ""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(380)
        self.setWindowTitle("New client")
        form = QFormLayout(self)
        form.setSpacing(6)

        self.name = QLineEdit()
        form.addRow("Client name", self.name)
        self._err_name = QLabel("")
        self._err_name.setStyleSheet("color:#A52D1E;font-size:11px;")
        self._err_name.setVisible(False)
        form.addRow("", self._err_name)

        self.reference = QLineEdit()
        self.reference.setPlaceholderText("optional, e.g. case number")
        form.addRow("Reference", self.reference)
        self._err_ref = QLabel("")
        self._err_ref.setStyleSheet("color:#A52D1E;font-size:11px;")
        self._err_ref.setVisible(False)
        form.addRow("", self._err_ref)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self.name.textChanged.connect(lambda: self._clear_err(self.name, self._err_name))
        self.reference.textChanged.connect(lambda: self._clear_err(self.reference, self._err_ref))

    def _field_error(self, field: QLineEdit, label: QLabel, message: str) -> None:
        field.setStyleSheet(self._ERR_STYLE)
        label.setText(message)
        label.setVisible(True)
        field.setFocus()

    def _clear_err(self, field: QLineEdit, label: QLabel) -> None:
        field.setStyleSheet(self._OK_STYLE)
        label.setVisible(False)

    def accept(self) -> None:  # noqa: A003
        if not self.name.text().strip():
            self._field_error(self.name, self._err_name, "Client name is required.")
            return
        super().accept()

    def field_error_reference(self, message: str) -> None:
        """Called by the parent view when the service rejects the reference."""
        self._field_error(self.reference, self._err_ref, message)

    def values(self) -> tuple[str, str | None]:
        return self.name.text().strip(), (self.reference.text().strip() or None)


class ReassignDialog(QDialog):
    def __init__(self, current_owner_id: int | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reassign client")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pick the user who should own this client:"))
        self.combo = QComboBox()
        self._users = users_service.list_active_users()
        for u in self._users:
            label = f"{u.full_name or u.username}  ·  {u.role}"
            if u.id == current_owner_id:
                label += "  (current)"
            self.combo.addItem(label, userData=u.id)
        layout.addWidget(self.combo)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_user_id(self) -> int | None:
        data = self.combo.currentData()
        return int(data) if data is not None else None


class ClientsView(QWidget):
    open_client = Signal(int, str)   # (client_id, name)
    logout_requested = Signal()
    admin_requested = Signal()
    settings_requested = Signal()
    audit_log_requested = Signal()
    training_requested = Signal()

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Top row — greeting + motivational line + collapsed menu button.
        header = QHBoxLayout()
        header.setSpacing(10)

        greeting_col = QVBoxLayout()
        greeting_col.setSpacing(2)
        self.greeting_label = QLabel("")
        self.greeting_label.setStyleSheet(
            "QLabel { font-size: 22px; font-weight: 600; color: #1F1030; }"
        )
        self.quote_label = QLabel("")
        self.quote_label.setStyleSheet("QLabel { color: #6B6679; font-size: 13px; }")
        self.quote_label.setWordWrap(True)
        greeting_col.addWidget(self.greeting_label)
        greeting_col.addWidget(self.quote_label)
        header.addLayout(greeting_col, 1)

        header.addStretch(0)

        self.menu_btn = QToolButton()
        self.menu_btn.setText("☰  Menu")
        self.menu_btn.setToolTip("Settings, Admin and Audit log")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setStyleSheet(
            "QToolButton { background-color: #4A1766; color: #FFFFFF; "
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: 600; }"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
            "QToolButton:hover { background-color: #D63A91; }"
        )
        self._menu = QMenu(self)
        self._menu.setToolTipsVisible(True)
        self.settings_action = self._menu.addAction("Settings…")
        self.settings_action.triggered.connect(self.settings_requested.emit)
        self.training_action = self._menu.addAction("AI Training Zone…")
        self.training_action.triggered.connect(self.training_requested.emit)
        self.admin_action = self._menu.addAction("Admin (manage users)…")
        self.admin_action.triggered.connect(self.admin_requested.emit)
        self.audit_action = self._menu.addAction("Audit log…")
        self.audit_action.triggered.connect(self.audit_log_requested.emit)
        self.menu_btn.setMenu(self._menu)
        header.addWidget(self.menu_btn)

        layout.addLayout(header)

        toolbar = QHBoxLayout()
        new_btn = QPushButton("+  New client")
        new_btn.clicked.connect(self._new_client)
        toolbar.addWidget(new_btn)
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("Search"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name or reference…")
        self.search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search, 1)
        self.show_archived = QCheckBox("Show archived")
        self.show_archived.stateChanged.connect(self.refresh)
        toolbar.addWidget(self.show_archived)
        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Name", "Reference", "Created", "Status", "ID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.cellDoubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        self.status_label = QLabel("Double-click a client to open, or right-click for more actions.")
        self.status_label.setStyleSheet("QLabel { color: #6B6679; }")
        layout.addWidget(self.status_label)

        bottom = QHBoxLayout()
        self.user_label = QLabel()
        bottom.addWidget(self.user_label)
        bottom.addStretch(1)
        logout = QPushButton("Log out")
        logout.setObjectName("GhostButton")
        logout.clicked.connect(self.logout_requested.emit)
        bottom.addWidget(logout)
        layout.addLayout(bottom)

        self._records: list[ClientRecord] = []

    def refresh(self) -> None:
        cu = get_current()
        if cu is not None:
            label = cu.full_name or cu.username
            self.user_label.setText(
                f"<span style='color:#555'>Signed in as <b>{label}</b> ({cu.role})</span>"
            )
            first = (label.split()[0] if label else label) or "there"
            self.greeting_label.setText(greeting_for(first))
            self.quote_label.setText(random_quote())
            is_admin = cu.role == "admin"
            self.admin_action.setVisible(is_admin)
            self.audit_action.setVisible(is_admin)
        else:
            self.greeting_label.setText("Welcome")
            self.quote_label.setText("")
            self.user_label.setText("")
        try:
            self._records = list_clients(include_archived=self.show_archived.isChecked())
        except Exception as e:  # noqa: BLE001
            self._records = []
            self.status_label.setText(f"Could not load clients: {e}")
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self.search.text().strip().lower()
        rows = self._records
        if needle:
            rows = [
                r for r in rows
                if needle in r.display_name.lower()
                or (r.reference or "").lower().find(needle) >= 0
            ]
        self.table.setRowCount(len(rows))
        for idx, c in enumerate(rows):
            self.table.setItem(idx, 0, QTableWidgetItem(c.display_name))
            self.table.setItem(idx, 1, QTableWidgetItem(c.reference or ""))
            self.table.setItem(idx, 2, QTableWidgetItem(c.created_at.strftime("%Y-%m-%d")))
            status = "archived" if c.archived_at else "active"
            self.table.setItem(idx, 3, QTableWidgetItem(status))
            id_item = QTableWidgetItem(str(c.id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(idx, 4, id_item)
        hint = "Double-click to open, or right-click for more actions."
        self.status_label.setText(
            f"{len(rows)} of {len(self._records)} client(s) shown"
            + (f" — search: '{needle}'" if needle else "")
            + f"  ·  {hint}"
        )

    def _selected_record(self) -> ClientRecord | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        try:
            cid = int(self.table.item(row, 4).text())
        except (AttributeError, ValueError):
            return None
        for r in self._records:
            if r.id == cid:
                return r
        return None

    def _context_menu(self, pos) -> None:
        row = self.table.indexAt(pos).row()
        if row < 0:
            return
        self.table.selectRow(row)
        rec = self._selected_record()
        if rec is None:
            return
        cu = get_current()
        is_admin = cu is not None and cu.role == "admin"

        menu = QMenu(self)
        open_act = QAction("Open", self)
        open_act.triggered.connect(self._open_selected)
        menu.addAction(open_act)

        rename_act = QAction("Rename…", self)
        rename_act.triggered.connect(self._rename_selected)
        menu.addAction(rename_act)

        if rec.archived_at is None:
            arch_act = QAction("Archive", self)
            arch_act.triggered.connect(self._archive_selected)
            menu.addAction(arch_act)
        else:
            restore_act = QAction("Restore", self)
            restore_act.triggered.connect(self._restore_selected)
            menu.addAction(restore_act)

        menu.addSeparator()
        reassign_act = QAction("Reassign to…", self)
        reassign_act.triggered.connect(self._reassign_selected)
        reassign_act.setEnabled(is_admin)
        menu.addAction(reassign_act)

        delete_act = QAction("Delete…", self)
        delete_act.triggered.connect(self._delete_selected)
        delete_act.setEnabled(is_admin)
        menu.addAction(delete_act)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _rename_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename client", "New name:", text=rec.display_name
        )
        if not ok or not new_name.strip() or new_name.strip() == rec.display_name:
            return
        try:
            rename_client(rec.id, new_name.strip())
        except ClientError as e:
            QMessageBox.warning(self, "Rename failed", str(e))
            return
        self.refresh()

    def _archive_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        if QMessageBox.question(
            self, "Archive client",
            f"Archive '{rec.display_name}'? It will be hidden until you toggle 'Show archived'.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            archive_client(rec.id)
        except ClientError as e:
            QMessageBox.warning(self, "Archive failed", str(e))
            return
        self.refresh()

    def _restore_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        try:
            restore_client(rec.id)
        except ClientError as e:
            QMessageBox.warning(self, "Restore failed", str(e))
            return
        self.refresh()

    def _reassign_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        dlg = ReassignDialog(current_owner_id=None, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target = dlg.selected_user_id()
        if target is None:
            return
        try:
            reassign_client(rec.id, target)
        except (ClientError, PermissionError) as e:
            QMessageBox.warning(self, "Reassign failed", str(e))
            return
        self.refresh()

    def _delete_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        typed, ok = QInputDialog.getText(
            self, "Delete client",
            f"Type the client name '{rec.display_name}' to confirm deletion. "
            "All statements and transactions will be removed.",
        )
        if not ok or typed.strip() != rec.display_name:
            if ok:
                QMessageBox.information(self, "Not deleted", "Name did not match — nothing deleted.")
            return
        try:
            delete_client(rec.id)
        except (ClientError, PermissionError) as e:
            QMessageBox.warning(self, "Delete failed", str(e))
            return
        self.refresh()

    def _new_client(self) -> None:
        dlg = NewClientDialog(self)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            name, ref = dlg.values()
            try:
                rec = create_client(name, ref)
                break
            except ClientError as e:
                # Keep the dialog open; highlight only the failing field.
                err = str(e)
                if "reference" in err.lower() or "already in use" in err.lower():
                    dlg.field_error_reference(err + " — please choose a different one.")
                else:
                    QMessageBox.warning(self, "Could not create client", err)
                    return
        self.refresh()
        self.open_client.emit(rec.id, rec.display_name)

    def _open_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        self.open_client.emit(rec.id, rec.display_name)
