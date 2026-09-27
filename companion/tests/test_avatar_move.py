"""Moving the avatar with a shortcut.

First built as "click the character, then arrow keys". The user asked instead
for an unused shortcut that turns moving on, then click and drag the avatar into
place. avatar.move_hotkey -- ctrl+shift+2 in config.yaml, checked free on this
machine, and a number key like the "say something" shortcut, so no browser
shortcut is taken away the way ctrl+shift+m, v or d would -- toggles move mode,
the same as the tray's Move avatar. In move mode the whole window takes clicks
behind a faint tint: dragging moves it, the mouse wheel or + / - resize it
standing where it stood, and the shortcut again, Esc, Enter, the tray or hiding
end it. Otherwise every click passes through, and nothing is read per frame.

A real drag, wheel and Esc on this desktop are in test_avatar_live.py.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import yaml
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QAction, QHideEvent, QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from core.config import AppConfig, AvatarConfig
from core.logging import setup_logging
from core.settings import SETTINGS, apply, current_values
from modules.ui import avatar as body
from modules.ui import hotkey
from modules.ui.app import CompanionApp
from modules.ui.settings_dialog import SettingsDialog

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


qt = QApplication.instance() or QApplication(sys.argv)
K = Qt.Key

print("the shortcut")

check("ctrl+shift+2 in config.yaml, none in code, on the Avatar tab, after a restart",
      AvatarConfig().move_hotkey == "" and AppConfig.load(CONFIG_PATH).avatar.move_hotkey == "ctrl+shift+2"
      and any(s.key == "avatar.move_hotkey" and s.kind == "hotkey" and s.section == "Avatar" and not s.live
              for s in SETTINGS))
check("...a combination Windows can register",
      hotkey.parse("ctrl+shift+2") == (hotkey.MOD_CONTROL | hotkey.MOD_SHIFT | hotkey.MOD_NOREPEAT, ord("2")))
check("clicking the character and arrow keys are gone, as asked",
      not hasattr(AvatarConfig(), "click_to_select") and not hasattr(body, "key_action")
      and not any(s.key == "avatar.click_to_select" for s in SETTINGS))
page_cfg = AppConfig.load(CONFIG_PATH)
dialog = SettingsDialog(page_cfg, defaults=current_values(page_cfg), monitors=[(1, "Monitor 1")],
                        microphones=[("", "System default")])
field = dialog.widget("avatar.move_hotkey")
check("the settings page shows it as text", field.text() == "ctrl+shift+2", field.text())
field.setText(" Ctrl+Alt+M ")
apply(page_cfg, dialog.values())
check("...and one typed there is kept tidy", page_cfg.avatar.move_hotkey == "ctrl+alt+m", page_cfg.avatar.move_hotkey)

print("\nthe window")

standing = QRect(1591, 800, 400, 600)
taller = body.resized(standing, 650)
check("resized, it stands where it stood: same feet, same middle, 2:3",
      taller.bottom() == standing.bottom() and abs(taller.center().x() - standing.center().x()) <= 1
      and taller.width() == 433, str(taller.getRect()))

cfg = AppConfig.load(CONFIG_PATH)
window = body.AvatarWindow(cfg, Path("unused.model3.json"))
window.move(1591, 800)
check("never created with Qt's WindowTransparentForInput, which drops its mouse presses for good",
      not window._flags() & Qt.WindowType.WindowTransparentForInput)
switches, modes, resizes = [], [], []
real_switch = body.set_click_through
body.set_click_through = lambda hwnd, on: switches.append(on)
window.isVisible = lambda: True  # shown, as far as the window's own code can tell
window.moving_changed.connect(modes.append)
window.resized.connect(resizes.append)


def press(x, y):
    window.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x - window.x(), y - window.y()),
                                       QPointF(x, y), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier))


def drag_to(x, y):
    window.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, QPointF(0, 0), QPointF(x, y), Qt.MouseButton.NoButton,
                                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def release(x, y):
    window.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(0, 0), QPointF(x, y),
                                         Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                         Qt.KeyboardModifier.NoModifier))


def wheel(delta):
    window.wheelEvent(QWheelEvent(QPointF(200, 150), QPointF(1791, 950), QPoint(0, 0), QPoint(0, delta),
                                  Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                  Qt.ScrollPhase.NoScrollPhase, False))


def key(code):
    window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier))


def saved():
    path = Path(window.state.path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


press(1791, 950)
drag_to(1891, 900)
release(1891, 900)
wheel(120)
key(K.Key_Plus)
check("not in move mode: dragging, the wheel and keys do nothing",
      window.pos() == QPoint(1591, 800) and cfg.avatar.height == 600 and not resizes and not modes)

window.set_moving(True)
check("move mode on: the window takes clicks, switched in place, and says so",
      switches == [False] and modes == [True] and window.moving, f"{switches} {modes}")
press(1791, 950)
drag_to(1891, 900)
release(1891, 900)
check("dragging moves it with the pointer, and where it is is remembered",
      window.pos() == QPoint(1691, 750) and (saved().get("x"), saved().get("y")) == (1691, 750),
      f"{window.pos()} {saved()}")
bottom, middle = window.geometry().bottom(), window.geometry().center().x()
wheel(120)
check("a notch of the wheel up makes it 50 px taller, standing where it stood",
      cfg.avatar.height == 650 and window.height() == 650 and window.geometry().bottom() == bottom
      and abs(window.geometry().center().x() - middle) <= 1 and resizes == [650], window.geometry().getRect().__str__())
wheel(-120)
key(K.Key_Plus)
key(K.Key_Minus)
key(K.Key_Equal)
check("down shorter; + (or =) and - do the same", resizes == [650, 600, 650, 600, 650], str(resizes))
for _ in range(40):
    wheel(120)
check("...never taller than the setting allows", cfg.avatar.height == body.MAX_HEIGHT == 1400)
for _ in range(40):
    wheel(-120)
check("...nor smaller", cfg.avatar.height == body.MIN_HEIGHT == 200)
count = len(resizes)
wheel(-120)
check("a notch that changes nothing saves nothing", len(resizes) == count)
cfg.avatar.height = 600
window.apply_settings()
at = window.pos()
key(K.Key_Left)
check("arrow keys no longer move it", window.pos() == at)
key(K.Key_Escape)
check("Esc ends move mode: clicks pass through again, and the place is saved",
      not window.moving and modes == [True, False] and switches == [False, True]
      and (saved().get("x"), saved().get("y")) == (at.x(), at.y()), f"{modes} {switches}")
window.set_moving(True)
key(K.Key_Return)
check("...so does Enter", not window.moving)
window.set_moving(True)
window.hideEvent(QHideEvent())
check("...and hiding it", not window.moving)
body.set_click_through = real_switch
del window.isVisible


class FakeAvatar:
    def __init__(self):
        self.moving, self.calls = False, []

    def set_moving(self, on):
        self.calls.append(on)
        self.moving = on


notices = []


def app(avatar=True, hidden=False):
    config = AppConfig.load(CONFIG_PATH)
    config.avatar.hidden = hidden
    action = QAction("Move avatar")
    action.setCheckable(True)
    fake = SimpleNamespace(config=config, avatar=FakeAvatar() if avatar else None, _avatar_move_action=action,
                           window=SimpleNamespace(add_notice=lambda text, colour="": notices.append(text)))
    fake._set_avatar_moving = lambda on: CompanionApp._set_avatar_moving(fake, on)
    fake._toggle_avatar_moving = lambda: CompanionApp._toggle_avatar_moving(fake)
    return fake


print("\nthe app")

a = app()
CompanionApp._on_hotkey(a, hotkey.HOTKEY_MOVE_AVATAR)
check("the move shortcut turns move mode on", a.avatar.calls == [True], str(a.avatar.calls))
CompanionApp._on_hotkey(a, hotkey.HOTKEY_MOVE_AVATAR)
check("...and pressed again, off", a.avatar.calls == [True, False], str(a.avatar.calls))
toggled = []
a._avatar_move_action.toggled.connect(toggled.append)
CompanionApp._on_avatar_moving_changed(a, True)
on_tick = a._avatar_move_action.isChecked()
CompanionApp._on_avatar_moving_changed(a, False)
check("the tray's tick follows move mode, however it started or ended, without toggling it again",
      on_tick and not a._avatar_move_action.isChecked() and toggled == [])
hidden = app(hidden=True)
CompanionApp._on_hotkey(hidden, hotkey.HOTKEY_MOVE_AVATAR)
none = app(avatar=False)
CompanionApp._on_hotkey(none, hotkey.HOTKEY_MOVE_AVATAR)
check("with the avatar hidden or not there, the shortcut says so and moves nothing",
      hidden.avatar.calls == [] and len(notices) == 2 and "no avatar" in notices[-1], str(notices))

root = Path(tempfile.mkdtemp(prefix="companion-avatar-move-"))
app_cfg, base = AppConfig.load(CONFIG_PATH), AppConfig.load(CONFIG_PATH)
app_cfg.root = base.root = root
app_cfg.avatar.height = 650
problems = []
fake = SimpleNamespace(config=app_cfg, _base_config=lambda: base,
                       window=SimpleNamespace(add_notice=lambda text, colour="": problems.append(text)))
CompanionApp._on_avatar_resized(fake, 650)
written = yaml.safe_load((root / app_cfg.settings_file).read_text(encoding="utf-8"))
check("a size set with the wheel is saved like the Avatar height setting",
      written.get("avatar", {}).get("height") == 650 and not problems, str(written))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
