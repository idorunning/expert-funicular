"""Brand theme — Mortgage Oasis purple → magenta palette.

The official logo is loaded from ``brokerledger/gui/assets/logo.png`` (or
``.svg``). Drop the supplied company asset into that path. If it's missing
the header falls back to the bundled SVG placeholder so the app still
builds and runs.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QPixmap


# Brand palette — sampled from the Mortgage Oasis gradient.
BRAND_PURPLE = "#4A1766"
BRAND_PURPLE_DARK = "#2A0A3E"
BRAND_MAGENTA = "#D63A91"
BRAND_PINK_SOFT = "#F3E5F0"
BRAND_PURPLE_SOFT = "#EFE7F5"

TEXT_PRIMARY = "#1F1030"
TEXT_MUTED = "#6B6679"
SURFACE = "#FFFFFF"
APP_BG = "#FAF7FC"
BORDER = "#E5DFEE"

SUCCESS = "#1F8F3B"
WARNING = "#B87200"
DANGER = "#A52D1E"


ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH_CANDIDATES: tuple[Path, ...] = (
    ASSETS_DIR / "logo.png",
    ASSETS_DIR / "logo.svg",
    ASSETS_DIR / "logo_placeholder.svg",
)


def load_logo_pixmap(height: int = 40) -> QPixmap:
    """Return the first available logo scaled to ``height`` px."""
    from PySide6.QtCore import Qt

    for candidate in LOGO_PATH_CANDIDATES:
        if candidate.exists():
            pm = QPixmap(str(candidate))
            if not pm.isNull():
                return pm.scaledToHeight(
                    height,
                    Qt.TransformationMode.SmoothTransformation,
                )
    return QPixmap()


STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    color: {TEXT_PRIMARY};
}}

QMainWindow, QWidget#centralWidget, QStackedWidget {{
    background-color: {APP_BG};
}}

QStatusBar {{
    background-color: {BRAND_PURPLE_DARK};
    color: #FFFFFF;
    padding: 4px 10px;
}}
QStatusBar::item {{ border: none; }}

QLabel#BrandTitle {{
    font-size: 20px;
    font-weight: 600;
    color: #FFFFFF;
    letter-spacing: 0.5px;
}}
QLabel#BrandSubtitle {{
    color: rgba(255, 255, 255, 0.75);
    font-size: 12px;
}}
QLabel#BrandUserLabel {{
    color: #FFFFFF;
    font-weight: 600;
    padding-right: 8px;
}}
QFrame#BrandHeader {{
    background: qlineargradient(
        x1:0, y1:1, x2:1, y2:0,
        stop:0 {BRAND_MAGENTA},
        stop:1 {BRAND_PURPLE}
    );
    border: none;
    border-radius: 0px;
}}

QLabel {{
    background: transparent;
}}

QPushButton {{
    background-color: {BRAND_PURPLE};
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {BRAND_MAGENTA};
}}
QPushButton:pressed {{
    background-color: {BRAND_PURPLE_DARK};
}}
QPushButton:disabled {{
    background-color: #C9C2D4;
    color: #F2EEF7;
}}
QPushButton[flat="true"], QPushButton#GhostButton {{
    background-color: transparent;
    color: {BRAND_PURPLE};
    border: 1px solid {BRAND_PURPLE};
}}
QPushButton[flat="true"]:hover, QPushButton#GhostButton:hover {{
    background-color: {BRAND_PURPLE_SOFT};
    color: {BRAND_PURPLE_DARK};
}}

QPushButton#PrimaryButton {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {BRAND_PURPLE},
        stop:1 {BRAND_MAGENTA}
    );
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: 700;
    min-height: 18px;
}}
QPushButton#PrimaryButton:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {BRAND_MAGENTA},
        stop:1 {BRAND_PURPLE}
    );
}}
QPushButton#PrimaryButton:pressed {{
    background-color: {BRAND_PURPLE_DARK};
}}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {BRAND_MAGENTA};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus {{
    border: 1px solid {BRAND_PURPLE};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QGroupBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 16px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: {BRAND_PURPLE};
}}

QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget, QListView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    alternate-background-color: {BRAND_PURPLE_SOFT};
    selection-background-color: {BRAND_MAGENTA};
    selection-color: #FFFFFF;
}}
QHeaderView::section {{
    background-color: {BRAND_PURPLE_SOFT};
    color: {BRAND_PURPLE_DARK};
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}

QProgressBar {{
    background-color: {BORDER};
    border: none;
    border-radius: 6px;
    text-align: center;
    color: #FFFFFF;
    font-weight: 600;
}}
QProgressBar::chunk {{
    border-radius: 6px;
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {BRAND_PURPLE},
        stop:1 {BRAND_MAGENTA}
    );
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background-color: {SURFACE};
}}
QTabBar::tab {{
    padding: 8px 16px;
    background: transparent;
    border: none;
    color: {TEXT_MUTED};
    font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {BRAND_PURPLE};
    border-bottom: 3px solid {BRAND_MAGENTA};
}}

QMenu {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 0;
}}
QMenu::item {{
    padding: 6px 18px;
}}
QMenu::item:selected {{
    background-color: {BRAND_PURPLE_SOFT};
    color: {BRAND_PURPLE_DARK};
}}

QToolTip {{
    background-color: {BRAND_PURPLE_DARK};
    color: #FFFFFF;
    border: none;
    padding: 4px 8px;
    border-radius: 4px;
}}
"""


def apply_theme(app) -> None:
    app.setStyleSheet(STYLESHEET)
