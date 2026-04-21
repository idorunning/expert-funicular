"""Headless GUI smoke test. Skipped automatically if Qt can't initialise."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _try_qapp():
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401
    except Exception:  # noqa: BLE001
        return None
    try:
        app = QApplication.instance()
        if app is None:
            import sys
            app = QApplication(sys.argv)
        return app
    except Exception:  # noqa: BLE001
        return None


def test_main_window_boots_and_routes(logged_in_admin):
    app = _try_qapp()
    if app is None:
        pytest.skip("Qt platform unavailable in this environment")
    from brokerledger.clients.service import create_client
    from brokerledger.gui.main_window import MainWindow

    c = create_client("GUI Client")
    w = MainWindow()
    w._on_logged_in()
    assert w.stack.currentWidget().__class__.__name__ == "ClientsView"
    w._open_client_detail(c.id, c.display_name)
    assert w.stack.currentWidget().__class__.__name__ == "ClientDetailView"
    w._open_review(c.id, c.display_name)
    assert w.stack.currentWidget().__class__.__name__ == "ReviewView"
    app.processEvents()
