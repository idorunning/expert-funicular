"""Round avatar label — shows a photo or falls back to initials on a gradient."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel


_BRAND_PURPLE = QColor("#4A1766")
_BRAND_MAGENTA = QColor("#D63A91")


def _initials(name: str | None, username: str) -> str:
    raw = (name or username or "?").strip()
    if not raw:
        return "?"
    parts = [p for p in raw.replace("_", " ").replace("-", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return parts[0][:2].upper()


class AvatarLabel(QLabel):
    def __init__(self, size: int = 64, parent=None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._photo_path: str | None = None
        self._initials: str = "?"

    def set_photo(self, photo_path: str | None, username: str = "", full_name: str | None = None) -> None:
        self._photo_path = photo_path if photo_path and Path(photo_path).exists() else None
        self._initials = _initials(full_name, username)
        self._render()

    def _render(self) -> None:
        size = self._size
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        clip = QPainterPath()
        clip.addEllipse(0, 0, size, size)
        painter.setClipPath(clip)

        if self._photo_path:
            img = QImage(self._photo_path)
            if not img.isNull():
                scaled = img.scaled(
                    size, size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                off_x = (scaled.width() - size) // 2
                off_y = (scaled.height() - size) // 2
                painter.drawImage(-off_x, -off_y, scaled)
            else:
                self._draw_initials(painter, size)
        else:
            self._draw_initials(painter, size)

        # Thin outline ring.
        painter.setClipping(False)
        ring = QPen(QColor(255, 255, 255, 180), 2)
        painter.setPen(ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(1, 1, size - 2, size - 2)

        painter.end()
        self.setPixmap(pm)

    def _draw_initials(self, painter: QPainter, size: int) -> None:
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QLinearGradient

        grad = QLinearGradient(QPointF(0, 0), QPointF(size, size))
        grad.setColorAt(0.0, _BRAND_PURPLE)
        grad.setColorAt(1.0, _BRAND_MAGENTA)
        painter.fillRect(0, 0, size, size, QBrush(grad))

        painter.setPen(QColor("#FFFFFF"))
        font = QFont()
        font.setPointSize(max(8, int(size * 0.38)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, self._initials)
