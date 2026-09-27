"""The companion's icons: its own, drawn in code, and the ones on its buttons.

The button icons are Lucide's (ISC licence, assets/icons/lucide/LICENSE),
downloaded once as plain SVG files -- shapes only, no scripts or links, checked
in tests/test_button_icons.py -- and drawn in whatever colour a place needs:
each is a single stroke in currentColor. Nothing is fetched while running.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons" / "lucide"

#: Colours: the chat window's header grey, its dark menu, light menus and dialogs,
#: and the states a checked button shows.
HEADER = "#7d8598"
MENU_DARK = "#c9cedb"
ON_LIGHT = "#4a5263"
RECORDING = "#e06c6c"
LISTENING = "#7fd4a0"
QUIET = "#e6a15c"


def app_icon() -> QIcon:
    """Drawn rather than shipped as a binary asset: a blue disc with a white C."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#4a7dff"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(6, 6, 52, 52)
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setPointSize(28)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "C")
    painter.end()
    return QIcon(pixmap)


def _pixmap(name: str, color: str, size: int) -> QPixmap | None:
    """One Lucide icon in one colour, or None if its file is missing or unreadable."""
    try:
        svg = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    except OSError:
        return None
    renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", color).encode("utf-8")))
    if not renderer.isValid():
        return None
    scale = 2  # drawn at twice the size, so it stays sharp on scaled screens
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def button_icon(name: str, color: str = HEADER, on: tuple[str, str] | None = None,
                size: int = 18) -> QIcon:
    """A Lucide icon in `color` -- and while a checkable button is checked, `on`,
    an (icon, colour) pair, instead. Empty if the file is missing, so a button can
    fall back to its words rather than fail."""
    icon = QIcon()
    unchecked = _pixmap(name, color, size)
    if unchecked is None:
        return icon
    icon.addPixmap(unchecked, QIcon.Mode.Normal, QIcon.State.Off)
    if on is not None:
        checked = _pixmap(on[0], on[1], size)
        if checked is not None:
            icon.addPixmap(checked, QIcon.Mode.Normal, QIcon.State.On)
    return icon
