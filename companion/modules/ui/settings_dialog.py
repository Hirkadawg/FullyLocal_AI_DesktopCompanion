"""The settings page: every entry of core.settings.SETTINGS, in tabs and groups.

The dialog only shows and collects values. Saving them, and applying what can
change while running, is the app's job (CompanionApp._apply_settings).

Laid out for reading: a tab per topic with small headings, "after
restart" as a quiet tag beside a label rather than inside it, whole numbers
without a decimal, a folder's full path on hover, and the companion's icon.
"""

from __future__ import annotations

from typing import Any

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.reset import RESETS, Reset, archive, archive_folder, describe, files
from core.settings import SECTIONS, SETTINGS, Setting, current_values
from modules.ui.icon import ON_LIGHT, app_icon, button_icon
from modules.voice.devices import microphone_choices

#: Descriptions, tags and statuses: quieter than the labels they explain.
MUTED = "#6b7385"


def screen_choices() -> list[tuple[int, str]]:
    """(monitor_index, label) for each screen, numbered the way the window docks."""
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    choices = []
    for i, screen in enumerate(screens):
        size = screen.geometry()
        mark = " (primary)" if screen is primary else ""
        choices.append((i + 1, f"Monitor {i + 1}: {size.width()}×{size.height()}{mark}"))
    return choices


