"""Admin-only audit log viewer with date / user / action filters."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select

from ..auth.session import require_admin
from ..db.engine import session_scope
from ..db.models import AuditLog, User
from ..users.service import list_audit_actions, list_audit_users


_DEFAULT_LIMIT = 500

_ACTION_LABELS: dict[str, str] = {
    "login": "Logged in",
    "logout": "Logged out",
    "account_locked": "Account locked (too many failed attempts)",
    "create_user": "New user created",
    "update_user": "User details updated",
    "delete_user": "User deleted",
    "set_active": "User enabled / disabled",
    "change_password": "Password changed",
    "password_reset_requested": "Password reset requested",
    "password_reset_dismissed": "Password reset request dismissed",
    "create_client": "New client added",
    "rename_client": "Client renamed",
    "archive_client": "Client archived",
    "restore_client": "Client restored",
    "delete_client": "Client deleted",
    "reassign_client": "Client assigned to different user",
    "import_statement": "Bank statement imported",
    "verify_statement": "Statement marked as verified",
    "export_client": "Client data exported",
    "correct_category": "Transaction category corrected",
}


def _action_label(raw: str) -> str:
    return _ACTION_LABELS.get(raw, raw.replace("_", " ").capitalize())


def _format_detail(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(parsed, dict):
        return json.dumps(parsed)
    # Translate detail keys into readable phrases.
    parts: list[str] = []
    for k, v in parsed.items():
        if k == "merchant" and "to" in parsed:
            continue  # handled below with "to"
        if k == "to" and "merchant" in parsed:
            parts.append(f'"{parsed["merchant"]}" → {v}')
        elif k == "from" and "to" in parsed:
            parts.append(f"reassigned from user #{v}")
        elif k == "to" and "from" in parsed:
            parts.append(f"to user #{v}")
        elif k in ("request_id", "email"):
            pass  # omit internal IDs from display
        elif k == "client_id":
            parts.append(f"client #{v}")
        elif k == "old_name":
            parts.append(f'renamed from "{v}"')
        elif k == "new_name":
            parts.append(f'to "{v}"')
        else:
            parts.append(f"{k}: {v}")
    return "  ".join(parts)


class AuditLogView(QWidget):
    """Filterable read-only view onto the ``audit_log`` table.

    Layout mirrors :class:`AdminUsersView` so admins see a consistent chrome.
    Access is gated via :func:`require_admin` on every refresh.
    """

    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        back = QPushButton("← Back")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addSpacing(12)
        header.addWidget(QLabel("<h1>Audit log</h1>"))
        header.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        outer.addLayout(header)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(QLabel("From:"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_from.dateChanged.connect(lambda _d: self.refresh())
        filters.addWidget(self.date_from)

        filters.addWidget(QLabel("to:"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setDate(QDate.currentDate())
        self.date_to.dateChanged.connect(lambda _d: self.refresh())
        filters.addWidget(self.date_to)

        filters.addSpacing(16)
        filters.addWidget(QLabel("User:"))
        self.user_combo = QComboBox()
        self.user_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        filters.addWidget(self.user_combo)

        filters.addSpacing(8)
        filters.addWidget(QLabel("Action:"))
        self.action_combo = QComboBox()
        self.action_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        filters.addWidget(self.action_combo)

        filters.addStretch(1)
        outer.addLayout(filters)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["When", "User", "What happened", "Record", "Details"]
        )
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        outer.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6B6679;")
        outer.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _populate_filters(self) -> None:
        current_user = self.user_combo.currentData()
        current_action = self.action_combo.currentData()

        self.user_combo.blockSignals(True)
        self.user_combo.clear()
        self.user_combo.addItem("All users", None)
        for u in list_audit_users():
            label = u.full_name or u.username
            self.user_combo.addItem(f"{label} ({u.username})", u.id)
        if current_user is not None:
            idx = self.user_combo.findData(current_user)
            if idx >= 0:
                self.user_combo.setCurrentIndex(idx)
        self.user_combo.blockSignals(False)

        self.action_combo.blockSignals(True)
        self.action_combo.clear()
        self.action_combo.addItem("All actions", None)
        for action in list_audit_actions():
            self.action_combo.addItem(_action_label(action), action)
        if current_action is not None:
            idx = self.action_combo.findData(current_action)
            if idx >= 0:
                self.action_combo.setCurrentIndex(idx)
        self.action_combo.blockSignals(False)

    def refresh(self) -> None:
        require_admin()
        self._populate_filters()

        start_date = self.date_from.date().toPython()
        end_date = self.date_to.date().toPython()
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)

        user_id = self.user_combo.currentData()
        action = self.action_combo.currentData()

        with session_scope() as s:
            q = (
                select(AuditLog, User.username)
                .join(User, User.id == AuditLog.user_id, isouter=True)
                .where(AuditLog.at >= start_dt, AuditLog.at <= end_dt)
                .order_by(AuditLog.at.desc())
                .limit(_DEFAULT_LIMIT)
            )
            if user_id is not None:
                q = q.where(AuditLog.user_id == user_id)
            if action is not None:
                q = q.where(AuditLog.action == action)
            rows = s.execute(q).all()

        self.table.setRowCount(len(rows))
        for idx, (entry, username) in enumerate(rows):
            when = entry.at.strftime("%d %b %Y  %H:%M") if entry.at else ""
            entity = ""
            if entry.entity_type:
                label = {
                    "user": "User",
                    "client": "Client",
                    "statement": "Statement",
                    "transaction": "Transaction",
                    "password_reset": "Reset request",
                }.get(entry.entity_type, entry.entity_type.capitalize())
                if entry.entity_id is not None:
                    entity = f"{label} #{entry.entity_id}"
                else:
                    entity = label
            self.table.setItem(idx, 0, QTableWidgetItem(when))
            self.table.setItem(idx, 1, QTableWidgetItem(username or "—"))
            self.table.setItem(idx, 2, QTableWidgetItem(_action_label(entry.action)))
            self.table.setItem(idx, 3, QTableWidgetItem(entity))
            self.table.setItem(idx, 4, QTableWidgetItem(_format_detail(entry.detail_json)))

        if len(rows) >= _DEFAULT_LIMIT:
            self.status_label.setText(
                f"Showing the most recent {_DEFAULT_LIMIT} entries. Narrow the date range or filters to see older events."
            )
        else:
            self.status_label.setText(f"{len(rows)} event(s) in this window.")
