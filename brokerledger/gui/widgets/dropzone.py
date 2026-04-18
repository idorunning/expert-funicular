"""Drag-and-drop target for statement files."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLabel, QSizePolicy


_ACCEPTED_EXTS = {".pdf", ".csv", ".xlsx", ".xlsm"}


class DropZone(QLabel):
    files_dropped = Signal(list)  # list[Path]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setText("Drop bank statement files here (PDF / CSV / XLSX)")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #8aa; border-radius: 12px; "
            "padding: 24px; color: #556; font-size: 15px; background: #f6f8fb; }"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(
                "QLabel { border: 2px dashed #2a6; border-radius: 12px; "
                "padding: 24px; color: #163; background: #eaf7ef; font-size: 15px; }"
            )
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(
            "QLabel { border: 2px dashed #8aa; border-radius: 12px; "
            "padding: 24px; color: #556; font-size: 15px; background: #f6f8fb; }"
        )

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths: list[Path] = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in _ACCEPTED_EXTS:
                paths.append(p)
        self.dragLeaveEvent(event)
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()
