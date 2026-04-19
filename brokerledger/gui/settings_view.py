"""Settings view — AI model management + categorisation thresholds."""
from __future__ import annotations

from pathlib import Path

import httpx
from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..auth.session import get_current, set_current
from ..categorize import corrections_cache
from ..categorize.llm_client import LLMError, OllamaClient
from ..config import get_settings
from ..db import app_settings
from ..users import service as users_service
from ..utils.logging import logger
from .widgets.avatar import AvatarLabel


class _PullWorker(QObject):
    progress = Signal(str)
    done = Signal(bool, str)  # (ok, message)

    def __init__(self, base_url: str, tag: str) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.tag = tag

    def run(self) -> None:
        url = f"{self.base_url}/api/pull"
        try:
            with httpx.stream("POST", url, json={"name": self.tag}, timeout=None) as r:
                if r.status_code >= 400:
                    self.done.emit(False, f"HTTP {r.status_code}: {r.text[:200]}")
                    return
                for line in r.iter_lines():
                    if not line:
                        continue
                    self.progress.emit(line)
            self.done.emit(True, f"Pulled {self.tag}")
        except httpx.HTTPError as e:
            self.done.emit(False, f"Pull failed: {e}")


class PullModelDialog(QDialog):
    def __init__(self, base_url: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pull a model from Ollama")
        self.resize(520, 360)
        self._base_url = base_url
        self._thread: QThread | None = None
        self._worker: _PullWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Enter the model tag exactly as it appears on <b>ollama.com/library</b> "
            "(e.g. <code>gemma4:e4b</code>, <code>llama3.1:8b</code>, <code>qwen2.5:14b</code>)."
        ))
        row = QHBoxLayout()
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("gemma4:e4b")
        row.addWidget(self.tag_input)
        self.pull_btn = QPushButton("Pull")
        self.pull_btn.clicked.connect(self._start_pull)
        row.addWidget(self.pull_btn)
        layout.addLayout(row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)

    def _start_pull(self) -> None:
        tag = self.tag_input.text().strip()
        if not tag:
            QMessageBox.information(self, "Missing tag", "Type a model tag first.")
            return
        if self._thread is not None:
            return
        self.pull_btn.setEnabled(False)
        self.log.append(f"→ pulling {tag} … (can take several minutes)")

        worker = _PullWorker(self._base_url, tag)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Drop our refs only after the thread's event loop has fully exited.
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_progress(self, line: str) -> None:
        self.log.append(line)

    def _on_done(self, ok: bool, message: str) -> None:
        self.log.append(("✓ " if ok else "✗ ") + message)
        self.pull_btn.setEnabled(True)

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None


