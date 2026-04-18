"""Clients list + "new client" dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from ..auth.session import get_current
from ..clients.service import ClientError, create_client, list_clients


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


class ClientsView(QWidget):
    open_client = Signal(int, str)   # (client_id, name)
    logout_requested = Signal()
    admin_requested = Signal()

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.title = QLabel("<h1>Clients</h1>")
        header.addWidget(self.title)
        header.addStretch(1)
        self.user_label = QLabel()
        header.addWidget(self.user_label)
        self.admin_btn = QPushButton("Admin…")
        self.admin_btn.clicked.connect(self.admin_requested.emit)
        header.addWidget(self.admin_btn)
        logout = QPushButton("Log out")
        logout.clicked.connect(self.logout_requested.emit)
        header.addWidget(logout)
        layout.addLayout(header)

        actions = QHBoxLayout()
        new_btn = QPushButton("+  New client")
        new_btn.clicked.connect(self._new_client)
        actions.addWidget(new_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Name", "Reference", "Created", "ID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        open_btn = QPushButton("Open selected client")
        open_btn.clicked.connect(self._open_selected)
        layout.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def refresh(self) -> None:
        cu = get_current()
        if cu is not None:
            label = cu.full_name or cu.username
            self.user_label.setText(f"<span style='color:#555'>Signed in as <b>{label}</b> ({cu.role})</span>")
            self.admin_btn.setVisible(cu.role == "admin")
        clients = list_clients()
        self.table.setRowCount(len(clients))
        for row, c in enumerate(clients):
            self.table.setItem(row, 0, QTableWidgetItem(c.display_name))
            self.table.setItem(row, 1, QTableWidgetItem(c.reference or ""))
            self.table.setItem(row, 2, QTableWidgetItem(c.created_at.strftime("%Y-%m-%d")))
            id_item = QTableWidgetItem(str(c.id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, id_item)

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
        row = self.table.currentRow()
        if row < 0:
            return
        client_id = int(self.table.item(row, 3).text())
        name = self.table.item(row, 0).text()
        self.open_client.emit(client_id, name)
