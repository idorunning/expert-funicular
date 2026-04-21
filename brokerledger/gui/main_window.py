"""Main application window + view router."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..auth.service import logout
from ..auth.session import get_current
from .admin_users_view import AdminUsersView
from .audit_log_view import AuditLogView
from .client_detail_view import ClientDetailView
from .clients_view import ClientsView
from .login_view import LoginView
from .review_view import ReviewView
from .settings_view import SettingsView
from .theme import load_logo_pixmap
from .training_view import TrainingView
from .widgets.avatar import AvatarLabel


class _BrandHeader(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("BrandHeader")
        self.setFixedHeight(72)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 12, 20, 12)
        row.setSpacing(14)

        self.logo = QLabel()
        pm = load_logo_pixmap(height=44)
        if not pm.isNull():
            self.logo.setPixmap(pm)
        else:
            self.logo.setText("")
        row.addWidget(self.logo)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        title = QLabel("Mortgage Broker Affordability Assistant")
        title.setObjectName("BrandTitle")
        subtitle = QLabel("AI powered · Fully local · Fully secure")
        subtitle.setObjectName("BrandSubtitle")
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        row.addLayout(text_col)
        row.addStretch(1)

        self.user_label = QLabel("")
        self.user_label.setObjectName("BrandUserLabel")
        self.user_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.user_label)

        self.avatar = AvatarLabel(size=40)
        self.avatar.setVisible(False)
        row.addWidget(self.avatar)

    def refresh_user(self) -> None:
        from ..auth.session import get_current
        cu = get_current()
        if cu is None:
            self.user_label.setText("")
            self.avatar.setVisible(False)
            return
        name = cu.full_name or cu.username
        self.user_label.setText(f"<span style='color:#FFFFFF'>{name}</span>")
        self.avatar.set_photo(cu.photo_path, cu.username, cu.full_name)
        self.avatar.setVisible(True)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mortgage Broker Affordability Assistant")
        self.resize(1280, 820)
        self.setMinimumSize(1024, 640)

        container = QWidget()
        container.setObjectName("centralWidget")
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = _BrandHeader()
        root.addWidget(self.header)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(container)

        self.login = LoginView()
        self.clients = ClientsView()
        self.admin = AdminUsersView()
        self.settings = SettingsView()
        self.audit_log = AuditLogView()
        self.training = TrainingView()

        self.stack.addWidget(self.login)      # 0
        self.stack.addWidget(self.clients)    # 1
        self.stack.addWidget(self.admin)      # 2
        self.stack.addWidget(self.settings)   # 3
        self.stack.addWidget(self.audit_log)  # 4
        self.stack.addWidget(self.training)   # 5

        self.login.logged_in.connect(self._on_logged_in)
        self.clients.logout_requested.connect(self._on_logout)
        self.clients.admin_requested.connect(self._open_admin)
        self.clients.settings_requested.connect(self._open_settings)
        self.clients.audit_log_requested.connect(self._open_audit_log)
        self.clients.training_requested.connect(self._open_training)
        self.clients.open_client.connect(self._open_client_detail)
        self.admin.back_requested.connect(self._show_clients)
        self.settings.back_requested.connect(self._show_clients)
        self.audit_log.back_requested.connect(self._show_clients)
        self.training.back_requested.connect(self._show_clients)
        self.settings.profile_changed.connect(self.header.refresh_user)

        # Cache detail views per client so their worker threads survive
        # navigation — destroying a view with a running QThread crashes Qt.
        self._details: dict[int, ClientDetailView] = {}
        self._review: ReviewView | None = None
        self._review_client_id: int | None = None

        # Status bar surfaces background activity while the user works elsewhere.
        self.statusBar().setSizeGripEnabled(False)

        self.stack.setCurrentWidget(self.login)
        self.login.focus_default()

    def _on_logged_in(self) -> None:
        self.header.refresh_user()
        self.clients.refresh()
        self.stack.setCurrentWidget(self.clients)

    def _on_logout(self) -> None:
        busy = [v for v in self._details.values() if v.is_processing()]
        if busy:
            QMessageBox.information(
                self, "Background work still running",
                "Statements are still being processed in the background. "
                "Please wait for that to finish before logging out."
            )
            return
        logout()
        self.header.refresh_user()
        self.stack.setCurrentWidget(self.login)
        self.login.focus_default()

    def _open_admin(self) -> None:
        self.admin.refresh()
        self.stack.setCurrentWidget(self.admin)

    def _open_settings(self) -> None:
        self.settings.refresh()
        self.stack.setCurrentWidget(self.settings)

    def _open_audit_log(self) -> None:
        try:
            self.audit_log.refresh()
        except PermissionError:
            QMessageBox.warning(
                self, "Admins only",
                "Only administrators can view the audit log.",
            )
            return
        self.stack.setCurrentWidget(self.audit_log)

    def _open_training(self) -> None:
        self.training.refresh()
        self.stack.setCurrentWidget(self.training)

    def _show_clients(self) -> None:
        self.clients.refresh()
        self.stack.setCurrentWidget(self.clients)

    def _open_client_detail(self, client_id: int, name: str) -> None:
        detail = self._details.get(client_id)
        if detail is None:
            detail = ClientDetailView(client_id, name)
            detail.back_requested.connect(self._show_clients)
            detail.review_requested.connect(
                lambda cid=client_id, n=name: self._open_review(cid, n)
            )
            detail.review_flagged_requested.connect(
                lambda count, cid=client_id, n=name: self._open_review(
                    cid, n, flagged_only=True, flagged_count=count
                )
            )
            detail.processing_changed.connect(self._update_busy_indicator)
            self.stack.addWidget(detail)
            self._details[client_id] = detail
        else:
            # Pick up any changes made via Review or other background work.
            detail.refresh()
        self.stack.setCurrentWidget(detail)

    def _open_review(
        self,
        client_id: int,
        name: str,
        *,
        flagged_only: bool = False,
        flagged_count: int | None = None,
    ) -> None:
        if self._review is not None:
            self.stack.removeWidget(self._review)
            self._review.deleteLater()
        self._review = ReviewView(
            client_id, name,
            flagged_only=flagged_only,
            flagged_count=flagged_count,
        )
        self._review_client_id = client_id
        self._review.back_requested.connect(
            lambda cid=client_id, n=name: self._open_client_detail(cid, n)
        )
        self._review.export_requested.connect(
            lambda cid=client_id, n=name: self._export_via_detail(cid, n)
        )
        self._review.affordability_requested.connect(
            lambda cid=client_id, n=name: self._open_client_detail(cid, n)
        )
        # Live updates while an ingest/recategorize run is still in flight.
        detail = self._details.get(client_id)
        if detail is not None:
            detail.tx_persisted.connect(self._review.model.on_tx_persisted)
        self.stack.addWidget(self._review)
        self.stack.setCurrentWidget(self._review)
        self._review.setFocus(Qt.FocusReason.OtherFocusReason)

    def _export_via_detail(self, client_id: int, name: str) -> None:
        self._open_client_detail(client_id, name)
        detail = self._details.get(client_id)
        if detail is not None:
            detail._export()

    def _update_busy_indicator(self, _state: bool = False) -> None:
        busy_ids: set[int] = {
            cid for cid, v in self._details.items() if v.is_processing()
        }
        active_names = [
            v.client_name for cid, v in self._details.items() if cid in busy_ids
        ]
        self.clients.set_processing_clients(busy_ids)
        if not active_names:
            self.statusBar().clearMessage()
            return
        if len(active_names) == 1:
            self.statusBar().showMessage(f"⏳ Background processing: {active_names[0]}")
        else:
            self.statusBar().showMessage(
                f"⏳ Background processing for {len(active_names)} clients"
            )