class SettingsView(QWidget):
    back_requested = Signal()
    model_changed = Signal(str)  # emitted when user saves a new active model
    profile_changed = Signal()   # emitted after avatar upload/clear

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Settings")

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        back = QPushButton("← Back")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addWidget(QLabel("<h1>Settings</h1>"))
        header.addStretch(1)
        layout.addLayout(header)

        layout.addWidget(self._build_profile_panel())
        layout.addWidget(self._build_ai_panel())
        layout.addWidget(self._build_data_panel())
        layout.addWidget(self._build_thresholds_panel())
        layout.addStretch(1)

        self.refresh()

    # ---- Profile panel ---------------------------------------------------

    def _build_profile_panel(self) -> QGroupBox:
        box = QGroupBox("Profile")
        layout = QHBoxLayout(box)

        self.avatar = AvatarLabel(size=84)
        layout.addWidget(self.avatar)

        meta = QVBoxLayout()
        self.profile_name = QLabel("—")
        self.profile_name.setStyleSheet("QLabel { font-weight: 600; font-size: 14px; }")
        self.profile_meta = QLabel("—")
        self.profile_meta.setStyleSheet("QLabel { color: #6B6679; }")
        meta.addWidget(self.profile_name)
        meta.addWidget(self.profile_meta)
        meta.addStretch(1)
        layout.addLayout(meta, 1)

        buttons = QVBoxLayout()
        change = QPushButton("Change photo…")
        change.clicked.connect(self._change_photo)
        remove = QPushButton("Remove photo")
        remove.clicked.connect(self._remove_photo)
        buttons.addWidget(change)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        return box

    def _change_photo(self) -> None:
        cu = get_current()
        if cu is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select profile photo", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        try:
            stored = users_service.set_user_photo(cu.id, Path(path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save photo", str(e))
            return
        self._refresh_current_user(photo_path=stored)
        self._render_profile()
        self.profile_changed.emit()

    def _remove_photo(self) -> None:
        cu = get_current()
        if cu is None:
            return
        users_service.clear_user_photo(cu.id)
        self._refresh_current_user(photo_path=None)
        self._render_profile()
        self.profile_changed.emit()

    def _refresh_current_user(self, *, photo_path: str | None) -> None:
        cu = get_current()
        if cu is None:
            return
        from ..auth.session import CurrentUser
        set_current(CurrentUser(
            id=cu.id, username=cu.username, role=cu.role,
            full_name=cu.full_name, photo_path=photo_path,
        ))

    def _render_profile(self) -> None:
        cu = get_current()
        if cu is None:
            self.profile_name.setText("(not logged in)")
            self.profile_meta.setText("")
            self.avatar.set_photo(None, "", None)
            return
        self.profile_name.setText(cu.full_name or cu.username)
        self.profile_meta.setText(f"{cu.username} · {cu.role}")
        self.avatar.set_photo(cu.photo_path, cu.username, cu.full_name)

    # ---- Data panel ------------------------------------------------------

    def _build_data_panel(self) -> QGroupBox:
        box = QGroupBox("Corrections cache")
        layout = QVBoxLayout(box)
        path = corrections_cache.cache_path()
        label = QLabel(
            "The AI checks this file before calling the model. Copy it between "
            "installs to carry corrections with you.<br>"
            f"<code>{path}</code>"
        )
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(label)
        row = QHBoxLayout()
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        )
        row.addWidget(open_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    # ---- AI panel --------------------------------------------------------

    def _build_ai_panel(self) -> QGroupBox:
        box = QGroupBox("AI management")
        layout = QVBoxLayout(box)

        form = QFormLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("http://127.0.0.1:11434")
        form.addRow("Ollama URL", self.url_input)

        self.status_label = QLabel("—")
        form.addRow("Status", self.status_label)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)  # allow typing an exact tag
        form.addRow("Active model", self.model_combo)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh from Ollama")
        self.refresh_btn.clicked.connect(self.refresh_models)
        buttons.addWidget(self.refresh_btn)
        self.test_btn = QPushButton("Test model")
        self.test_btn.clicked.connect(self._test_model)
        buttons.addWidget(self.test_btn)
        self.pull_btn = QPushButton("Pull new model…")
        self.pull_btn.clicked.connect(self._pull_dialog)
        buttons.addWidget(self.pull_btn)
        buttons.addStretch(1)
        self.save_btn = QPushButton("Save active model")
        self.save_btn.clicked.connect(self._save_model)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        self.test_output = QLabel("")
        self.test_output.setWordWrap(True)
        self.test_output.setStyleSheet("QLabel { color: #555; padding-top: 6px; }")
        layout.addWidget(self.test_output)

        return box

    # ---- Thresholds panel ------------------------------------------------

    def _build_thresholds_panel(self) -> QGroupBox:
        box = QGroupBox("Categorisation thresholds (read-only)")
        form = QFormLayout(box)
        s = get_settings()
        form.addRow("Fuzzy match (auto-accept ≥)", QLabel(str(s.fuzzy_high)))
        form.addRow("Fuzzy match (flag between)", QLabel(f"{s.fuzzy_low} – {s.fuzzy_high}"))
        form.addRow("LLM confidence (flag below)", QLabel(f"{s.llm_confidence_threshold:.2f}"))
        form.addRow("Rule weight to auto-accept", QLabel(str(s.confirm_weight_threshold)))
        form.addRow("Clients to promote to global rule", QLabel(str(s.global_promote_threshold)))
        form.addRow(QLabel(
            "<i>These are currently controlled via the config file / env vars. "
            "Expose as editable here later if you want to tune from the UI.</i>"
        ))
        return box

    # ---- Actions ---------------------------------------------------------

    def refresh(self) -> None:
        s = get_settings()
        saved_url = app_settings.get("ollama_url") or s.ollama_url
        self.url_input.setText(saved_url)
        self._render_profile()
        self.refresh_models()

    def refresh_models(self) -> None:
        url = self.url_input.text().strip() or get_settings().ollama_url
        try:
            client = OllamaClient(base_url=url, model="probe")
            available = client.list_models()
            self.status_label.setText(
                f"<span style='color:#176b1a'>Reachable · {len(available)} model(s) available</span>"
            )
        except LLMError as e:
            available = []
            self.status_label.setText(f"<span style='color:#a52d1e'>Not reachable — {e}</span>")

        current_pref = (
            app_settings.get("ollama_model")
            or get_settings().ollama_model
            or ""
        )
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if current_pref and current_pref not in available:
            self.model_combo.addItem(f"{current_pref}  (not installed)")
        for m in available:
            self.model_combo.addItem(m)
        if current_pref:
            # Select whichever entry starts with the saved pref.
            for i in range(self.model_combo.count()):
                if self.model_combo.itemText(i).startswith(current_pref):
                    self.model_combo.setCurrentIndex(i)
                    break
        self.model_combo.blockSignals(False)

    def _selected_model(self) -> str:
        text = self.model_combo.currentText().strip()
        # Strip the "(not installed)" marker if present.
        if text.endswith("(not installed)"):
            text = text.rsplit("  (", 1)[0]
        return text

    def _save_model(self) -> None:
        model = self._selected_model()
        if not model:
            QMessageBox.information(self, "No model", "Pick or type a model tag first.")
            return
        url = self.url_input.text().strip()
        app_settings.put("ollama_model", model)
        if url:
            app_settings.put("ollama_url", url)
        self.model_changed.emit(model)
        QMessageBox.information(
            self, "Saved",
            f"Active model set to '{model}'. New categorisation runs will use it."
        )

    def _test_model(self) -> None:
        from decimal import Decimal

        model = self._selected_model()
        if not model:
            QMessageBox.information(self, "No model", "Select a model to test first.")
            return
        url = self.url_input.text().strip() or get_settings().ollama_url
        self.test_output.setText("Running a test classification… (this may take a few seconds)")
        try:
            client = OllamaClient(base_url=url, model=model)
            result = client.classify(
                description_raw="TESCO STORES 2345 LONDON",
                merchant_normalized="TESCO STORES",
                amount=Decimal("-34.20"),
                direction="debit",
                posted_date="2025-03-05",
                few_shot=[],
            )
            self.test_output.setText(
                f"<b style='color:#176b1a'>✓ Test OK</b> · "
                f"category: <b>{result.category}</b> · "
                f"group: {result.group} · "
                f"confidence: {result.confidence:.2f}"
                + (f" · <i>{result.reason}</i>" if result.reason else "")
            )
        except LLMError as e:
            logger.warning("Settings test call failed: {}", e)
            self.test_output.setText(
                f"<b style='color:#a52d1e'>✗ Test failed</b> — {e}"
            )

    def _pull_dialog(self) -> None:
        url = self.url_input.text().strip() or get_settings().ollama_url
        dlg = PullModelDialog(url, self)
        dlg.exec()
        self.refresh_models()
