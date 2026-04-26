"""Client detail view — import drop zone + affordability summary + actions."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sqlalchemy import func, select

from ..affordability.calculator import compute_for_client
from ..auth.session import get_current
from ..clients.service import ClientError, count_flagged_transactions, get_client, verify_statement
from ..db.engine import session_scope
from ..db.models import AuditLog, Statement, Transaction, User
from ..categorize.taxonomy import (
    COMMITTED_CATEGORIES,
    DISCRETIONARY_CATEGORIES,
)

from .theme import SUCCESS
from .widgets.dropzone import DropZone
from .workers.ingest_worker import run_ingest_in_thread
from .workers.recategorize_worker import run_recategorize_in_thread


class ClientDetailView(QWidget):
    back_requested = Signal()
    review_requested = Signal()
    processing_changed = Signal(bool)
    tx_persisted = Signal(int, int)  # (client_id, tx_id) — forwarded from workers for live Review updates
    # Emitted after ingest / re-categorisation when at least one transaction
    # needs human review. Carries the flagged count.
    review_flagged_requested = Signal(int)

    def __init__(self, client_id: int, client_name: str) -> None:
        super().__init__()
        self.client_id = client_id
        self.client_name = client_name
        self._thread: QThread | None = None
        self._worker = None
        self._recategorize_thread: QThread | None = None
        self._recategorize_worker = None
        self._pending_paths: list[Path] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        self.back_btn = QPushButton("← Clients")
        self.back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(self.back_btn)
        header.addStretch(1)
        title_label = QLabel(client_name)
        title_label.setStyleSheet(
            "QLabel { font-size: 22px; font-weight: 600; color: #1F1030; }"
        )
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(title_label)
        header.addStretch(1)
        layout.addLayout(header)

        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        self.notice.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.notice.linkActivated.connect(self._on_notice_link)
        layout.addWidget(self.notice)

        self.dropzone = DropZone()
        self.dropzone.files_dropped.connect(self._on_files_queued)
        layout.addWidget(self.dropzone)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        browse = QPushButton("Browse files…")
        browse.clicked.connect(self._browse)
        actions.addWidget(browse)
        self.clear_btn = QPushButton("Clear queue")
        self.clear_btn.setObjectName("GhostButton")
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

        self.file_log = QListWidget()
        self.file_log.setMaximumHeight(160)
        layout.addWidget(self.file_log)
        self._queue_items: dict[str, QListWidgetItem] = {}

        layout.addWidget(self._build_statements_panel())
        layout.addWidget(self._build_affordability_panel())
        layout.addWidget(self._build_exports_panel())
        layout.addStretch(1)

        self.refresh()
        self._refresh_statements_table()

    def _build_statements_panel(self) -> QGroupBox:
        group = QGroupBox("Statements")
        outer = QVBoxLayout(group)
        outer.setContentsMargins(12, 18, 12, 12)
        outer.setSpacing(8)

        hint = QLabel(
            "Each statement can be marked as verified once all flagged transactions "
            "have been reviewed."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6B6679;")
        outer.addWidget(hint)

        self.statements_table = QTableWidget()
        self.statements_table.setColumnCount(6)
        self.statements_table.setHorizontalHeaderLabels(
            ["Imported", "File", "Rows", "Flagged", "Status", ""]
        )
        header = self.statements_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.statements_table.verticalHeader().setVisible(False)
        self.statements_table.setAlternatingRowColors(True)
        self.statements_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.statements_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.statements_table.setMinimumHeight(120)
        outer.addWidget(self.statements_table)

        self.statements_empty = QLabel(
            "<i>No statements imported yet. Drop a file above to begin.</i>"
        )
        self.statements_empty.setStyleSheet("color: #6B6679;")
        self.statements_empty.setVisible(False)
        outer.addWidget(self.statements_empty)

        return group

    def _build_exports_panel(self) -> QGroupBox:
        """Group-box wrapping the reusable ExportsPanel widget."""
        from .widgets.exports_panel import ExportsPanel

        group = QGroupBox("Past exports")
        outer = QVBoxLayout(group)
        outer.setContentsMargins(12, 18, 12, 12)
        outer.setSpacing(6)

        hint = QLabel(
            "Every export is filed here with a data-compliance PDF snapshot. "
            "Double-click a file to open it."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6B6679;")
        outer.addWidget(hint)

        try:
            client = get_client(self.client_id)
            folder = client.folder_path
        except ClientError:
            folder = ""
        self.exports_panel = ExportsPanel(folder)
        outer.addWidget(self.exports_panel)

        return group

    def _load_statements(self) -> list[dict]:
        with session_scope() as s:
            stmts = s.execute(
                select(Statement)
                .where(Statement.client_id == self.client_id)
                .order_by(Statement.imported_at.desc())
            ).scalars().all()
            rows: list[dict] = []
            for st in stmts:
                flagged = int(
                    s.execute(
                        select(func.count()).select_from(Transaction).where(
                            Transaction.statement_id == st.id,
                            Transaction.needs_review == 1,
                        )
                    ).scalar_one()
                )
                verifier_name: str | None = None
                if st.verified_by is not None:
                    u = s.get(User, st.verified_by)
                    verifier_name = u.username if u else None
                rows.append({
                    "id": st.id,
                    "imported_at": st.imported_at,
                    "original_name": st.original_name,
                    "row_count": st.row_count,
                    "flagged": flagged,
                    "verified_at": st.verified_at,
                    "verified_by_username": verifier_name,
                })
            return rows

    def _refresh_statements_table(self) -> None:
        rows = self._load_statements()
        self.statements_table.setRowCount(len(rows))
        self.statements_empty.setVisible(not rows)
        self.statements_table.setVisible(bool(rows))
        for idx, row in enumerate(rows):
            imported = row["imported_at"]
            imported_text = imported.strftime("%Y-%m-%d %H:%M") if imported else "—"
            name_item = QTableWidgetItem(row["original_name"])
            name_item.setToolTip(row["original_name"])
            rows_text = str(row["row_count"]) if row["row_count"] is not None else "—"
            flagged = row["flagged"]
            flagged_item = QTableWidgetItem(str(flagged))
            flagged_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if flagged:
                flagged_item.setForeground(QBrush(QColor("#A52D1E")))

            if row["verified_at"] is not None:
                stamp = row["verified_at"].strftime("%Y-%m-%d %H:%M")
                who = row["verified_by_username"] or "—"
                status_item = QTableWidgetItem(f"✓ Verified {stamp} · {who}")
                status_item.setForeground(QBrush(QColor(SUCCESS)))
            elif flagged > 0:
                status_item = QTableWidgetItem("Review needed")
                status_item.setForeground(QBrush(QColor("#A52D1E")))
            else:
                status_item = QTableWidgetItem("Ready to verify")
                status_item.setForeground(QBrush(QColor("#6B6679")))

            self.statements_table.setItem(idx, 0, QTableWidgetItem(imported_text))
            self.statements_table.setItem(idx, 1, name_item)
            rc_item = QTableWidgetItem(rows_text)
            rc_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.statements_table.setItem(idx, 2, rc_item)
            self.statements_table.setItem(idx, 3, flagged_item)
            self.statements_table.setItem(idx, 4, status_item)

            if row["verified_at"] is None:
                verify_btn = QPushButton("Verify ✓")
                verify_btn.setEnabled(flagged == 0)
                if flagged > 0:
                    verify_btn.setToolTip(
                        f"{flagged} transaction(s) still need review before you can verify."
                    )
                else:
                    verify_btn.setToolTip("Mark this statement as reviewed and complete.")
                verify_btn.clicked.connect(
                    lambda _checked=False, sid=row["id"]: self._on_verify_statement(sid)
                )
                self.statements_table.setCellWidget(idx, 5, verify_btn)
            else:
                self.statements_table.setCellWidget(idx, 5, QLabel(""))

    def _on_verify_statement(self, statement_id: int) -> None:
        try:
            verify_statement(statement_id)
        except ClientError as e:
            QMessageBox.warning(self, "Cannot verify yet", str(e))
            self._refresh_statements_table()
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Verify failed", str(e))
            return
        self._refresh_statements_table()

    def _build_affordability_panel(self) -> QGroupBox:
        group = QGroupBox("Affordability summary")
        outer = QVBoxLayout(group)
        outer.setContentsMargins(12, 18, 12, 12)
        outer.setSpacing(10)

        form = QFormLayout()
        self.declared = QLineEdit()
        self.declared.setPlaceholderText("Override declared income (optional)")
        self.declared.editingFinished.connect(self.refresh)
        form.addRow("Declared income", self.declared)

        outer.addLayout(form)

        headline = QFormLayout()
        self.l_period = QLabel("—")
        self.l_income = QLabel("—")
        self.l_outgoings = QLabel("—")
        self.l_net = QLabel("—")
        for lbl in (self.l_income, self.l_outgoings, self.l_net):
            lbl.setStyleSheet("font-weight:600")
        headline.addRow("Period", self.l_period)
        headline.addRow("Income (total)", self.l_income)
        headline.addRow("Outgoings (total)", self.l_outgoings)
        headline.addRow("Net disposable (total)", self.l_net)
        outer.addLayout(headline)

        self._category_value_labels: dict[str, QLabel] = {}
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addWidget(self._build_category_column(
            "Committed expenditure", COMMITTED_CATEGORIES
        ), stretch=1)
        columns.addWidget(self._build_category_column(
            "Discretionary expenditure", DISCRETIONARY_CATEGORIES
        ), stretch=1)
        outer.addLayout(columns)

        buttons = QHBoxLayout()
        recalculate_btn = QPushButton("Recalculate")
        recalculate_btn.setToolTip("Recalculate affordability from current transaction categories")
        recalculate_btn.clicked.connect(self.refresh)
        buttons.addWidget(recalculate_btn)
        self.recategorize_btn = QPushButton("Re-run category assignment")
        self.recategorize_btn.setToolTip(
            "Re-assign categories to all transactions (keeps your manual fixes)"
        )
        self.recategorize_btn.clicked.connect(self._start_recategorize)
        buttons.addWidget(self.recategorize_btn)
        self.review_btn = QPushButton("Review transactions →")
        self.review_btn.setObjectName("GhostButton")
        self.review_btn.clicked.connect(self.review_requested.emit)
        buttons.addWidget(self.review_btn)
        buttons.addStretch(1)
        export_btn = QPushButton("Export PDF")
        export_btn.setObjectName("PrimaryButton")
        export_btn.setToolTip("Save the affordability report as a PDF")
        export_btn.clicked.connect(self._export)
        buttons.addWidget(export_btn)
        outer.addLayout(buttons)

        return group

    def _build_category_column(self, title: str, categories: tuple[str, ...]) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for cat in categories:
            value = QLabel("£0.00")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setStyleSheet(
                "padding:4px 8px; border:1px solid #D9D3E1; border-radius:4px; "
                "background:#FFFFFF; min-width:120px"
            )
            self._category_value_labels[cat] = value
            form.addRow(QLabel(cat), value)
        return box

    def _wrap(self, inner_layout) -> QWidget:
        w = QWidget()
        w.setLayout(inner_layout)
        return w

    def refresh(self) -> None:
        declared = self._declared_income()
        report = compute_for_client(
            self.client_id,
            declared_income=declared,
        )
        if report.period_start and report.period_end:
            self.l_period.setText(f"{report.period_start.isoformat()} → {report.period_end.isoformat()}")
        else:
            self.l_period.setText("—")
        self.l_income.setText(f"£{report.income_total:,.2f}")
        self.l_outgoings.setText(f"£{report.outgoings_total:,.2f}")
        self.l_net.setText(f"£{report.net_disposable:,.2f}")
        self._populate_breakdown_from_report(report)
        if hasattr(self, "statements_table"):
            self._refresh_statements_table()

    def _populate_breakdown_from_report(self, report) -> None:
        for cat, label in self._category_value_labels.items():
            totals = report.per_category.get(cat)
            total = totals.total if totals is not None else Decimal("0.00")
            label.setText(f"£{total:,.2f}")

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
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_queue = bool(self._pending_paths)
        running = self._thread is not None or self._recategorize_thread is not None
        self.process_btn.setEnabled(has_queue and not running)
        self.clear_btn.setEnabled(has_queue and not running)
        self.recategorize_btn.setEnabled(not running)

    def _clear_queue(self) -> None:
        if self._thread is not None:
            return
        for item in self._queue_items.values():
            row = self.file_log.row(item)
            if row >= 0:
                self.file_log.takeItem(row)
        self._queue_items.clear()
        self._pending_paths.clear()
        self._update_buttons()

    def _process_queue(self) -> None:
        if self._thread is not None or self._recategorize_thread is not None or not self._pending_paths:
            return
        paths = list(self._pending_paths)
        self._show_notice(
            "Thank you! I will let you know once I have finished reviewing "
            f"{'this file' if len(paths) == 1 else f'these {len(paths)} files'} for you.",
            tone="info",
        )

        thread, worker = run_ingest_in_thread(self.client_id, paths)
        self._thread = thread
        self._worker = worker
        worker.file_done.connect(self._on_file_done)
        worker.error.connect(self._on_file_error)
        worker.all_done.connect(self._on_all_done)
        worker.tx_persisted.connect(self.tx_persisted.emit)
        # CRITICAL: keep the Python references alive until thread.finished
        # actually fires (the event loop has fully exited). Otherwise GC may
        # destroy the QThread wrapper while Qt is still cleaning up, producing
        # "QThread: Destroyed while thread is still running" warnings — or worse.
        thread.finished.connect(self._on_thread_finished)
        thread.start()
        self._update_buttons()
        self.processing_changed.emit(True)

    def _find_queue_item(self, name: str) -> QListWidgetItem | None:
        for key, item in self._queue_items.items():
            if Path(key).name == name:
                return item
        return None

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
        # Do NOT clear self._thread / self._worker here — the QThread event
        # loop is still running. Wait for thread.finished (see _on_thread_finished).
        self._pending_paths.clear()
        self._queue_items.clear()
        self._update_buttons()
        self.refresh()
        self._refresh_statements_table()
        if ok > 0:
            self._notify_completion(context="statements", ok=ok, fail=fail)

    def _on_thread_finished(self) -> None:
        # Fires after the QThread's event loop has fully exited. Safe to drop
        # our Python references now — Qt has finished cleaning up.
        self._thread = None
        self._worker = None
        self._update_buttons()
        self.processing_changed.emit(self.is_processing())

    def is_processing(self) -> bool:
        return self._thread is not None or self._recategorize_thread is not None

    def _flagged_count(self) -> int:
        with session_scope() as s:
            return int(
                s.execute(
                    select(func.count()).select_from(Transaction).where(
                        Transaction.client_id == self.client_id,
                        Transaction.needs_review == 1,
                    )
                ).scalar_one()
            )

    def _maybe_emit_flagged(self) -> None:
        """Fire review_flagged_requested so MainWindow can auto-open Review."""
        count = self._flagged_count()
        if count > 0:
            self.review_flagged_requested.emit(count)

    # --- User-facing completion notifications --------------------------------

    def _user_greeting_name(self) -> str:
        cu = get_current()
        if cu is None:
            return "there"
        raw = (cu.full_name or cu.username or "").strip()
        if not raw:
            return "there"
        return raw.split()[0]

    def _show_notice(self, html_or_text: str, *, tone: str = "info") -> None:
        palette = {
            "info":    ("#EFE7F5", "#4A1766", "#D6C9E6"),   # soft purple
            "success": ("#E6F4EA", "#176B1A", "#C7E5CD"),   # soft green
            "warn":    ("#FDECEA", "#A52D1E", "#F3C8C3"),   # soft red
        }
        bg, fg, border = palette.get(tone, palette["info"])
        self.notice.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg}; "
            f"border: 1px solid {border}; border-radius: 8px; "
            "padding: 10px 12px; font-size: 14px; }"
        )
        self.notice.setText(html_or_text)
        self.notice.setVisible(True)

    def _clear_notice(self) -> None:
        self.notice.setVisible(False)
        self.notice.setText("")

    def _on_notice_link(self, href: str) -> None:
        if href == "review-flagged":
            self.review_flagged_requested.emit(self._flagged_count())
        elif href == "review":
            self.review_requested.emit()
        elif href == "dismiss":
            self._clear_notice()

    def _notify_completion(
        self,
        *,
        context: str,
        ok: int | None = None,
        fail: int | None = None,
        updated: int | None = None,
    ) -> None:
        """Show a friendly banner + modal after ingest / re-categorise.

        ``context`` is 'statements' (after ingest) or 'recategorise'.
        """
        greeting = self._user_greeting_name()
        flagged = self._flagged_count()

        if context == "statements":
            headline = f"Hi {greeting}, I've finished reviewing the statements."
            if fail:
                headline += f" ({ok or 0} imported, {fail} failed.)"
        else:
            if updated:
                headline = (
                    f"Hi {greeting}, I've finished re-checking every transaction — "
                    f"{updated} were updated."
                )
            else:
                headline = (
                    f"Hi {greeting}, I've re-checked every transaction — "
                    "nothing needed updating."
                )

        if flagged > 0:
            body_html = (
                f"<b>{headline}</b><br>"
                f"<span>There are <b>{flagged}</b> transaction(s) I'd like you to look at. "
                f"<a href='review-flagged' style='color:#4A1766;font-weight:600'>"
                "Please can you review these transactions →</a></span> "
                "<a href='dismiss' style='color:#6B6679'>dismiss</a>"
            )
            self._show_notice(body_html, tone="warn")
            QMessageBox.information(
                self,
                "All done",
                f"{headline}\n\n"
                f"There are {flagged} transaction(s) I'd like you to look at. "
                "Click OK to open the review list.",
            )
            self.review_flagged_requested.emit(flagged)
        else:
            body_html = (
                f"<b>{headline}</b><br>"
                "Everything has been categorised with high confidence — no further "
                "review needed. "
                "<a href='review' style='color:#4A1766;font-weight:600'>"
                "Open the transaction list anyway</a> · "
                "<a href='dismiss' style='color:#6B6679'>dismiss</a>"
            )
            self._show_notice(body_html, tone="success")
            QMessageBox.information(
                self,
                "All done",
                f"{headline}\n\nNothing needs your review — everything was categorised "
                "with high confidence.",
            )

    def _start_recategorize(self) -> None:
        if self._thread is not None or self._recategorize_thread is not None:
            return
        self._show_notice(
            "Re-running category assignment. I'll let you know when it finishes.",
            tone="info",
        )

        thread, worker = run_recategorize_in_thread(self.client_id)
        self._recategorize_thread = thread
        self._recategorize_worker = worker
        worker.done.connect(self._on_recategorize_done)
        worker.error.connect(self._on_recategorize_error)
        worker.tx_persisted.connect(self.tx_persisted.emit)
        thread.finished.connect(self._on_recategorize_thread_finished)
        thread.start()
        self._update_buttons()
        self.processing_changed.emit(True)

    def _on_recategorize_done(self, count: int) -> None:
        self.refresh()
        if count:
            self._notify_completion(context="recategorise", updated=count)
        else:
            self._notify_completion(context="recategorise", updated=0)

    def _on_recategorize_error(self, message: str) -> None:
        QMessageBox.critical(self, "Re-categorisation failed", message)

    def _on_recategorize_thread_finished(self) -> None:
        self._recategorize_thread = None
        self._recategorize_worker = None
        self._update_buttons()
        self.processing_changed.emit(self.is_processing())

    def _export(self) -> None:
        default_name = f"{self.client_name.replace(' ', '_')}_affordability.pdf"
        pdf_filter = "PDF affordability report (*.pdf)"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export affordability report", default_name, pdf_filter
        )
        if not path:
            return
        out_path = Path(path)
        if out_path.suffix.lower() != ".pdf":
            out_path = out_path.with_suffix(".pdf")
        try:
            from ..export.pdf import export_client_pdf
            out = export_client_pdf(
                self.client_id, out_path,
                declared_income=self._declared_income(),
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))
            return
        auto_copy = self._auto_save_copy(out, "pdf")
        self._record_export_audit(out, auto_copy, "pdf")
        message = f"Saved to:\n{out}"
        if auto_copy is not None:
            message += f"\n\nA copy was also filed under this client:\n{auto_copy}"
        QMessageBox.information(self, "Export complete", message)
        if hasattr(self, "exports_panel"):
            self.exports_panel.refresh()

    def _auto_save_copy(self, primary: Path, export_kind: str) -> Path | None:
        """Drop a dated duplicate into ``{client_folder}/exports/``.

        Returns the copied path, or ``None`` if the client folder is missing
        (the save dialog already succeeded, so we keep the UI happy even if
        auto-filing can't be performed).
        """
        try:
            client = get_client(self.client_id)
        except ClientError:
            return None
        folder = Path(client.folder_path)
        exports = folder / "exports"
        try:
            exports.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        suffix = primary.suffix or f".{export_kind}"
        target = exports / f"{stamp}-transactions{suffix}"
        try:
            shutil.copy2(primary, target)
        except OSError:
            return None
        return target


    def _record_export_audit(
        self,
        primary: Path,
        auto_copy: Path | None,
        export_kind: str,
    ) -> None:
        cu = get_current()
        if cu is None:
            return
        detail = {
            "client_id": self.client_id,
            "format": export_kind,
            "path": str(primary),
        }
        if auto_copy is not None:
            detail["auto_copy_path"] = str(auto_copy)
        try:
            with session_scope() as s:
                s.add(AuditLog(
                    user_id=cu.id,
                    action="export_client",
                    entity_type="client",
                    entity_id=self.client_id,
                    detail_json=json.dumps(detail),
                ))
                s.commit()
        except Exception:  # noqa: BLE001
            # Audit logging must never block the user's export.
            return
