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
        self.dropzone.files_dropped.connect(self._on_files_dropped)
        layout.addWidget(self.dropzone)

        actions = QHBoxLayout()
        browse = QPushButton("Browse files…")
        browse.clicked.connect(self._browse)
        actions.addWidget(browse)
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
        self.file_log.setMaximumHeight(140)
        layout.addWidget(self.file_log)

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
        self.l_months = QLabel("—")
        self.l_income = QLabel("—")
        self.l_committed = QLabel("—")
        self.l_discretionary = QLabel("—")
        self.l_outgoings = QLabel("—")
        self.l_net = QLabel("—")
        self.l_monthly_income = QLabel("—")
        self.l_monthly_net = QLabel("—")
        grid.addRow("Period", self.l_period)
        grid.addRow("Months in window", self.l_months)
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
        self.l_months.setText(f"{report.months_in_window:.2f}")
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
            self._on_files_dropped([Path(f) for f in files])

    def _on_files_dropped(self, paths: list[Path]) -> None:
        if self._thread is not None:
            QMessageBox.information(self, "Busy", "Ingest in progress — please wait.")
            return
        self._pending_paths = list(paths)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(paths))
        self.progress.setValue(0)
        self.progress_label.setVisible(True)
        self.progress_label.setText(f"Processing {len(paths)} file(s)…")
        self.file_log.clear()

        thread, worker = run_ingest_in_thread(self.client_id, self._pending_paths)
        self._thread = thread
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.file_done.connect(self._on_file_done)
        worker.error.connect(self._on_file_error)
        worker.all_done.connect(self._on_all_done)
        thread.start()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress_label.setText(message)

    def _on_file_done(self, result) -> None:
        if result.duplicate:
            self.file_log.addItem(f"⚠ Already imported — skipped (statement {result.statement_id})")
        else:
            self.file_log.addItem(f"✓ {result.file_kind}: {result.transaction_count} rows (statement {result.statement_id})")

    def _on_file_error(self, name: str, message: str) -> None:
        self.file_log.addItem(f"✗ {name}: {message}")

    def _on_all_done(self, ok: int, fail: int) -> None:
        self.progress_label.setText(f"Done — {ok} file(s) processed, {fail} failed")
        self._thread = None
        self._worker = None
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
