"""Hiding the avatar.

Asked for: a setting to hide the VTuber model. "Hide the avatar" (Features) and
"Hide avatar" in the tray are one switch, saved to settings.yaml like any
setting, so it stays hidden after a restart. Hidden, the avatar stays loaded --
it comes back at once -- and its drawing timer stops, so it costs no frames; a
setting changed while hidden doesn't start drawing again. Hiding ends move mode,
and "Move avatar" is offered only while the avatar can be seen.

Drawing for real, hidden and shown again, is checked in test_avatar_live.py.
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import yaml
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from core.config import AppConfig, AvatarConfig
from core.logging import setup_logging
from core.settings import SETTINGS, current_values
from modules.ui import avatar as body
from modules.ui.app import CompanionApp

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


qt = QApplication.instance() or QApplication(sys.argv)

print("the setting")

check("shown by default, in code and in config.yaml, and on the settings page, live",
      AvatarConfig().hidden is False and AppConfig.load(CONFIG_PATH).avatar.hidden is False
      and any(s.key == "avatar.hidden" and s.kind == "bool" and s.live for s in SETTINGS))

print("\nthe window")

cfg = AppConfig.load(CONFIG_PATH)
window = body.AvatarWindow(cfg, Path("unused.model3.json"))
check("before it is shown, its frame rate is set but nothing draws",
      window.timer.interval() == 1000 // cfg.avatar.fps and not window.timer.isActive(),
      f"interval {window.timer.interval()}, active {window.timer.isActive()}")
cfg.avatar.fps = 20
window.apply_settings()
check("a setting changed while it isn't shown takes effect without starting the drawing",
      window.timer.interval() == 50 and not window.timer.isActive())


class FakeAvatar:
    failed = resized = moving_changed = SimpleNamespace(connect=lambda slot: None)

    def __init__(self):
        self.visible, self.moving, self.calls = False, False, []

    def isVisible(self):
        return self.visible

    def show(self):
        self.visible = True
        self.calls.append("show")

    def hide(self):
        self.visible = False
        self.calls.append("hide")

    def set_moving(self, on):
        if on != self.moving:
            self.calls.append(("moving", on))
        self.moving = on

    def place(self):
        self.calls.append("place")

    def apply_settings(self):
        self.calls.append("settings")


root = Path(tempfile.mkdtemp(prefix="companion-avatar-hidden-"))
notices = []


def app(hidden=False):
    config, base = AppConfig.load(CONFIG_PATH), AppConfig.load(CONFIG_PATH)
    config.root = base.root = root
    config.avatar.enabled, config.avatar.hidden = True, hidden
    fake = SimpleNamespace(
        config=config, avatar=None, listener=None, ratings=None, _quiet_action=None,
        window=SimpleNamespace(add_notice=lambda text, colour="": notices.append(text),
                               apply_settings=lambda redock=False: None, isVisible=lambda: True),
        worker=SimpleNamespace(speech_level=lambda: 0.0, speech_shape=lambda: None, apply_settings=lambda: None),
        _avatar_move_action=QAction("Move avatar"), _avatar_hide_action=QAction("Hide avatar"),
        _base_config=lambda: base, _on_avatar_failed=lambda message: None,
        _on_avatar_resized=lambda height: None, _on_avatar_moving_changed=lambda on: None,
    )
    fake._avatar_move_action.setCheckable(True)
    fake._avatar_hide_action.setCheckable(True)
    fake._sync_avatar_action = lambda: CompanionApp._sync_avatar_action(fake)
    fake._sync_avatar_visibility = lambda: CompanionApp._sync_avatar_visibility(fake)
    return fake, base


real = (body.problem, body.choose_model, body.AvatarWindow)
body.problem = lambda config: ""
body.choose_model = lambda folder, model: Path("haru.model3.json")
body.AvatarWindow = lambda config, path, level=None, shape=None: FakeAvatar()


def saved():
    path = root / AppConfig.load(CONFIG_PATH).settings_file
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}


print("\nthe app")

shown, _ = app()
CompanionApp._build_avatar(shown)
check("switched on, it is built and shown, with Move and Hide in the tray",
      shown.avatar.calls == ["place", "show"] and shown._avatar_move_action.isVisible()
      and shown._avatar_hide_action.isVisible() and not shown._avatar_hide_action.isChecked(),
      str(shown.avatar.calls))

hidden, _ = app(hidden=True)
CompanionApp._build_avatar(hidden)
check("started hidden: built but not shown; Hide ticked, no Move offered",
      hidden.avatar.calls == ["place"] and hidden._avatar_hide_action.isChecked()
      and not hidden._avatar_move_action.isVisible(), str(hidden.avatar.calls))

avatar = shown.avatar
avatar.moving = True
shown._avatar_move_action.setChecked(True)
avatar.calls.clear()
CompanionApp._set_avatar_hidden(shown, True)
check("Hide avatar in the tray: move mode ends (saving its place), then it hides",
      avatar.calls == [("moving", False), "hide"], str(avatar.calls))
check("...the same avatar, not unloaded", shown.avatar is avatar)
check("...Move is no longer offered, and Hide is ticked",
      not shown._avatar_move_action.isVisible() and not shown._avatar_move_action.isChecked()
      and shown._avatar_hide_action.isChecked())
check("...and it is saved, so it stays hidden after a restart",
      shown.config.avatar.hidden and saved().get("avatar", {}).get("hidden") is True, str(saved()))

avatar.calls.clear()
CompanionApp._set_avatar_hidden(shown, False)
check("unticked, it shows again at once, and Move comes back",
      avatar.calls == ["show"] and shown._avatar_move_action.isVisible() and not shown._avatar_hide_action.isChecked(),
      str(avatar.calls))
check("...and nothing is left saved: shown is config.yaml's default", "avatar" not in saved(), str(saved()))

page, base = app()
CompanionApp._build_avatar(page)
page.avatar.calls.clear()
values = current_values(page.config)
values["avatar.hidden"] = True
CompanionApp._apply_settings(page, values, base)
check("the settings page's Hide the avatar does the same, without a restart",
      page.avatar.calls[-1] == "hide" and page._avatar_hide_action.isChecked()
      and saved().get("avatar", {}).get("hidden") is True, str(page.avatar.calls))

values["avatar.hidden"] = False
CompanionApp._apply_settings(page, values, base)
check("...and unticking it there shows it", page.avatar.calls[-1] == "show" and page.avatar.visible)

gone, _ = app()
CompanionApp._set_avatar_hidden(gone, True)
check("with no avatar built, hiding is only saved, and nothing is offered",
      gone.avatar is None and not gone._avatar_hide_action.isVisible() and not gone._avatar_move_action.isVisible())

body.problem, body.choose_model, body.AvatarWindow = real
check("no problem saving was reported along the way", not [n for n in notices if "Couldn't" in n], str(notices))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
