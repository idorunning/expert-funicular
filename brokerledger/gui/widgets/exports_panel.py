"""Reusable widget + dialog for browsing a client's past exports.

The client's ``{folder}/exports/`` directory is populated by every export from
the Review/Client Detail views — one copy per primary format plus an always-on
PDF snapshot for data compliance.  This widget lists them sorted newest-first
and lets the user open a file with the system's default handler.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


_COL_NAME = 0
_COL_FORMAT = 1
_COL_DATE = 2
_COL_SIZE = 3


def _human_size(num_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


class ExportsPanel(QWidget):
    """Embedded widget listing files inside a client's exports/ folder."""

    def __init__(
        self,
        folder_path: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._folder = Path(folder_path) / "exports"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("<b>Past exports</b>")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("GhostButton")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        self.reveal_btn = QPushButton("Open folder")
        self.reveal_btn.setObjectName("GhostButton")
        self.reveal_btn.setToolTip("Open the exports folder in your file manager")
        self.reveal_btn.clicked.connect(self._open_folder)
        header.addWidget(self.reveal_btn)
        layout.addLayout(header)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Name", "Format", "Saved", "Size"])
        hv = self.table.horizontalHeader()
        hv.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        hv.setSectionResizeMode(_COL_FORMAT, QHeaderView.ResizeMode.ResizeToContents)
        hv.setSectionResizeMode(_COL_DATE, QHeaderView.ResizeMode.ResizeToContents)
        hv.setSectionResizeMode(_COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.cellDoubleClicked.connect(self._open_selected)
        layout.addWidget(self.table)

        self.empty_label = QLabel(
            "No exports yet. Open a client and click <b>Export…</b> to create one."
        )
        self.empty_label.setStyleSheet("QLabel { color: #6B6679; padding: 12px; }")
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        files: list[Path] = []
        if self._folder.is_dir():
            for p in self._folder.iterdir():
                if p.is_file():
                    files.append(p)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        self.table.setRowCount(len(files))
        for row, p in enumerate(files):
            st = p.stat()
            name_item = QTableWidgetItem(p.name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.table.setItem(row, _COL_NAME, name_item)

            fmt = p.suffix.lstrip(".").lower() or "?"
            self.table.setItem(row, _COL_FORMAT, QTableWidgetItem(fmt.upper()))

            when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            self.table.setItem(row, _COL_DATE, QTableWidgetItem(when))

            size_item = QTableWidgetItem(_human_size(st.st_size))
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, _COL_SIZE, size_item)

        self.empty_label.setVisible(len(files) == 0)
        self.table.setVisible(len(files) > 0)

    # ------------------------------------------------------------------

    def _open_folder(self) -> None:
        if not self._folder.exists():
            try:
                self._folder.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                QMessageBox.warning(self, "Could not open", str(e))
                return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))

    def _selected_path(self) -> Path | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, _COL_NAME)
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        return Path(path) if path else None

    def _open_selected(self, *_args) -> None:
        p = self._selected_path()
        if p is None or not p.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _context_menu(self, pos) -> None:
        p = self._selected_path()
        if p is None:
            return
        menu = QMenu(self)
        open_act = QAction("Open", self)
        open_act.triggered.connect(self._open_selected)
        menu.addAction(open_act)
        reveal_act = QAction("Open containing folder", self)
        reveal_act.triggered.connect(self._open_folder)
        menu.addAction(reveal_act)
        menu.exec(self.table.viewport().mapToGlobal(pos))


class ExportsDialog(QDialog):
    """Modal popup showing a client's past exports.

    Invoked from the client list's context menu — lets the user jump to any
    saved report without first opening the detail view.
    """

    def __init__(
        self,
        *,
        client_id: int,
        client_name: str,
        folder_path: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Past exports — {client_name}")
        self.setMinimumSize(620, 420)
        self._client_id = client_id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel(
            f"<p style='margin:0'>Exports saved for <b>{client_name}</b>:</p>"
        ))
        self.panel = ExportsPanel(folder_path)
        layout.addWidget(self.panel, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)
