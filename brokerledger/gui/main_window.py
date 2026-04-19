"""Main application window + view router."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QStackedWidget

from ..auth.service import logout
from .admin_users_view import AdminUsersView
from .client_detail_view import ClientDetailView
from .clients_view import ClientsView
from .login_view import LoginView
from .review_view import ReviewView
from .settings_view import SettingsView


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BrokerLedger")
        self.resize(1200, 780)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.login = LoginView()
        self.clients = ClientsView()
        self.admin = AdminUsersView()
        self.settings = SettingsView()

        self.stack.addWidget(self.login)    # 0
        self.stack.addWidget(self.clients)  # 1
        self.stack.addWidget(self.admin)    # 2
        self.stack.addWidget(self.settings) # 3

        self.login.logged_in.connect(self._on_logged_in)
        self.clients.logout_requested.connect(self._on_logout)
        self.clients.admin_requested.connect(self._open_admin)
        self.clients.settings_requested.connect(self._open_settings)
        self.clients.open_client.connect(self._open_client_detail)
        self.admin.back_requested.connect(self._show_clients)
        self.settings.back_requested.connect(self._show_clients)

        # Transient detail/review views created on demand.
        self._detail: ClientDetailView | None = None
        self._review: ReviewView | None = None

        self.stack.setCurrentWidget(self.login)
        self.login.focus_default()

    def _on_logged_in(self) -> None:
        self.clients.refresh()
        self.stack.setCurrentWidget(self.clients)

    def _on_logout(self) -> None:
        logout()
        self.stack.setCurrentWidget(self.login)
        self.login.focus_default()

    def _open_admin(self) -> None:
        self.admin.refresh()
        self.stack.setCurrentWidget(self.admin)

    def _open_settings(self) -> None:
        self.settings.refresh()
        self.stack.setCurrentWidget(self.settings)

    def _show_clients(self) -> None:
        self.clients.refresh()
        self.stack.setCurrentWidget(self.clients)

    def _open_client_detail(self, client_id: int, name: str) -> None:
        if self._detail is not None:
            self.stack.removeWidget(self._detail)
            self._detail.deleteLater()
        self._detail = ClientDetailView(client_id, name)
        self._detail.back_requested.connect(self._show_clients)
        self._detail.review_requested.connect(lambda: self._open_review(client_id, name))
        self.stack.addWidget(self._detail)
        self.stack.setCurrentWidget(self._detail)

    def _open_review(self, client_id: int, name: str) -> None:
        if self._review is not None:
            self.stack.removeWidget(self._review)
            self._review.deleteLater()
        self._review = ReviewView(client_id, name)
        self._review.back_requested.connect(lambda: self._open_client_detail(client_id, name))
        self._review.export_requested.connect(
            lambda: (self._open_client_detail(client_id, name),
                     self._detail._export() if self._detail else None)  # type: ignore[func-returns-value]
        )
        self._review.affordability_requested.connect(lambda: self._open_client_detail(client_id, name))
        self.stack.addWidget(self._review)
        self.stack.setCurrentWidget(self._review)
        self._review.setFocus(Qt.FocusReason.OtherFocusReason)
