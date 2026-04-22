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
    QProgressBar,
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
    soft_delete_client,
)
from ..users import service as users_service


# Status codes used in the combo's userData and as sort keys.
_STATUS_ACTIVE = "active"
_STATUS_CLOSED = "closed"
_STATUS_DELETED = "deleted"


def _status_for(rec: ClientRecord) -> str:
    if rec.deleted_at is not None:
        return _STATUS_DELETED
    if rec.archived_at is not None:
        return _STATUS_CLOSED
    return _STATUS_ACTIVE


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


# Column indexes — single source of truth.
_COL_PROCESSING = 0
_COL_NAME = 1
_COL_REFERENCE = 2
_COL_MANAGER = 3
_COL_CREATED = 4
_COL_STATUS = 5
_COL_ID = 6


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

        # Top row — title + collapsed menu button.  (Motivational greeting has
        # been removed per broker feedback — the list itself is the start of
        # each session's work.)
        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("<h1 style='margin:0'>Clients</h1>")
        header.addWidget(title)
        header.addStretch(1)

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
        self.show_closed = QCheckBox("Show closed")
        self.show_closed.setToolTip("Include clients that have been marked Closed.")
        self.show_closed.stateChanged.connect(self.refresh)
        toolbar.addWidget(self.show_closed)
        # Admin-only — see soft-deleted clients.
        self.show_deleted = QCheckBox("Show deleted")
        self.show_deleted.setToolTip("Administrators only — include soft-deleted clients.")
        self.show_deleted.setVisible(False)
        self.show_deleted.stateChanged.connect(self.refresh)
        toolbar.addWidget(self.show_deleted)
        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["", "Name", "Reference", "Client manager", "Created", "Status", "ID"]
        )
        header_view = self.table.horizontalHeader()
        header_view.setSectionsClickable(True)
        header_view.setSectionsMovable(True)  # reorderable columns (drag header)
        header_view.setSortIndicatorShown(True)
        header_view.setSectionResizeMode(_COL_PROCESSING, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(_COL_PROCESSING, 120)
        header_view.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(_COL_REFERENCE, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(_COL_MANAGER, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(_COL_CREATED, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(_COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(_COL_ID, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
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
        self._processing_ids: set[int] = set()

    # ---- public API -----------------------------------------------------

    def set_processing_clients(self, ids: set[int]) -> None:
        """Called by the main window when workers start/stop.

        Updates the progress-bar column in place (no full refresh, so sort
        order and selection are preserved).
        """
        self._processing_ids = set(ids)
        self._repaint_processing_column()

    # ---- rendering ------------------------------------------------------

    def refresh(self) -> None:
        cu = get_current()
        if cu is not None:
            label = cu.full_name or cu.username
            self.user_label.setText(
                f"<span style='color:#555'>Signed in as <b>{label}</b> ({cu.role})</span>"
            )
            is_admin = cu.role == "admin"
            self.admin_action.setVisible(is_admin)
            self.audit_action.setVisible(is_admin)
            self.show_deleted.setVisible(is_admin)
            if not is_admin:
                self.show_deleted.setChecked(False)
        else:
            self.user_label.setText("")
        try:
            self._records = list_clients(
                include_archived=self.show_closed.isChecked(),
                include_deleted=self.show_deleted.isChecked(),
            )
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
                or (r.created_by_name or "").lower().find(needle) >= 0
            ]
        # Rebuilding the table removes cell widgets; disable sorting while we
        # populate so row indexes stay stable, then re-enable.
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for idx, c in enumerate(rows):
            self._populate_row(idx, c)
        self.table.setSortingEnabled(True)
        self._repaint_processing_column()
        hint = "Double-click to open, or right-click for more actions."
        self.status_label.setText(
            f"{len(rows)} of {len(self._records)} client(s) shown"
            + (f" — search: '{needle}'" if needle else "")
            + f"  ·  {hint}"
        )

    def _populate_row(self, row: int, c: ClientRecord) -> None:
        # Processing column (widget added/removed by _repaint_processing_column).
        # A placeholder item lets sorting work without crashing when empty.
        proc_item = QTableWidgetItem("")
        proc_item.setFlags(proc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, _COL_PROCESSING, proc_item)

        name_item = QTableWidgetItem(c.display_name)
        self.table.setItem(row, _COL_NAME, name_item)

        ref_item = QTableWidgetItem(c.reference or "")
        self.table.setItem(row, _COL_REFERENCE, ref_item)

        manager_item = QTableWidgetItem(c.created_by_name or "")
        self.table.setItem(row, _COL_MANAGER, manager_item)

        created_item = QTableWidgetItem(c.created_at.strftime("%Y-%m-%d"))
        # ISO-format dates already sort lexicographically; no extra role needed.
        self.table.setItem(row, _COL_CREATED, created_item)

        # Status cell hosts a live combo box so archiving is one click.
        status_code = _status_for(c)
        # The underlying item holds the status text so the column still sorts
        # correctly even though a combo widget covers it.
        status_item = QTableWidgetItem(status_code)
        self.table.setItem(row, _COL_STATUS, status_item)
        combo = self._make_status_combo(c, status_code)
        self.table.setCellWidget(row, _COL_STATUS, combo)

        id_item = QTableWidgetItem()
        id_item.setData(Qt.ItemDataRole.DisplayRole, c.id)  # numeric sort
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, _COL_ID, id_item)

    def _make_status_combo(self, rec: ClientRecord, status: str) -> QComboBox:
        cu = get_current()
        is_admin = cu is not None and cu.role == "admin"
        combo = QComboBox()
        combo.addItem("Active", userData=_STATUS_ACTIVE)
        combo.addItem("Closed", userData=_STATUS_CLOSED)
        if is_admin or status == _STATUS_DELETED:
            combo.addItem("Deleted", userData=_STATUS_DELETED)
        target_idx = combo.findData(status)
        if target_idx < 0:
            # Non-admin viewing a deleted row (shouldn't happen, but be safe).
            combo.addItem("Deleted", userData=_STATUS_DELETED)
            target_idx = combo.findData(status)
        combo.setCurrentIndex(max(0, target_idx))
        # Anchor on client_id because row indexes shift when the user re-sorts.
        cid = rec.id
        combo.currentIndexChanged.connect(
            lambda new_idx, client_id=cid, box=combo: self._on_status_changed(client_id, box)
        )
        return combo

    def _repaint_processing_column(self) -> None:
        for row in range(self.table.rowCount()):
            cid = self._client_id_for_row(row)
            if cid is not None and cid in self._processing_ids:
                existing = self.table.cellWidget(row, _COL_PROCESSING)
                if not isinstance(existing, QProgressBar):
                    bar = QProgressBar()
                    bar.setRange(0, 0)           # indeterminate
                    bar.setTextVisible(False)
                    bar.setFixedHeight(10)
                    bar.setToolTip("Processing in the background…")
                    self.table.setCellWidget(row, _COL_PROCESSING, bar)
            else:
                if self.table.cellWidget(row, _COL_PROCESSING) is not None:
                    self.table.removeCellWidget(row, _COL_PROCESSING)

    # ---- row helpers ---------------------------------------------------

    def _client_id_for_row(self, row: int) -> int | None:
        item = self.table.item(row, _COL_ID)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.DisplayRole)
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _record_for_id(self, client_id: int) -> ClientRecord | None:
        for r in self._records:
            if r.id == client_id:
                return r
        return None

    def _selected_record(self) -> ClientRecord | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        cid = self._client_id_for_row(row)
        if cid is None:
            return None
        return self._record_for_id(cid)

    # ---- status combo slot ---------------------------------------------

    def _on_status_changed(self, client_id: int, combo: QComboBox) -> None:
        rec = self._record_for_id(client_id)
        if rec is None:
            return
        current_status = _status_for(rec)
        target = combo.currentData()
        if target == current_status:
            return
        cu = get_current()
        is_admin = cu is not None and cu.role == "admin"

        if target == _STATUS_DELETED and not is_admin:
            QMessageBox.warning(
                self, "Administrators only",
                "Only an administrator can mark a client as Deleted.",
            )
            self.refresh()
            return

        try:
            if target == _STATUS_ACTIVE:
                # Lift whichever marker is set (Closed or Deleted).
                restore_client(client_id)
            elif target == _STATUS_CLOSED:
                # If coming back from Deleted we first need restore, then archive.
                if current_status == _STATUS_DELETED:
                    restore_client(client_id)
                if QMessageBox.question(
                    self, "Close client",
                    f"Mark '{rec.display_name}' as Closed?  It will be hidden "
                    "until you toggle 'Show closed'.",
                ) != QMessageBox.StandardButton.Yes:
                    self.refresh()
                    return
                archive_client(client_id)
            elif target == _STATUS_DELETED:
                if QMessageBox.question(
                    self, "Delete client (soft)",
                    f"Mark '{rec.display_name}' as Deleted?  Only administrators "
                    "will be able to see it, and they can restore it later.",
                ) != QMessageBox.StandardButton.Yes:
                    self.refresh()
                    return
                soft_delete_client(client_id)
        except (ClientError, PermissionError) as e:
            QMessageBox.warning(self, "Status change failed", str(e))
        self.refresh()

    # ---- context menu --------------------------------------------------

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

        if rec.archived_at is None and rec.deleted_at is None:
            arch_act = QAction("Close", self)
            arch_act.triggered.connect(self._archive_selected)
            menu.addAction(arch_act)
        else:
            restore_act = QAction("Restore to Active", self)
            restore_act.triggered.connect(self._restore_selected)
            menu.addAction(restore_act)

        menu.addSeparator()
        reassign_act = QAction("Reassign to…", self)
        reassign_act.triggered.connect(self._reassign_selected)
        reassign_act.setEnabled(is_admin)
        menu.addAction(reassign_act)

        purge_act = QAction("Permanently delete…  (irreversible)", self)
        purge_act.triggered.connect(self._delete_selected)
        purge_act.setEnabled(is_admin)
        menu.addAction(purge_act)

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
            self, "Close client",
            f"Mark '{rec.display_name}' as Closed?  It will be hidden until you "
            "toggle 'Show closed'.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            archive_client(rec.id)
        except ClientError as e:
            QMessageBox.warning(self, "Close failed", str(e))
            return
        self.refresh()

    def _restore_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        try:
            restore_client(rec.id)
        except (ClientError, PermissionError) as e:
            QMessageBox.warning(self, "Restore failed", str(e))
            return
        self.refresh()

    def _reassign_selected(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        dlg = ReassignDialog(current_owner_id=rec.created_by, parent=self)
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
        cu = get_current()
        if cu is None or cu.role != "admin":
            QMessageBox.warning(
                self, "Administrators only",
                "Only administrators can permanently delete clients.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Permanently delete client?",
            f"Permanently delete '{rec.display_name}'?\n\n"
            "All statements and transactions for this client will be removed. "
            "This cannot be undone.\n\n"
            "(Use the Status column's 'Deleted' option for a recoverable soft-delete.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
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
