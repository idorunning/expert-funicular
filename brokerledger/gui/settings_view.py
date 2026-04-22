"""Settings view — AI model, category confidence, profile, and data management."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..auth.session import get_current, set_current
from ..categorize import corrections_cache
from ..categorize.llm_client import LLMError, OllamaClient
from ..categorize.model_catalog import (
    DEFAULT_LEVEL,
    STRICTNESS_COLORS,
    STRICTNESS_LABELS,
    STRICTNESS_LEVELS,
    recommended_level_for_model,
    thresholds_for_level,
)
from ..config import (
    DEFAULT_STRICTNESS_LEVEL,
    STRICTNESS_KEY,
    get_settings,
    get_strictness_level,
    set_strictness_level,
)
from ..db import app_settings
from ..users import service as users_service
from ..utils.logging import logger
from .widgets.avatar import AvatarLabel


# ---- Pull-model worker ---------------------------------------------------

class _PullWorker(QObject):
    progress = Signal(str)
    done = Signal(bool, str)

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
        self.setWindowTitle("Download a model")
        self.resize(520, 360)
        self._base_url = base_url
        self._thread: QThread | None = None
        self._worker: _PullWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Enter the model tag exactly as it appears on <b>ollama.com/library</b> "
            "(e.g. <code>gemma3:4b</code>, <code>llama3.1:8b</code>)."
        ))
        row = QHBoxLayout()
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("gemma3:4b")
        row.addWidget(self.tag_input)
        self.pull_btn = QPushButton("Download")
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
        self.log.append(f"→ downloading {tag} … (can take several minutes)")

        worker = _PullWorker(self._base_url, tag)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
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


# ---- Colour swatch -------------------------------------------------------

class _ColourSwatch(QWidget):
    """Small filled circle showing the current strictness colour."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._color = QColor("#D4A017")

    def set_color(self, hex_color: str) -> None:
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, self.width(), self.height())


# ---- Main settings widget ------------------------------------------------

