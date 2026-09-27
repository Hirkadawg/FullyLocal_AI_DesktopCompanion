"""Icons on the buttons.

Asked for: appropriate icons on most of the buttons. The chat window's header
words -- lang, mic, listen, say, quiet, menu -- became Lucide icons (ISC
licence), downloaded once with the user's approval as 18 plain SVG files
(276-586 bytes each, plus the licence) into assets/icons/lucide, and drawn in
the colour each place needs. A checked button shows its state in its icon: the
mic becomes a red send arrow, listen turns green, quiet becomes an amber
bell-off. The chat menu, the tray menu and the settings page's buttons have
icons too. Each button's words stay in its tooltip and accessible name, and come
back as its text if an icon file is ever missing.
"""

import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QPushButton

from core.config import AppConfig
from core.logging import setup_logging
from core.settings import current_values
from modules.ui import icon
from modules.ui.app import CompanionApp
from modules.ui.settings_dialog import SettingsDialog
from modules.ui.window import ChatWindow

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


qt = QApplication.instance() or QApplication(sys.argv)
USED = ["mic", "send", "ear", "message-circle", "bell", "bell-off", "languages", "menu", "settings", "eraser",
        "eye-off", "power", "app-window", "move", "folder-open", "external-link", "rotate-ccw", "undo-2"]

print("the icon files")

found = sorted(p.stem for p in icon.ICON_DIR.glob("*.svg"))
check("every icon used is there, and no others", found == sorted(USED), str(found))
tags, suspicious = set(), []
for name in USED:
    body = (icon.ICON_DIR / f"{name}.svg").read_text(encoding="utf-8").replace('xmlns="http://www.w3.org/2000/svg"', "")
    tags |= set(re.findall(r"<([a-zA-Z]+)", body))
    if re.search(r"<script|href|xlink|foreignObject|\son[a-z]+=|https?://|<image|<use|data:", body, re.I):
        suspicious.append(name)
check("plain drawings: shapes only, no scripts, links or anything fetched",
      not suspicious and tags <= {"svg", "path", "rect", "circle", "line", "polyline", "polygon", "ellipse"},
      f"{suspicious} {sorted(tags)}")
check("small: each under 1 KB", all((icon.ICON_DIR / f"{n}.svg").stat().st_size < 1024 for n in USED))
licence = (icon.ICON_DIR / "LICENSE").read_text(encoding="utf-8")
check("Lucide's ISC licence is kept with them", licence.startswith("ISC License") and "Lucide" in licence)


def colour_of(pixmap):
    image = pixmap.toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            if colour.alpha() > 230:
                return colour
    return None


def near(colour, wanted):
    wanted = QColor(wanted)
    return colour is not None and all(abs(a - b) <= 12 for a, b in
                                      zip(colour.getRgb()[:3], wanted.getRgb()[:3]))


print("\ndrawing them")

size = QSize(18, 18)
red = icon.button_icon("mic", "#e06c6c")
check("drawn in the colour asked for", near(colour_of(red.pixmap(size)), "#e06c6c"),
      str(colour_of(red.pixmap(size)).name() if colour_of(red.pixmap(size)) else None))
mic = icon.button_icon("mic", icon.HEADER, on=("send", icon.RECORDING))
unchecked = mic.pixmap(size, QIcon.Mode.Normal, QIcon.State.Off)
checked = mic.pixmap(size, QIcon.Mode.Normal, QIcon.State.On)
check("a checkable button's icon changes when checked: a grey mic, a red send arrow",
      near(colour_of(unchecked), icon.HEADER) and near(colour_of(checked), icon.RECORDING)
      and unchecked.toImage() != checked.toImage())
check("a missing icon is an empty icon, not an error", icon.button_icon("no-such-icon").isNull())

print("\nthe chat window")

cfg = AppConfig.load(CONFIG_PATH)
cfg.speech.enabled, cfg.speech.languages = True, ["en", "tr"]
cfg.speech.mic_button = cfg.speech.listen_button = True
cfg.proactive.enabled = True
window = ChatWindow(cfg)
buttons = {"lang": window.language_button, "mic": window.mic_button, "listen": window.listen_button,
           "say": window.say_button, "quiet": window.quiet_button, "menu": window.menu_button}
check("every header button has an icon and a tooltip saying what it does",
      all(not b.icon().isNull() and b.toolTip() for b in buttons.values()))
check("...no words, except the language beside its icon",
      all(not b.text() for key, b in buttons.items() if key != "lang") and window.language_button.text() == "auto",
      str({key: b.text() for key, b in buttons.items()}))
check("...and each keeps its words as its name",
      [b.accessibleName() for b in buttons.values()] == ["lang: auto", "mic", "listen", "say", "quiet", "menu"])
window.set_recording(True)
check("recording: the mic is checked -- the red send arrow -- and named send",
      window.mic_button.isChecked() and window.mic_button.accessibleName() == "● send")
window.set_recording(False)
window.set_listening(True)
window.set_quiet(True)
check("listening and quiet show as checked, and are named so",
      window.listen_button.isChecked() and window.listen_button.accessibleName() == "● listening"
      and window.quiet_button.isChecked() and window.quiet_button.accessibleName() == "quiet ✓")
check("the header menu's items have icons", all(not a.icon().isNull() for a in window.menu.actions()))
check(f"with every button showing, the header fits ui.width ({cfg.ui.width}px) with room to spare",
      window.minimumSizeHint().width() <= cfg.ui.width, f"needs {window.minimumSizeHint().width()}px")

real_dir = icon.ICON_DIR
icon.ICON_DIR = Path(tempfile.mkdtemp(prefix="companion-no-icons-"))
bare = ChatWindow(cfg)
icon.ICON_DIR = real_dir
bare.set_recording(True)
check("with the icon files missing, buttons show their words instead",
      bare.say_button.text() == "say" and bare.quiet_button.text() == "quiet" and bare.mic_button.text() == "● send",
      str((bare.say_button.text(), bare.quiet_button.text(), bare.mic_button.text())))

print("\nthe tray and the settings page")

fake = SimpleNamespace(config=cfg, icon=icon.app_icon(), toggle=lambda: None, _on_clear=lambda: None,
                       _on_remark_now=lambda: None, _open_settings=lambda: None, _set_avatar_moving=lambda on: None,
                       _set_avatar_hidden=lambda hidden: None, _on_toggle_mute=lambda quiet: None, quit=lambda: None)
CompanionApp._build_tray(fake)
tray_actions = [a for a in fake.tray.contextMenu().actions() if not a.isSeparator()]
check("every tray menu item has an icon", tray_actions and all(not a.icon().isNull() for a in tray_actions),
      str([a.text() for a in tray_actions if a.icon().isNull()]))
fake.tray.hide()

dialog = SettingsDialog(cfg, defaults=current_values(cfg), monitors=[(1, "Monitor 1")],
                        microphones=[("", "System default")])
labelled = {}
for button in dialog.findChildren(QPushButton):
    labelled.setdefault(button.text(), []).append(button)
wanted = ("Choose…", "Open", "Reset…", "Back to config.yaml")
check("the settings page's Choose, Open, Reset and Back to config.yaml buttons have icons",
      all(text in labelled for text in wanted)
      and all(not b.icon().isNull() for text in wanted for b in labelled[text]))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
