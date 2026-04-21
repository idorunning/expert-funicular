"""About dialog — product name, version, publisher, privacy statement."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from ... import __version__
from ..theme import load_logo_pixmap
from .legal_texts import COMPANY_NAME, COMPANY_WEBSITE, PRODUCT_NAME, PRODUCT_TAGLINE


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setSpacing(10)

        logo_pm = load_logo_pixmap(height=56)
        if not logo_pm.isNull():
            logo_label = QLabel()
            logo_label.setPixmap(logo_pm)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        title = QLabel(f"<h2 style='margin:4px 0'>{PRODUCT_NAME}</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        tagline = QLabel(f"<p style='color:#6B6679;margin:0'>{PRODUCT_TAGLINE}</p>")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tagline)

        version = QLabel(f"<p style='color:#555'>Version {__version__}</p>")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        publisher = QLabel(
            f"<p>© 2026 {COMPANY_NAME}<br>"
            f"<a href='{COMPANY_WEBSITE}'>{COMPANY_WEBSITE}</a></p>"
        )
        publisher.setAlignment(Qt.AlignmentFlag.AlignCenter)
        publisher.setOpenExternalLinks(True)
        publisher.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(publisher)

        privacy = QLabel(
            "<p style='color:#333;margin-top:12px'>"
            "All client data is processed on this device. Nothing leaves the "
            "machine except to the local Ollama model."
            "</p>"
        )
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)