class SettingsView(QWidget):
    back_requested = Signal()
    model_changed = Signal(str)
    profile_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Settings")

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
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        back = QPushButton("← Back")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        title = QLabel("Settings")
        title.setStyleSheet("QLabel { font-size: 22px; font-weight: 600; color: #1F1030; }")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        layout.addWidget(self._build_profile_panel())
        layout.addWidget(self._build_password_panel())
        layout.addWidget(self._build_ai_panel())
        layout.addWidget(self._build_web_lookup_panel())
        layout.addWidget(self._build_thresholds_panel())
        layout.addWidget(self._build_category_register_panel())
        layout.addWidget(self._build_legal_panel())
        layout.addStretch(1)

        self.refresh()

    # ---- Profile ---------------------------------------------------------

    def _build_profile_panel(self) -> QGroupBox:
        box = QGroupBox("Profile")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(14)

        self.avatar = AvatarLabel(size=84)
        layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)

        meta = QVBoxLayout()
        meta.setSpacing(4)
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

    # ---- Change my password ---------------------------------------------

    def _build_password_panel(self) -> QGroupBox:
        from .widgets.password_field import PasswordField, PasswordPair
        box = QGroupBox("Change my password")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(8)

        intro = QLabel(
            "Update the password you use to log in.  You'll be signed out on "
            "other devices the next time you log in there."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel { color: #6B6679; }")
        layout.addWidget(intro)

        old_lbl = QLabel("Current password")
        layout.addWidget(old_lbl)
        self._pw_old = PasswordField(placeholder="Current password")
        layout.addWidget(self._pw_old)

        self._pw_new = PasswordPair(
            label_new="New password", label_confirm="Confirm new password",
        )
        layout.addWidget(self._pw_new)

        row = QHBoxLayout()
        row.addStretch(1)
        save_btn = QPushButton("Update password")
        save_btn.clicked.connect(self._change_own_password)
        row.addWidget(save_btn)
        layout.addLayout(row)

        return box

    def _change_own_password(self) -> None:
        from ..auth.service import AuthError, InvalidCredentials, change_own_password
        old = self._pw_old.text()
        if not old:
            QMessageBox.warning(self, "Current password", "Please enter your current password.")
            return
        ok, err = self._pw_new.is_valid(
            min_length=get_settings().password_min_length, required=True,
        )
        if not ok:
            QMessageBox.warning(self, "New password", err)
            return
        try:
            change_own_password(old, self._pw_new.value())
        except InvalidCredentials as e:
            QMessageBox.warning(self, "Current password incorrect", str(e))
            return
        except AuthError as e:
            QMessageBox.warning(self, "Could not update password", str(e))
            return
        self._pw_old.clear()
        self._pw_new.clear()
        QMessageBox.information(self, "Password updated", "Your password has been changed.")

    # ---- AI management ---------------------------------------------------

    def _build_ai_panel(self) -> QGroupBox:
        box = QGroupBox("AI model")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(10)

        # Status + refresh
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_label = QLabel("Checking…")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        check_btn = QPushButton("Refresh")
        check_btn.setToolTip("Ask Ollama which models are installed right now.")
        check_btn.clicked.connect(self.refresh_models)
        status_row.addWidget(check_btn)
        layout.addLayout(status_row)

        # Model list — just names, no descriptions.
        from PySide6.QtWidgets import QComboBox
        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        self.model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.model_combo.setMinimumWidth(280)
        layout.addWidget(self.model_combo)

        # Three action buttons in a single row.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.default_btn = QPushButton("Default model")
        self.default_btn.setToolTip(
            "Use the selected model for every categorisation run."
        )
        self.default_btn.clicked.connect(self._save_model)
        btn_row.addWidget(self.default_btn)

        download_btn = QPushButton("Download new model")
        download_btn.setToolTip("Download a model from Ollama.")
        download_btn.clicked.connect(self._pull_dialog)
        btn_row.addWidget(download_btn)

        self.remove_btn = QPushButton("Remove model")
        self.remove_btn.setToolTip(
            "Permanently delete the selected model from this machine."
        )
        self.remove_btn.clicked.connect(self._remove_model)
        btn_row.addWidget(self.remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return box

    def refresh_models(self) -> None:
        url = get_settings().ollama_url
        saved_url = app_settings.get("ollama_url")
        if saved_url:
            url = saved_url
        try:
            client = OllamaClient(base_url=url, model="probe")
            available = client.list_models()
            self.status_label.setText(
                f"<span style='color:#176b1a'>Ollama running — "
                f"{len(available)} model(s) installed</span>"
            )
        except LLMError as e:
            available = []
            self.status_label.setText(
                f"<span style='color:#a52d1e'>Ollama not found — {e}</span>"
            )

        current_pref = app_settings.get("ollama_model") or get_settings().ollama_model or ""

        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if current_pref and current_pref not in available:
            self.model_combo.addItem(f"{current_pref}  (not installed)", current_pref)
        for m in available:
            self.model_combo.addItem(m, m)
        if current_pref:
            for i in range(self.model_combo.count()):
                if (self.model_combo.itemData(i) or "").startswith(current_pref):
                    self.model_combo.setCurrentIndex(i)
                    break
        self.model_combo.blockSignals(False)

    def _selected_model(self) -> str:
        data = self.model_combo.currentData()
        if data:
            return str(data)
        text = self.model_combo.currentText().strip()
        if "  (not installed)" in text:
            text = text.split("  (not installed)")[0]
        return text

    def _save_model(self) -> None:
        model = self._selected_model()
        if not model:
            QMessageBox.information(self, "No model selected", "Pick a model first.")
            return
        app_settings.put("ollama_model", model)
        # Suggest updating strictness to match the model recommendation.
        rec = recommended_level_for_model(model)
        cur = get_strictness_level()
        self.model_changed.emit(model)
        if rec != cur:
            self._suggest_strictness_update(model, rec)
        else:
            QMessageBox.information(
                self, "Default model set",
                f"'{model}' is now the default model.",
            )

    def _remove_model(self) -> None:
        model = self._selected_model()
        if not model:
            QMessageBox.information(self, "No model selected", "Pick a model to remove first.")
            return
        current_default = app_settings.get("ollama_model") or get_settings().ollama_model or ""
        warn = ""
        if model == current_default:
            warn = (
                "\n\nThis is currently the default model. After removing it, "
                "pick another installed model as the default."
            )
        reply = QMessageBox.question(
            self,
            "Remove model?",
            f"Permanently delete '{model}' from this machine?{warn}\n\n"
            "You can download it again later if needed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        url = app_settings.get("ollama_url") or get_settings().ollama_url
        try:
            r = httpx.request(
                "DELETE",
                f"{url.rstrip('/')}/api/delete",
                json={"name": model},
                timeout=30.0,
            )
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        except (httpx.HTTPError, LLMError) as e:
            logger.warning("Remove model failed: {}", e)
            QMessageBox.critical(
                self, "Remove failed",
                f"Could not remove '{model}':\n\n{e}",
            )
            return
        if model == current_default:
            app_settings.delete("ollama_model")
        self.refresh_models()
        QMessageBox.information(
            self, "Model removed", f"'{model}' was removed from this machine."
        )

    def _suggest_strictness_update(self, model: str, rec: int) -> None:
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Update confidence level?",
            f"The recommended confidence level for '{model}' is "
            f"{rec} ({STRICTNESS_LABELS[rec]}) — your current setting is "
            f"{get_strictness_level()}.\n\nSwitch to the recommended level?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            set_strictness_level(rec)
            self._load_strictness()

    def _pull_dialog(self) -> None:
        url = app_settings.get("ollama_url") or get_settings().ollama_url
        dlg = PullModelDialog(url, self)
        dlg.exec()
        self.refresh_models()

    # ---- Merchant web lookup (opt-in, red warning) -----------------------

    def _build_web_lookup_panel(self) -> QGroupBox:
        from ..categorize import web_lookup

        box = QGroupBox("Merchant web lookup")
        box.setStyleSheet(
            "QGroupBox {"
            "  border: 2px solid #A52D1E;"
            "  border-radius: 8px;"
            "  background: #FFF5F3;"
            "  margin-top: 12px;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin;"
            "  left: 10px;"
            "  padding: 0 6px;"
            "  color: #A52D1E;"
            "  font-weight: 700;"
            "}"
        )
        outer = QVBoxLayout(box)
        outer.setContentsMargins(12, 18, 12, 12)
        outer.setSpacing(8)

        warning = QLabel(
            "<b style='color:#A52D1E'>⚠ Warning — this feature sends data "
            "over the internet.</b><br><br>"
            "When enabled, the AI will look up <b>only the merchant name</b> "
            "(e.g. <i>TESCO METRO</i>) on DuckDuckGo to help decide the "
            "category. No other information is ever sent:<br>"
            "&nbsp;&nbsp;• <b>not</b> your client's name, account number, or IBAN<br>"
            "&nbsp;&nbsp;• <b>not</b> the amount, date, direction, or balance<br>"
            "&nbsp;&nbsp;• <b>not</b> the full description line<br><br>"
            "Only switch this on if you are comfortable with short merchant "
            "names leaving your machine. <b>Off is the default.</b>"
        )
        warning.setWordWrap(True)
        warning.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(warning)

        self.web_lookup_toggle = QCheckBox(
            "Enable merchant web lookup (OFF by default)"
        )
        self.web_lookup_toggle.setChecked(web_lookup.is_enabled())
        self.web_lookup_toggle.toggled.connect(self._on_web_lookup_toggled)
        outer.addWidget(self.web_lookup_toggle)
        return box

    def _on_web_lookup_toggled(self, checked: bool) -> None:
        from ..categorize import web_lookup

        web_lookup.set_enabled(checked)

    # ---- Category confidence (1-5 strictness) ----------------------------

    def _build_thresholds_panel(self) -> QGroupBox:
        box = QGroupBox("Category confidence settings")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(12, 18, 12, 12)
        outer.setSpacing(10)

        intro = QLabel(
            "Control how much you trust the AI's category suggestions. "
            "Move the slider left to review more transactions yourself; "
            "right to let the AI decide more automatically."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel { color: #6B6679; }")
        outer.addWidget(intro)

        # Red–green gradient bar + slider
        gradient_row = QHBoxLayout()
        gradient_row.setSpacing(8)

        red_lbl = QLabel("Review more")
        red_lbl.setStyleSheet("color: #C0392B; font-size: 11px; font-weight: 600;")
        gradient_row.addWidget(red_lbl)

        slider_col = QVBoxLayout()
        slider_col.setSpacing(2)
        self.strictness_slider = QSlider(Qt.Orientation.Horizontal)
        self.strictness_slider.setMinimum(1)
        self.strictness_slider.setMaximum(5)
        self.strictness_slider.setSingleStep(1)
        self.strictness_slider.setPageStep(1)
        self.strictness_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.strictness_slider.setTickInterval(1)
        self.strictness_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #C0392B, stop:0.25 #E67E22,
                    stop:0.5 #D4A017, stop:0.75 #7FB069, stop:1 #27AE60);
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: white;
                border: 2px solid #4A1766;
                width: 18px; height: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal { background: transparent; }
            QSlider::add-page:horizontal { background: transparent; }
        """)
        self.strictness_slider.valueChanged.connect(self._on_strictness_changed)
        slider_col.addWidget(self.strictness_slider)

        # Tick labels 1–5
        ticks_row = QHBoxLayout()
        ticks_row.setContentsMargins(0, 0, 0, 0)
        tick_colors = [STRICTNESS_COLORS[i] for i in range(1, 6)]
        tick_labels = ["1", "2", "3", "4", "5"]
        for col, txt in zip(tick_colors, tick_labels):
            lbl = QLabel(txt)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {col}; font-size: 11px; font-weight: 700;")
            ticks_row.addWidget(lbl, 1)
        slider_col.addLayout(ticks_row)
        gradient_row.addLayout(slider_col, 1)

        green_lbl = QLabel("Trust AI more")
        green_lbl.setStyleSheet("color: #27AE60; font-size: 11px; font-weight: 600;")
        gradient_row.addWidget(green_lbl)
        outer.addLayout(gradient_row)

        # Swatch + description of current level
        desc_row = QHBoxLayout()
        desc_row.setSpacing(8)
        self._swatch = _ColourSwatch()
        desc_row.addWidget(self._swatch)
        self.strictness_desc = QLabel("")
        self.strictness_desc.setWordWrap(True)
        self.strictness_desc.setStyleSheet("QLabel { color: #1F1030; font-weight: 600; }")
        desc_row.addWidget(self.strictness_desc, 1)
        outer.addLayout(desc_row)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to default")
        reset_btn.setToolTip("Reset to the Balanced (level 3) setting.")
        reset_btn.clicked.connect(self._reset_strictness)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        save_btn = QPushButton("Save setting")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self._save_strictness)
        btn_row.addWidget(save_btn)
        outer.addLayout(btn_row)

        self._load_strictness()
        return box

    def _on_strictness_changed(self, value: int) -> None:
        color = STRICTNESS_COLORS.get(value, "#D4A017")
        self._swatch.set_color(color)
        self.strictness_desc.setText(
            f"Level {value} — {STRICTNESS_LABELS.get(value, '')}"
        )
        self.strictness_desc.setStyleSheet(
            f"QLabel {{ color: {color}; font-weight: 600; }}"
        )

    def _load_strictness(self) -> None:
        level = get_strictness_level()
        self.strictness_slider.blockSignals(True)
        self.strictness_slider.setValue(level)
        self.strictness_slider.blockSignals(False)
        self._on_strictness_changed(level)

    def _save_strictness(self) -> None:
        level = self.strictness_slider.value()
        set_strictness_level(level)
        QMessageBox.information(
            self, "Saved",
            f"Confidence level set to {level} — {STRICTNESS_LABELS[level]}\n\n"
            "The next categorisation run will use this setting.",
        )

    def _reset_strictness(self) -> None:
        app_settings.delete(STRICTNESS_KEY)
        self._load_strictness()
        QMessageBox.information(self, "Reset", "Confidence level reset to Balanced (level 3).")

    # ---- Category Register -----------------------------------------------

    def _build_category_register_panel(self) -> QGroupBox:
        box = QGroupBox("Category Register")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(12, 18, 12, 12)
        outer.setSpacing(8)

        intro = QLabel(
            "The Category Register remembers every correction you make. "
            "It is an important file — back it up regularly and restore it "
            "if you ever move to a new machine."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel { color: #6B6679; }")
        outer.addWidget(intro)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open folder")
        open_btn.setToolTip("Open the folder containing the Category Register file.")
        path = corrections_cache.cache_path()
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        )
        btn_row.addWidget(open_btn)

        backup_btn = QPushButton("Back up…")
        backup_btn.setToolTip("Save a backup copy of the Category Register.")
        backup_btn.clicked.connect(self._backup_register)
        btn_row.addWidget(backup_btn)

        restore_btn = QPushButton("Restore from backup…")
        restore_btn.setToolTip(
            "Replace the active Category Register with a previously saved backup."
        )
        restore_btn.clicked.connect(self._restore_register)
        btn_row.addWidget(restore_btn)

        btn_row.addStretch(1)
        outer.addLayout(btn_row)
        return box

    def _backup_register(self) -> None:
        from pathlib import Path
        stamp = datetime.now().strftime("%Y-%m-%d")
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save Category Register backup",
            str(Path.home() / f"category-register-{stamp}.json"),
            "JSON files (*.json)",
        )
        if not dest:
            return
        try:
            written = corrections_cache.backup_to(Path(dest))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Backup failed", str(e))
            return
        QMessageBox.information(self, "Backup saved", f"Saved to:\n{written}")

    def _restore_register(self) -> None:
        from pathlib import Path
        src, _ = QFileDialog.getOpenFileName(
            self, "Restore Category Register backup", "", "JSON files (*.json)"
        )
        if not src:
            return
        reply = QMessageBox.question(
            self,
            "Replace Category Register?",
            "This will replace your current Category Register with the backup.\n\n"
            "Any corrections made after the backup was taken will be lost.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            count = corrections_cache.restore_from(Path(src))
        except ValueError as e:
            QMessageBox.critical(self, "Restore failed", str(e))
            return
        QMessageBox.information(
            self, "Register restored",
            f"Restored {count} correction(s) from backup.\n\n"
            "Restart the application for the restored rules to take effect.",
        )

    # ---- Legal & About ---------------------------------------------------

    def _build_legal_panel(self) -> QGroupBox:
        from .dialogs.about_dialog import AboutDialog
        from .dialogs.legal_dialog import LegalDialog
        from .dialogs.legal_texts import PRODUCT_NAME

        box = QGroupBox("Legal & About")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(12, 18, 12, 12)
        outer.setSpacing(6)

        intro = QLabel(
            "View product information, Privacy Policy, Licence Agreement, "
            "and Data Processing Agreement."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel { color: #6B6679; }")
        outer.addWidget(intro)

        row = QHBoxLayout()
        about_btn = QPushButton(f"About {PRODUCT_NAME}")
        about_btn.clicked.connect(lambda: AboutDialog(self).exec())
        row.addWidget(about_btn)
        legal_btn = QPushButton("View Privacy Policy, EULA, and DPA")
        legal_btn.clicked.connect(lambda: LegalDialog(self).exec())
        row.addWidget(legal_btn)
        row.addStretch(1)
        outer.addLayout(row)
        return box

    # ---- Refresh ---------------------------------------------------------

    def refresh(self) -> None:
        self._render_profile()
        self.refresh_models()
        self._load_strictness()
