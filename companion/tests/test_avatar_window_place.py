"""The avatar takes the chat window's place while it is hidden.

Asked for: with a setting on, hiding the chat window shifts the avatar into the
window's place, and showing the window shifts it back. Reported: after hiding
the window and moving the avatar there by hand, showing the window left them
overlapping -- the setting must prevent that.

Hidden, the window's room is taken by gliding sideways, the avatar's middle
under the window's, on the same screen only. Shown, the avatar goes back where
it was; moved in between, it steps only as far sideways as it must to clear the
window. With the setting off nothing moves -- except an avatar put in the
window's place before it was switched off, which goes back rather than cover it.

Numbers are this machine's: a 2560x1440 screen with a 40 px taskbar, the window
docked bottom-right at 520x620 with a 24 px margin, the avatar 400x600 beside it.
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from core.config import AppConfig, AvatarConfig
from core.logging import setup_logging
from core.settings import SETTINGS
from modules.ui import avatar as body
from modules.ui.app import CompanionApp
from modules.ui.window import ChatWindow

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


qt = QApplication.instance() or QApplication(sys.argv)
cfg = AppConfig.load(CONFIG_PATH)
AREA = QRect(0, 0, 2560, 1400)
WINDOW = QRect(AREA.right() - cfg.ui.width - cfg.ui.margin, AREA.bottom() - cfg.ui.height - cfg.ui.margin,
               cfg.ui.width, cfg.ui.height)
HOME = QPoint(*body.default_position(AREA, cfg.ui, 400, 600))
SIZE = QRect(0, 0, 400, 600).size()


def rect(point):
    return QRect(point, SIZE)


print("where it goes")

check("its usual place is beside the window, not on it", not rect(HOME).intersects(WINDOW),
      f"avatar {rect(HOME).getRect()}, window {WINDOW.getRect()}")
into = body.into_window_place(rect(HOME), WINDOW, AREA)
check("window hidden: it shifts right into the window's place, its middle under the window's, at its own height",
      into.x() > HOME.x() and abs(rect(into).center().x() - WINDOW.center().x()) <= 1 and into.y() == HOME.y(),
      f"{HOME.x()} -> {into.x()}")
narrow_window = QRect(AREA.right() - 200, WINDOW.y(), 200, WINDOW.height())
edge = body.into_window_place(rect(HOME), narrow_window, AREA)
check("...never past the screen's edge", rect(edge).right() <= AREA.right(), str(rect(edge).getRect()))
check("clear of the window already: it stays", body.clear_of_window(rect(HOME), WINDOW, AREA) == HOME)
stepped = body.clear_of_window(rect(into), WINDOW, AREA)
check("on the window: it steps just clear, to the side that fits the screen",
      not rect(stepped).intersects(WINDOW) and rect(stepped).right() == WINDOW.left() - 1 and stepped.y() == into.y(),
      f"{into.x()} -> {stepped.x()}")
left_window = QRect(AREA.left() + 24, WINDOW.y(), 520, 620)
over = QPoint(100, HOME.y())
check("a window docked on the left: it steps to the right of it",
      body.clear_of_window(rect(over), left_window, AREA).x() == left_window.right() + 1)
small = QRect(0, 0, 700, 1400)
squeezed = body.clear_of_window(rect(QPoint(150, 800)), QRect(100, 800, 500, 600), small)
check("no room either side: kept on the screen", small.contains(rect(squeezed)), str(rect(squeezed).getRect()))

print("\ngliding")

window = body.AvatarWindow(cfg, Path("unused.model3.json"))
window.move(HOME)
window.slide_to(into)
check("not shown: it goes straight there", window.pos() == into and window.destination() == into)
window.move(HOME)
window.isVisible = lambda: True
window.slide_to(into)
running = window.destination() == into and window._slide.state() == window._slide.State.Running
window._slide.setCurrentTime(body.SLIDE_MS)
check(f"shown: it glides there over {body.SLIDE_MS} ms, knowing where it is going",
      running and window.pos() == into, str(window.pos()))

print("\nthe chat window says when it is shown or hidden")

chat = ChatWindow(cfg)
events = []
chat.shown.connect(lambda: events.append("shown"))
chat.hidden.connect(lambda: events.append("hidden"))
chat.show()
chat.hide()
chat.show()
check("shown, hidden, shown", events == ["shown", "hidden", "shown"], str(events))
chat.hide()


class FakeAvatar:
    failed = resized = moving_changed = SimpleNamespace(connect=lambda slot: None)

    def __init__(self, at=HOME):
        self.at, self.slides = QPoint(at), []

    def pos(self):
        return QPoint(self.at)

    def destination(self):
        return QPoint(self.at)

    def size(self):
        return SIZE

    def slide_to(self, point):
        self.slides.append(QPoint(point))
        self.at = QPoint(point)

    def place(self):
        pass

    def show(self):
        pass

    def isVisible(self):
        return True


def app(on=True, visible=True):
    config = AppConfig.load(CONFIG_PATH)
    config.avatar.take_window_place = on
    fake = SimpleNamespace(config=config, avatar=FakeAvatar(), _avatar_home=None, _avatar_slid_to=None,
                           window=SimpleNamespace(geometry=lambda: QRect(WINDOW), isVisible=lambda: visible,
                                                  add_notice=lambda text, colour="": None),
                           _screen_area=lambda r: QRect(AREA))
    fake._on_window_hidden = lambda: CompanionApp._on_window_hidden(fake)
    return fake


print("\nthe app")

check("off in code, on in config.yaml, and on the settings page",
      AvatarConfig().take_window_place is False and AppConfig.load(CONFIG_PATH).avatar.take_window_place is True
      and any(s.key == "avatar.take_window_place" and s.kind == "bool" and s.live for s in SETTINGS))

a = app()
CompanionApp._on_window_hidden(a)
check("window hidden: the avatar glides into its place", a.avatar.slides == [into], str(a.avatar.slides))
CompanionApp._on_window_shown(a)
check("window shown: it glides back where it was",
      bool(a.avatar.slides) and a.avatar.slides[-1] == HOME and a.avatar.at == HOME)

CompanionApp._on_window_hidden(a)
a.avatar.at = QPoint(into.x() + 60, into.y())  # moved by hand while the window was hidden
CompanionApp._on_window_shown(a)
check("moved by hand meanwhile: showing the window never leaves them overlapping -- the reported case",
      not rect(a.avatar.at).intersects(WINDOW) and a.avatar.at != HOME and rect(a.avatar.at).right() == WINDOW.left() - 1,
      str(rect(a.avatar.at).getRect()))

a.avatar.at = QPoint(HOME)
CompanionApp._on_window_hidden(a)
a.avatar.at = QPoint(300, HOME.y())  # moved far away by hand
slides = len(a.avatar.slides)
CompanionApp._on_window_shown(a)
check("moved well away: it stays where it was put", len(a.avatar.slides) == slides and a.avatar.at == QPoint(300, HOME.y()))

off = app(on=False)
CompanionApp._on_window_hidden(off)
CompanionApp._on_window_shown(off)
check("setting off: nothing moves", off.avatar.slides == [])

switched = app()
CompanionApp._on_window_hidden(switched)
switched.config.avatar.take_window_place = False
CompanionApp._on_window_shown(switched)
check("switched off while it stood in the window's place: it still goes back, rather than cover the window",
      switched.avatar.at == HOME, str(switched.avatar.at))

elsewhere = app()
elsewhere.avatar.at = QPoint(-1600, 800)  # on a second screen to the left
CompanionApp._on_window_hidden(elsewhere)
check("an avatar on another screen is left alone", elsewhere.avatar.slides == [])

started_hidden = app(visible=False)
started_hidden.avatar = None
started_hidden._sync_avatar_action = lambda: None
started_hidden.worker = SimpleNamespace(speech_level=lambda: 0.0, speech_shape=lambda: None)
started_hidden._on_avatar_failed = lambda message: None
started_hidden._on_avatar_resized = lambda height: None
started_hidden._on_avatar_moving_changed = lambda on: None
started_hidden.config.avatar.enabled = True
real = (body.problem, body.choose_model, body.AvatarWindow)
body.problem = lambda config: ""
body.choose_model = lambda folder, model: Path("haru.model3.json")
body.AvatarWindow = lambda config, path, level=None, shape=None: FakeAvatar()
CompanionApp._build_avatar(started_hidden)
body.problem, body.choose_model, body.AvatarWindow = real
check("the app started with the window hidden: the avatar takes its place from the start",
      started_hidden.avatar is not None and started_hidden.avatar.slides == [into], str(started_hidden.avatar.slides))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
