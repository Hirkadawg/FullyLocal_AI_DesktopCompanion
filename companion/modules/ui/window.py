"""The always-on-top chat window.

Frameless and docked to a corner of the target monitor, so it sits beside what
you're reading rather than covering it. This is also the shell the avatar will
eventually live in, which is why it is a real Qt window rather than a console.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QSize, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QTextBlockFormat,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig
from core.logging import get_logger
from modules.ui.icon import HEADER, LISTENING, MENU_DARK, QUIET, RECORDING, button_icon

log = get_logger(__name__)

STYLE = """
#root {{ background: transparent; }}
#card {{
    background: #16181d;
    border: 1px solid #2b2f3a;
    border-radius: 14px;
}}
#title {{ color: #e6e8ee; font-weight: 600; }}
#level {{
    color: #8fb3ff;
    font-size: 12px;
    border: 1px solid #2f3a52;
    border-radius: 9px;
    padding: 1px 8px;
}}
#level:hover {{ color: #e6e8ee; border-color: #4a5670; }}
#status {{ color: #7d8598; font-size: {small}px; }}
QTextBrowser {{
    background: transparent;
    border: none;
    color: #d7dae3;
    font-size: {size}px;
    selection-background-color: #3b4356;
}}
QLineEdit {{
    background: #1e212a;
    border: 1px solid #2f3441;
    border-radius: 9px;
    padding: 9px 12px;
    color: #e6e8ee;
    font-size: {size}px;
}}
QLineEdit:focus {{ border-color: #4a5670; }}
QLineEdit:disabled {{ color: #b9c1d3; border-style: dashed; }}
QPushButton {{
    background: transparent;
    border: none;
    color: #7d8598;
    font-size: 15px;
    padding: 2px 7px;
}}
QPushButton:hover {{ color: #e6e8ee; }}
QPushButton::menu-indicator {{ image: none; width: 0px; }}
QMenu {{
    background: #1e212a;
    color: #e6e8ee;
    border: 1px solid #2f3441;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 18px; }}
QMenu::item:selected {{ background: #3b4356; }}
"""


_ASK_PLACEHOLDER = "Ask about what's on screen…"


def _thumbs_html(item_id: str, chosen: str | None) -> str:
    """The two thumbs as links; the chosen one sits on a highlight."""
    parts = []
    for rating, thumb in (("up", "👍"), ("down", "👎")):
        background = "background-color:#34405a;" if rating == chosen else ""
        parts.append(
            f'<a href="rate:{item_id}:{rating}" '
            f'style="text-decoration:none;{background}">{thumb}</a>'
        )
    return "&nbsp;&nbsp;".join(parts)


class ChatWindow(QWidget):
    """Frameless chat panel: transcript, input box, status line."""

    ask_requested = Signal(str)
    cancel_requested = Signal()
    escape_pressed = Signal()
    quiet_toggled = Signal(bool)
    #: "Say something about this page."
    remark_requested = Signal()
    #: The mic button: start recording a question, or send the one recording.
    mic_clicked = Signal()
    #: The listen button: hands-free listening on (True) or off.
    listen_toggled = Signal(bool)
    #: Open the settings page.
    settings_requested = Signal()
    #: A thumb was clicked: (the rated item, "up" or "down").
    rated = Signal(object, str)
    #: Language to force for speech input; "" means auto-detect.
    language_changed = Signal(str)
    clear_requested = Signal()
    hide_requested = Signal()
    #: The window was shown or hidden, however it happened (the avatar makes room).
    shown = Signal()
    hidden = Signal()
    quit_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self._drag_offset: QPoint | None = None
        self._answering = False
        self._answer_open = False
        #: Rated items by id: (item, start, end) of their thumbs in the transcript.
        self._ratable: dict[str, list] = {}
        #: While the mic button records, the input box shows what was heard;
        #: whatever was typed before is kept here and put back afterwards.
        self._typed_before_preview: str | None = None

        self.setObjectName("root")
        self.setWindowTitle("Companion")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keeps it out of the taskbar and Alt-Tab
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(config.ui.width, config.ui.height)
        self.setWindowOpacity(config.ui.opacity)
        self._build()
        self._place()

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QWidget(objectName="card")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("Companion", objectName="title")
        header.addWidget(title)
        # Your level with the companion: hidden until the app sets
        # one, and while ratings or the setting are off. Click it for the details.
        self.level_button = QPushButton(objectName="level")
        self.level_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.level_button.clicked.connect(self._show_level_details)
        self.level_button.setVisible(False)
        self._level_details = ""
        header.addWidget(self.level_button)
        header.addStretch()
        if self.config.speech.enabled and len(self.config.speech.languages) > 1:
            # Cycles auto -> first language -> second -> auto. Visible in the
            # header rather than buried in a menu: when detection mishears you,
            # you want to pin it immediately, not go looking.
            self._lang_cycle = [None, *self.config.speech.languages]
            # Starts where speech.language pins it, not always on auto.
            pinned = (self.config.speech.language or "").lower() or None
            if pinned not in self._lang_cycle:
                self._lang_cycle.append(pinned)
            self._lang_index = self._lang_cycle.index(pinned)
            # The one button that keeps words: the language beside its icon.
            self.language_button = self._icon_button(
                "languages", "lang",
                "Pin a language: it listens for it, replies and makes remarks in it, and "
                "speaks with its voice. auto: it hears either and replies in the one you "
                "use. Video listening is unaffected.",
            )
            self._show_language(pinned)
            self.language_button.clicked.connect(self._cycle_language)
            header.addWidget(self.language_button)

        if self.config.speech.enabled and self.config.speech.mic_button:
            # While recording it is a red send arrow: clicking it again sends.
            self.mic_button = self._icon_button(
                "mic", "mic",
                f"Click to speak, click again to send (or hold {self.config.speech.hotkey})",
                on=("send", RECORDING),
            )
            self.mic_button.setCheckable(True)
            self.mic_button.clicked.connect(lambda: self.mic_clicked.emit())
            header.addWidget(self.mic_button)

        if self.config.speech.enabled and self.config.speech.listen_button:
            self.listen_button = self._icon_button(
                "ear", "listen",
                "Hands-free: the microphone stays on and each thing you say is "
                "answered after a pause. Click again to stop.",
                on=("ear", LISTENING),
            )
            self.listen_button.setCheckable(True)
            self.listen_button.toggled.connect(self.listen_toggled.emit)
            header.addWidget(self.listen_button)

        # Always shown, even with unprompted remarks off: asking for a remark is
        # a different thing from being interrupted by one.
        hotkey = self.config.proactive.remark_now_hotkey
        self.say_button = self._icon_button(
            "message-circle", "say",
            "Say something about this page" + (f" ({hotkey})" if hotkey else ""),
        )
        self.say_button.clicked.connect(self.remark_requested.emit)
        header.addWidget(self.say_button)

        # In the window, not only the tray: the tray item is in the right-click
        # menu, which is not where anyone looks when they want something to stop
        # talking. Built either way and shown with remarks, since the settings
        # page can switch them on while running.
        self.quiet_button = self._icon_button(
            "bell", "quiet", "Stop unprompted remarks", on=("bell-off", QUIET)
        )
        self.quiet_button.setCheckable(True)
        self.quiet_button.toggled.connect(self._on_quiet_toggled)
        self.quiet_button.setVisible(self.config.proactive.enabled)
        header.addWidget(self.quiet_button)

        # The less-used actions share one menu. With a button each, the header
        # forced the window to 620 px against ui.width 520.
        self.menu_button = self._icon_button("menu", "menu", "Settings, clear, hide, quit")
        self.menu = QMenu(self.menu_button)
        for text, signal, icon in (
            ("Settings…", self.settings_requested, "settings"),
            ("Clear conversation", self.clear_requested, "eraser"),
            (f"Hide  ({self.config.ui.hotkey} brings it back)", self.hide_requested, "eye-off"),
            ("Quit", self.quit_requested, "power"),
        ):
            action = self.menu.addAction(button_icon(icon, MENU_DARK), text)
            action.triggered.connect(lambda _=False, s=signal: s.emit())
        self.menu_button.setMenu(self.menu)
        header.addWidget(self.menu_button)
        layout.addLayout(header)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        # Links in the transcript are the rating thumbs: handled here, never
        # followed.
        self.transcript.setOpenLinks(False)
        self.transcript.anchorClicked.connect(self._on_anchor)
        layout.addWidget(self.transcript, stretch=1)

        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.input = QLineEdit()
        self.input.setPlaceholderText(_ASK_PLACEHOLDER)
        self.input.returnPressed.connect(self._submit)
        layout.addWidget(self.input)

        self.setStyleSheet(
            STYLE.format(size=self.config.ui.font_size,
                         small=max(10, self.config.ui.font_size - 3))
        )
        self.transcript.setFont(QFont("Segoe UI", self.config.ui.font_size))

    def _place(self) -> None:
        """Dock to the configured corner of the configured monitor."""
        screens = QApplication.screens()
        # config.monitor_index counts mss-style, where 0 is the virtual union of
        # every display and physical monitors start at 1. Qt only lists physical
        # screens, so shift by one.
        index = max(0, min(self.config.monitor_index - 1, len(screens) - 1))
        area = screens[index].availableGeometry()
        margin = self.config.ui.margin
        corner = self.config.ui.corner

        x = (
            area.left() + margin
            if "left" in corner
            else area.right() - self.width() - margin
        )
        y = (
            area.top() + margin
            if "top" in corner
            else area.bottom() - self.height() - margin
        )
        self.move(x, y)

    # -- transcript -----------------------------------------------------------

    def add_question(self, text: str) -> None:
        """Start a new turn: the question, styled distinctly from the reply."""
        cursor = self._new_block(top_margin=20, char_format=self._question_format())
        cursor.insertText(text, self._question_format())
        self._answer_open = False
        self._scroll_to_end()

    def add_chunk(self, text: str) -> None:
        """Append streamed answer text into the current reply block."""
        if not self._answer_open:
            self._new_block(top_margin=8, char_format=self._answer_format())
            self._answer_open = True
        pinned = self._at_bottom()
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        # The format is passed on every insert: a cursor's inherited format is
        # not reliable across the many small writes that streaming produces.
        cursor.insertText(text, self._answer_format())
        if pinned:
            self._scroll_to_end()

    def add_notice(self, text: str, colour: str = "#e6a15c") -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colour))
        fmt.setFontItalic(True)
        cursor = self._new_block(top_margin=10, char_format=fmt)
        cursor.insertText(text, fmt)
        self._answer_open = False
        self._scroll_to_end()

    def add_reason(self, text: str) -> None:
        """A small, muted line under a remark: why it was made."""
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#6b7385"))
        fmt.setFontPointSize(max(7.0, self.config.ui.font_size - 2))
        cursor = self._new_block(top_margin=2, char_format=fmt)
        cursor.insertText(f"why: {text}", fmt)
        self._answer_open = False
        self._scroll_to_end()

    def apply_settings(self, redock: bool = False) -> None:
        """Reflect settings changed while running."""
        self.quiet_button.setVisible(self.config.proactive.enabled)
        if redock:
            self._place()

    def _icon_button(self, icon: str, name: str, tooltip: str,
                     on: tuple[str, str] | None = None) -> QPushButton:
        """A header button showing a Lucide icon (modules/ui/icon.py), its words in
        the tooltip. `name` is what it is called: its accessible name, and the
        words shown if the icon file is ever missing. `on` is the (icon, colour)
        it shows while checked."""
        button = QPushButton()
        drawn = button_icon(icon, HEADER, on=on)
        if drawn.isNull():
            button.setText(name)
        else:
            button.setIcon(drawn)
            button.setIconSize(QSize(18, 18))
        button.setAccessibleName(name)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _rename(button: QPushButton, name: str) -> None:
        """A button's state, in words: its name, and its text when it has no icon."""
        button.setAccessibleName(name)
        if button.icon().isNull():
            button.setText(name)

    def set_level(self, level, up: int = 0, down: int = 0) -> None:
        """Show the relationship's level (core/relationship.py), or hide it with None."""
        if level is None:
            self.level_button.setVisible(False)
            return
        from core.relationship import describe

        self.level_button.setText(f"Lv {level.level} · {level.into}/{level.step}")
        self.level_button.setAccessibleName(f"level {level.level}")
        self.level_button.setToolTip(
            f"Your level with the companion: {level.to_next} more votes to level {level.level + 1}. "
            "Click for how it works."
        )
        self._level_details = describe(level, up, down)
        self.level_button.setVisible(True)

    def _show_level_details(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, "Your level with the companion", self._level_details)

    def _show_language(self, language: str | None) -> None:
        self.language_button.setText(language or "auto")
        self.language_button.setAccessibleName(f"lang: {language or 'auto'}")

    def set_listening(self, listening: bool) -> None:
        """Show whether hands-free listening is on."""
        button = getattr(self, "listen_button", None)
        if button is None:
            return
        button.blockSignals(True)
        button.setChecked(listening)
        self._rename(button, "● listening" if listening else "listen")
        button.blockSignals(False)

    def show_preview(self, text: str) -> None:
        """Show what the microphone has heard so far, in the input box.

        Disabled while it shows, so Enter can't send a half-heard question; the
        mic button's send is what asks, with the full recording.
        """
        if self._typed_before_preview is None:
            self._typed_before_preview = self.input.text()
            self.input.setEnabled(False)
            self.input.setPlaceholderText("listening… click the red send arrow when done")
        self.input.setText(text)

    def end_preview(self) -> None:
        if self._typed_before_preview is None:
            return
        self.input.setText(self._typed_before_preview)
        self._typed_before_preview = None
        self.input.setPlaceholderText(_ASK_PLACEHOLDER)
        self.input.setEnabled(True)

    def set_recording(self, recording: bool) -> None:
        """Show whether the microphone is recording, whatever started it."""
        button = getattr(self, "mic_button", None)
        if button is None:
            return
        button.blockSignals(True)
        button.setChecked(recording)
        self._rename(button, "● send" if recording else "mic")
        button.blockSignals(False)

    def add_rating(self, item) -> None:
        """Thumbs up and down under an answer or a remark, if ratings are on."""
        if not self.config.ratings.enabled:
            return
        fmt = QTextCharFormat()
        fmt.setFontPointSize(max(7.0, self.config.ui.font_size - 3))
        cursor = self._new_block(top_margin=2, char_format=fmt)
        start = cursor.position()
        cursor.insertHtml(_thumbs_html(item.id, None))
        self._ratable[item.id] = [item, start, cursor.position()]
        # Text typed after this must not inherit the link format.
        cursor.setCharFormat(QTextCharFormat())
        self._answer_open = False
        self._scroll_to_end()

    def _on_anchor(self, url: QUrl) -> None:
        scheme, _, rest = url.toString().partition(":")
        item_id, _, rating = rest.partition(":")
        entry = self._ratable.get(item_id)
        if scheme != "rate" or entry is None or rating not in ("up", "down"):
            return
        item, start, end = entry
        cursor = self.transcript.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(_thumbs_html(item_id, rating))
        shift = cursor.position() - end
        entry[2] = cursor.position()
        if shift:
            for other in self._ratable.values():
                if other is not entry and other[1] > start:
                    other[1] += shift
                    other[2] += shift
        self.rated.emit(item, rating)

    def clear_transcript(self) -> None:
        self.transcript.clear()
        self._ratable.clear()
        self._answer_open = False
        self.add_notice("Conversation cleared.", "#7d8598")

    # -- text formats ---------------------------------------------------------

    def _question_format(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#8fb3ff"))
        fmt.setFontWeight(QFont.Weight.DemiBold)
        fmt.setFontPointSize(self.config.ui.font_size)
        return fmt

    def _answer_format(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#d7dae3"))
        fmt.setFontWeight(QFont.Weight.Normal)
        fmt.setFontPointSize(self.config.ui.font_size)
        return fmt

    def _new_block(self, top_margin: int, char_format: QTextCharFormat):
        """Begin a paragraph with its own spacing, and return a cursor in it.

        An empty document already contains one block, so inserting another
        would leave a blank line at the top; the first block is reused instead.
        """
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        block = QTextBlockFormat()
        block.setTopMargin(top_margin)
        block.setBottomMargin(0)
        if self.transcript.document().isEmpty():
            cursor.setBlockFormat(block)
            cursor.setCharFormat(char_format)
        else:
            cursor.insertBlock(block, char_format)
        self.transcript.setTextCursor(cursor)
        return cursor

    def _at_bottom(self) -> bool:
        """Whether the view is scrolled to the end.

        Streaming should not yank the view back down while you are reading
        something further up.
        """
        bar = self.transcript.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_to_end(self) -> None:
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.transcript.ensureCursorVisible()

    # -- state ----------------------------------------------------------------

    def _cycle_language(self) -> None:
        self._lang_index = (self._lang_index + 1) % len(self._lang_cycle)
        language = self._lang_cycle[self._lang_index]
        self._show_language(language)
        self.language_changed.emit(language or "")

    def _on_quiet_toggled(self, quiet: bool) -> None:
        self._rename(self.quiet_button, "quiet ✓" if quiet else "quiet")
        self.quiet_toggled.emit(quiet)

    def set_quiet(self, quiet: bool) -> None:
        """Reflect mute state set elsewhere, e.g. from the tray."""
        button = getattr(self, "quiet_button", None)
        if button is not None and button.isChecked() != quiet:
            button.blockSignals(True)
            button.setChecked(quiet)
            self._rename(button, "quiet ✓" if quiet else "quiet")
            button.blockSignals(False)

    @property
    def is_answering(self) -> bool:
        return self._answering

    def set_answering(self, answering: bool) -> None:
        """`answering` means busy: still writing *or* still speaking."""
        self._answering = answering
        self.input.setPlaceholderText(
            "Esc to stop" if answering else "Ask about what's on screen…"
        )

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def focus_input(self) -> None:
        self.input.setFocus()
        self.input.selectAll()

    # -- events ---------------------------------------------------------------

    def _submit(self) -> None:
        question = self.input.text().strip()
        if not question or self._answering:
            return
        self.input.clear()
        self.add_question(question)
        self.ask_requested.emit(question)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            # The window deliberately does not decide what Esc means. Speech
            # outlives the token stream by a long way, so "is an answer still
            # being written" is the wrong question -- only the worker knows
            # whether anything is still running.
            self.escape_pressed.emit()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not event.spontaneous():  # shown by the app, not restored by Windows
            self.shown.emit()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if not event.spontaneous():
            self.hidden.emit()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None

    def paintEvent(self, event) -> None:
        """Rounded drop shadow behind the card."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)
        painter.fillPath(path, QColor(0, 0, 0, 40))


