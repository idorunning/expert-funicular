"""Legal dialog — Privacy Policy, EULA, and Data Processing Agreement tabs."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)

from .legal_texts import DPA, EULA, PRIVACY_POLICY


def _doc_tab(html: str) -> QTextBrowser:
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setHtml(html)
    return browser


class LegalDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Privacy Policy, EULA, and Data Processing")
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        tabs = QTabWidget()
        tabs.addTab(_doc_tab(PRIVACY_POLICY), "Privacy Policy")
        tabs.addTab(_doc_tab(EULA), "End-User Licence Agreement")
        tabs.addTab(_doc_tab(DPA), "Data Processing Agreement")
        layout.addWidget(tabs)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)
