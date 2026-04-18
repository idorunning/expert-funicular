"""QApplication bootstrap for BrokerLedger."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import paths
from .auth.service import user_count
from .db.engine import init_engine
from .db.seed import run_all_seeds
from .gui.first_run import FirstRunDialog
from .gui.main_window import MainWindow
from .utils.logging import configure_logging, logger


def run() -> int:
    paths.ensure_dirs()
    configure_logging()
    init_engine()
    run_all_seeds()
    logger.info("BrokerLedger starting — data at {}", paths.app_data_dir())

    app = QApplication.instance() or QApplication(sys.argv)

    if user_count() == 0:
        wiz = FirstRunDialog()
        if wiz.exec() != wiz.DialogCode.Accepted:
            return 0

    window = MainWindow()
    window.show()

    # If the first-run wizard already logged in, skip to the clients view.
    from .auth.session import get_current
    if get_current() is not None:
        window._on_logged_in()

    return app.exec()
