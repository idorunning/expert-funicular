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


class NewClientDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New client")
        form = QFormLayout(self)
        self.name = QLineEdit()
        form.addRow("Client name", self.name)
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("optional, e.g. case number")
        form.addRow("Reference", self.reference)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

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

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.title = QLabel("Clients")
        self.title.setStyleSheet(
            "QLabel { font-size: 22px; font-weight: 600; color: #1F1030; }"
        )
        header.addWidget(self.title)
        header.addStretch(1)
        self.user_label = QLabel()
        header.addWidget(self.user_label)
        self.settings_btn = QPushButton("Settings…")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_btn)
        self.admin_btn = QPushButton("Admin…")
        self.admin_btn.clicked.connect(self.admin_requested.emit)
        header.addWidget(self.admin_btn)
        logout = QPushButton("Log out")
        logout.clicked.connect(self.logout_requested.emit)
        header.addWidget(logout)
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

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("QLabel { color: #6B6679; }")
        layout.addWidget(self.status_label)

        open_btn = QPushButton("Open selected client")
        open_btn.clicked.connect(self._open_selected)
        layout.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self._records: list[ClientRecord] = []

    def refresh(self) -> None:
        cu = get_current()
        if cu is not None:
            label = cu.full_name or cu.username
            self.user_label.setText(f"<span style='color:#555'>Signed in as <b>{label}</b> ({cu.role})</span>")
            self.admin_btn.setVisible(cu.role == "admin")
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
        self.status_label.setText(
            f"{len(rows)} of {len(self._records)} client(s) shown"
            + (f" — search: '{needle}'" if needle else "")
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
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, ref = dlg.values()
        if not name:
            QMessageBox.warning(self, "Missing name", "Client name is required.")
            return
        try:
            rec = create_client(name, ref)
        except ClientError as e:
            QMessageBox.warning(self, "Could not create client", str(e))
            return
        self.refresh()
        self.open_client.emit(rec.id, rec.display_name)

    def _open_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        self.open_client.emit(rec.id, rec.display_name)