class SettingsDialog(QDialog):
    def __init__(
        self,
        config,
        defaults: dict[str, Any],
        monitors: list[tuple[int, str]] | None = None,
        on_reset=None,
        parent=None,
        microphones: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Companion settings")
        self.setWindowIcon(app_icon())
        self.resize(680, 720)
        self.config = config
        self.defaults = defaults
        self.monitors = monitors if monitors is not None else screen_choices()
        #: (saved value, label), "System default" first.
        self.microphones = microphones if microphones is not None else microphone_choices()
        #: Called with a reset's id; returns a core.reset.ResetResult. The app
        #: passes the worker's, which also forgets learned counts held in memory.
        self.on_reset = on_reset or (lambda reset_id: archive(config, reset_id))
        self._widgets: dict[str, QWidget] = {}
        #: The "after restart" tag beside each restart-only setting, by id.
        self._restart_tags: dict[str, QLabel] = {}
        self._reset_rows: dict[str, tuple[QLabel, QPushButton]] = {}

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        values = current_values(config)
        for section in SECTIONS:
            settings = [s for s in SETTINGS if s.section == section]
            if section == "Your data":
                tabs.addTab(self._data_tab(settings, values), section)
            else:
                tabs.addTab(self._page(settings, values), section)
        self.tabs = tabs
        layout.addWidget(tabs)

        note = QLabel("Saved on this machine, in data/settings.yaml. Settings tagged "
                      "\"after restart\" apply the next time the companion starts.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText(
            "Back to config.yaml"
        )
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setIcon(
            button_icon("undo-2", ON_LIGHT)
        )
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: self.set_values(self.defaults)
        )
        layout.addWidget(buttons)

    @staticmethod
    def _scrolling(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Rows wrap to the dialog's width; they never scroll sideways.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _column(page: QWidget) -> QVBoxLayout:
        rows = QVBoxLayout(page)
        rows.setSpacing(10)
        rows.setContentsMargins(14, 8, 14, 14)
        return rows

    @staticmethod
    def _heading(text: str, first: bool = False) -> QLabel:
        """A group's name, with a rule under it."""
        heading = QLabel(text)
        heading.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: #2b3445; padding-top: {2 if first else 12}px; "
            "padding-bottom: 3px; border-bottom: 1px solid #d5d9e0;"
        )
        return heading

    def _page(self, settings: list[Setting], values: dict[str, Any]) -> QScrollArea:
        page = QWidget()
        rows = self._column(page)
        group = None
        for setting in settings:
            if setting.group and setting.group != group:
                group = setting.group
                rows.addWidget(self._heading(group, first=rows.count() == 0))
            rows.addWidget(self._row(setting, values[setting.id]))
        rows.addStretch()
        return self._scrolling(page)

    # -- your data --------------------------------------------------------------

    def _data_tab(self, settings: list[Setting], values: dict[str, Any]) -> QScrollArea:
        page = QWidget()
        rows = self._column(page)
        intro = QLabel("Start any of these over. Nothing is deleted: a reset moves the files "
                       "to the archive folder below, and moving them back undoes it. "
                       "A reset happens straight away, not on Save.")
        intro.setWordWrap(True)
        rows.addWidget(intro)
        rows.addWidget(self._heading("Resets"))
        for item in RESETS:
            rows.addWidget(self._reset_row(item))
        group = None
        for setting in settings:
            if setting.group != group:
                group = setting.group
                rows.addWidget(self._heading(group))
            rows.addWidget(self._row(setting, values[setting.id]))
        rows.addStretch()
        return self._scrolling(page)

    def _reset_row(self, item: Reset) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        line = QHBoxLayout()
        name = QLabel(item.label)
        name.setWordWrap(True)
        line.addWidget(name, stretch=1)
        status = QLabel()
        status.setStyleSheet(f"color: {MUTED};")
        line.addWidget(status)
        button = QPushButton("Reset…")
        button.setIcon(button_icon("rotate-ccw", ON_LIGHT))
        button.setToolTip(item.help)
        button.clicked.connect(lambda _=False, i=item: self._reset(i))
        line.addWidget(button)
        column.addLayout(line)
        help_text = QLabel(item.help)
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        column.addWidget(help_text)
        self._reset_rows[item.id] = (status, button)
        self._show_saved(item)
        return box

    def reset_row(self, reset_id: str) -> tuple[QLabel, QPushButton]:
        """The row's "what is saved" label and its Reset button."""
        return self._reset_rows[reset_id]

    def _show_saved(self, item: Reset) -> None:
        status, button = self._reset_rows[item.id]
        status.setText(describe(self.config, item.id))
        button.setEnabled(bool(files(self.config, item.id)))

    def confirm_text(self, item: Reset) -> str:
        return (f"Reset {item.label.lower()}?\n\n{item.help}\n\n"
                f"The files are moved to {archive_folder(self.config)}, not deleted. "
                "This happens now, even if you then close the settings without saving.")

    def _confirm_reset(self, item: Reset) -> bool:
        answer = QMessageBox.question(
            self, f"Reset {item.label.lower()}", self.confirm_text(item),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _tell(self, text: str, problem: bool = False) -> None:
        show = QMessageBox.warning if problem else QMessageBox.information
        show(self, "Companion settings", text)

    def _reset(self, item: Reset) -> None:
        if not self._confirm_reset(item):
            return
        try:
            result = self.on_reset(item.id)
        except OSError as exc:
            # Files moved before the failure are already safe in the archive.
            self._tell(f"Couldn't reset {item.label.lower()}: {exc}", problem=True)
            self._show_saved(item)
            return
        self._show_saved(item)
        if result.folder is None:
            self._tell("There was nothing to reset.")
            return
        count = len(result.moved)
        self._tell(f"{item.label} reset. {count} file{'s' if count != 1 else ''} moved to\n"
                   f"{result.folder}")

    # -- rows -----------------------------------------------------------------

    def _tag(self, setting: Setting, line: QHBoxLayout) -> None:
        """A quiet "after restart" beside the label of a restart-only setting."""
        if setting.live:
            return
        tag = QLabel("after restart")
        tag.setStyleSheet(f"color: {MUTED}; font-size: 10px; border: 1px solid #c9ced8; "
                          "border-radius: 7px; padding: 0px 6px;")
        tag.setToolTip("Takes effect the next time the companion starts.")
        line.addWidget(tag)
        self._restart_tags[setting.id] = tag

    def _row(self, setting: Setting, value: Any) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(8)

        if setting.kind in ("bool", "tool"):
            widget = QCheckBox(setting.label)
            line.addWidget(widget)
            self._tag(setting, line)
            line.addStretch()
            column.addLayout(line)
        else:
            line.addWidget(QLabel(setting.label))
            self._tag(setting, line)
            line.addStretch()
            if setting.kind == "int":
                widget = QSpinBox()
                widget.setRange(int(setting.minimum), int(setting.maximum))
                widget.setSingleStep(int(setting.step))
                widget.setSuffix(setting.unit)
            elif setting.kind == "float":
                widget = QDoubleSpinBox()
                # "10 s", not "10,0 s": a decimal only where the steps have one.
                widget.setDecimals(0 if float(setting.step).is_integer() else 1)
                widget.setRange(setting.minimum, setting.maximum)
                widget.setSingleStep(setting.step)
                widget.setSuffix(setting.unit)
            elif setting.kind == "monitor":
                widget = QComboBox()
                for index, text in self.monitors:
                    widget.addItem(text, index)
            elif setting.kind == "choice":
                widget = QComboBox()
                # Not `value`: that is this row's current value, set below --
                # reusing the name showed the last choice whatever was chosen.
                for choice, text in setting.choices:
                    widget.addItem(text, choice)
            elif setting.kind == "microphone":
                widget = QComboBox()
                for name, text in self.microphones:
                    widget.addItem(text, name)
            elif setting.kind == "hotkey":
                widget = QLineEdit()
                widget.setPlaceholderText("none")
            elif setting.kind in ("folder", "dir"):
                widget = QLineEdit()
                # A long path is cut off in the field: the whole of it on hover.
                widget.textChanged.connect(widget.setToolTip)
                choose = QPushButton("Choose…")
                choose.setIcon(button_icon("folder-open", ON_LIGHT))
                choose.clicked.connect(lambda _=False, w=widget, s=setting: self._choose(w, s))
                show = QPushButton("Open")
                show.setIcon(button_icon("external-link", ON_LIGHT))
                show.setToolTip("Open this folder in Explorer")
                show.clicked.connect(lambda _=False, w=widget: self._open_folder(w.text()))
            else:  # pragma: no cover -- every kind is handled above
                raise ValueError(f"unknown setting kind {setting.kind!r}")
            if setting.kind in ("folder", "dir"):
                # A path is long: it gets a line of its own under the label.
                column.addLayout(line)
                path_line = QHBoxLayout()
                path_line.addWidget(widget, stretch=1)
                path_line.addWidget(choose)
                path_line.addWidget(show)
                column.addLayout(path_line)
            else:
                widget.setFixedWidth(
                    {"monitor": 220, "microphone": 270, "hotkey": 160}.get(setting.kind, 110)
                )
                line.addWidget(widget)
                column.addLayout(line)

        help_text = QLabel(setting.help)
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        column.addWidget(help_text)
        if setting.kind not in ("folder", "dir"):
            widget.setToolTip(setting.help)
        self._widgets[setting.id] = widget
        self._set(setting, value)
        return box

    def _open_folder(self, path: str) -> None:
        """Show the folder in Explorer, creating it if nothing has been saved yet."""
        folder = Path(path.strip())
        if not path.strip():
            return
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _choose(self, widget: QLineEdit, setting: Setting) -> None:
        folder = QFileDialog.getExistingDirectory(self, setting.label, widget.text())
        if folder:
            widget.setText(folder)

    # -- values ---------------------------------------------------------------

    def widget(self, setting_id: str) -> QWidget:
        return self._widgets[setting_id]

    def _set(self, setting: Setting, value: Any) -> None:
        widget = self._widgets[setting.id]
        if setting.kind in ("bool", "tool"):
            widget.setChecked(bool(value))
        elif setting.kind in ("int", "float"):
            widget.setValue(value)
        elif setting.kind == "monitor":
            index = widget.findData(int(value))
            if index < 0:
                widget.addItem(f"Monitor {value} (not connected)", int(value))
                index = widget.count() - 1
            widget.setCurrentIndex(index)
        elif setting.kind == "microphone":
            index = widget.findData(str(value))
            if index < 0:
                # Kept, not dropped: plugging it back in makes it work again.
                widget.addItem(f"{value} (not connected)", str(value))
                index = widget.count() - 1
            widget.setCurrentIndex(index)
        elif setting.kind in ("folder", "dir", "hotkey"):
            widget.setText(str(value))
        elif setting.kind == "choice":
            index = widget.findData(str(value))
            widget.setCurrentIndex(index if index >= 0 else 0)

    def set_values(self, values: dict[str, Any]) -> None:
        for setting in SETTINGS:
            if setting.id in values:
                self._set(setting, values[setting.id])

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for setting in SETTINGS:
            widget = self._widgets[setting.id]
            if setting.kind in ("bool", "tool"):
                out[setting.id] = widget.isChecked()
            elif setting.kind == "int":
                out[setting.id] = int(widget.value())
            elif setting.kind == "float":
                out[setting.id] = round(float(widget.value()), 3)
            elif setting.kind == "monitor":
                out[setting.id] = int(widget.currentData())
            elif setting.kind in ("folder", "dir", "hotkey"):
                out[setting.id] = widget.text().strip()
            elif setting.kind in ("choice", "microphone"):
                out[setting.id] = str(widget.currentData())
        return out
