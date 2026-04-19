"""Client detail view — import drop zone + affordability summary + actions."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..affordability.calculator import compute_for_client
from ..export.xlsx import export_client_workbook
from .widgets.dropzone import DropZone
from .workers.ingest_worker import run_ingest_in_thread


class ClientDetailView(QWidget):
    back_requested = Signal()
    review_requested = Signal()

    def __init__(self, client_id: int, client_name: str) -> None:
        super().__init__()
        self.client_id = client_id
        self.client_name = client_name
        self._thread: QThread | None = None
        self._worker = None
        self._pending_paths: list[Path] = []

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        back = QPushButton("← Clients")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addStretch(1)
        header.addWidget(QLabel(f"<h1>{client_name}</h1>"))
        header.addStretch(1)
        layout.addLayout(header)

        self.dropzone = DropZone()
        self.dropzone.files_dropped.connect(self._on_files_queued)
        layout.addWidget(self.dropzone)

        actions = QHBoxLayout()
        browse = QPushButton("Browse files…")
        browse.clicked.connect(self._browse)
        actions.addWidget(browse)
        self.clear_btn = QPushButton("Clear queue")
        self.clear_btn.clicked.connect(self._clear_queue)
        self.clear_btn.setEnabled(False)
        actions.addWidget(self.clear_btn)
        self.process_btn = QPushButton("Process queued files")
        self.process_btn.clicked.connect(self._process_queue)
        self.process_btn.setEnabled(False)
        actions.addWidget(self.process_btn)
        actions.addStretch(1)
        review = QPushButton("Review transactions →")
        review.clicked.connect(self.review_requested.emit)
        actions.addWidget(review)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)

        self.file_log = QListWidget()
        self.file_log.setMaximumHeight(180)
        layout.addWidget(self.file_log)
        self._queue_items: dict[str, QListWidgetItem] = {}

        layout.addWidget(self._build_affordability_panel())

        self.refresh()

    def _build_affordability_panel(self) -> QGroupBox:
        group = QGroupBox("Affordability summary")
        outer = QVBoxLayout(group)

        form = QFormLayout()
        self.declared = QLineEdit()
        self.declared.setPlaceholderText("Override declared income (optional)")
        self.declared.editingFinished.connect(self.refresh)
        form.addRow("Declared income", self.declared)
        outer.addLayout(form)

        grid = QFormLayout()
        self.l_period = QLabel("—")
        self.l_income = QLabel("—")
        self.l_committed = QLabel("—")
        self.l_discretionary = QLabel("—")
        self.l_outgoings = QLabel("—")
        self.l_net = QLabel("—")
        self.l_monthly_income = QLabel("—")
        self.l_monthly_net = QLabel("—")
        grid.addRow("Period", self.l_period)
        grid.addRow("Income (total)", self.l_income)
        grid.addRow("Committed (total)", self.l_committed)
        grid.addRow("Discretionary (total)", self.l_discretionary)
        grid.addRow("Outgoings (total)", self.l_outgoings)
        grid.addRow("Net disposable (total)", self.l_net)
        grid.addRow("Monthly income", self.l_monthly_income)
        grid.addRow("Monthly net disposable", self.l_monthly_net)
        outer.addLayout(grid)

        buttons = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        export_btn = QPushButton("Export XLSX…")
        export_btn.clicked.connect(self._export)
        buttons.addWidget(export_btn)
        outer.addLayout(buttons)

        return group

    def refresh(self) -> None:
        declared = self._declared_income()
        report = compute_for_client(self.client_id, declared_income=declared)
        if report.period_start and report.period_end:
            self.l_period.setText(f"{report.period_start.isoformat()} → {report.period_end.isoformat()}")
        else:
            self.l_period.setText("—")
        self.l_income.setText(f"£{report.income_total:,.2f}")
        self.l_committed.setText(f"£{report.committed_total:,.2f}")
        self.l_discretionary.setText(f"£{report.discretionary_total:,.2f}")
        self.l_outgoings.setText(f"£{report.outgoings_total:,.2f}")
        self.l_net.setText(f"£{report.net_disposable:,.2f}")
        self.l_monthly_income.setText(f"£{report.monthly_income:,.2f}")
        self.l_monthly_net.setText(f"£{report.monthly_net_disposable:,.2f}")

    def _declared_income(self) -> Decimal | None:
        raw = self.declared.text().strip()
        if not raw:
            return None
        try:
            return Decimal(raw.replace("£", "").replace(",", ""))
        except InvalidOperation:
            return None

    def _browse(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select statements", "", "Statements (*.pdf *.csv *.xlsx);;All files (*)"
        )
        if files:
            self._on_files_queued([Path(f) for f in files])

    def _on_files_queued(self, paths: list[Path]) -> None:
        if self._thread is not None:
            QMessageBox.information(self, "Busy", "Processing in progress — please wait.")
            return
        added = 0
        for p in paths:
            key = str(p.resolve())
            if key in self._queue_items:
                continue
            self._pending_paths.append(p)
            item = QListWidgetItem(f"⏳ Queued: {p.name}")
            self.file_log.addItem(item)
            self._queue_items[key] = item
            added += 1
        if added:
            self.progress_label.setVisible(True)
            self.progress_label.setText(
                f"{len(self._pending_paths)} file(s) queued. Click 'Process queued files' to start."
            )
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_queue = bool(self._pending_paths)
        running = self._thread is not None
        self.process_btn.setEnabled(has_queue and not running)
        self.clear_btn.setEnabled(has_queue and not running)

    def _clear_queue(self) -> None:
        if self._thread is not None:
            return
        for item in self._queue_items.values():
            row = self.file_log.row(item)
            if row >= 0:
                self.file_log.takeItem(row)
        self._queue_items.clear()
        self._pending_paths.clear()
        self.progress_label.setText("Queue cleared.")
        self._update_buttons()

    def _process_queue(self) -> None:
        if self._thread is not None or not self._pending_paths:
            return
        paths = list(self._pending_paths)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(paths))
        self.progress.setValue(0)
        self.progress_label.setVisible(True)
        self.progress_label.setText(f"Processing {len(paths)} file(s)…")

        thread, worker = run_ingest_in_thread(self.client_id, paths)
        self._thread = thread
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.file_done.connect(self._on_file_done)
        worker.error.connect(self._on_file_error)
        worker.all_done.connect(self._on_all_done)
        thread.start()
        self._update_buttons()

    def _find_queue_item(self, name: str) -> QListWidgetItem | None:
        for key, item in self._queue_items.items():
            if Path(key).name == name:
                return item
        return None

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress_label.setText(message)

    def _on_file_done(self, result) -> None:
        # Look up the queue item by original filename via the stored path.
        # Use the worker's emitted message to update the right row; fallback
        # appends a new row so the user never loses status for a file.
        item = None
        # Try to find an in-flight queued item in order (first pending).
        for key, queued in list(self._queue_items.items()):
            if queued.text().startswith("⏳") or queued.text().startswith("…"):
                item = queued
                name = Path(key).name
                break
        else:
            name = "(unknown)"
        text = (
            f"⚠ {name}: already imported — skipped (statement {result.statement_id})"
            if result.duplicate
            else f"✓ {name}: {result.file_kind} · {result.transaction_count} rows (statement {result.statement_id})"
        )
        if item is not None:
            item.setText(text)
            self._queue_items = {k: v for k, v in self._queue_items.items() if v is not item}
        else:
            self.file_log.addItem(text)

    def _on_file_error(self, name: str, message: str) -> None:
        item = self._find_queue_item(name)
        text = f"✗ {name}: {message}"
        if item is not None:
            item.setText(text)
            for k, v in list(self._queue_items.items()):
                if v is item:
                    del self._queue_items[k]
                    break
        else:
            self.file_log.addItem(text)

    def _on_all_done(self, ok: int, fail: int) -> None:
        self.progress_label.setText(f"Done — {ok} file(s) processed, {fail} failed")
        self._thread = None
        self._worker = None
        self._pending_paths.clear()
        self._queue_items.clear()
        self._update_buttons()
        self.refresh()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export affordability workbook",
            f"{self.client_name.replace(' ', '_')}_affordability.xlsx",
            "Excel workbook (*.xlsx)"
        )
        if not path:
            return
        try:
            out = export_client_workbook(self.client_id, Path(path), declared_income=self._declared_income())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))
            return
        QMessageBox.information(self, "Export complete", f"Saved to:\n{out}")
