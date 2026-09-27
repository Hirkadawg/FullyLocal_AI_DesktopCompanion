"""The avatar drawn for real, on this desktop.

Needs a display, a GPU and a Live2D model in the configured avatars folder (it
says so and stops if there is none). Shows the avatar for a few seconds and
checks, with screen grabs, that it is drawn and that the desktop shows through
around it; that it holds the configured frame rate at a measured CPU cost; that
its mouth opens with a voice; and that clicks pass through it, while move mode
takes them all over and still draws.

It also moves the mouse pointer onto the avatar for a moment, in move mode, for
a real drag, one notch of the wheel and Esc -- pressing only once Windows
confirms the point is the avatar's, and Esc only if the avatar has the keyboard
-- and puts the pointer back. Calling the event handlers directly once passed
while real clicks never reached them.
"""

import sys
import time

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import mss
import numpy as np
from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer  # noqa: F401
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from modules.ui import avatar as body

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)
model = body.choose_model(body._full(cfg, cfg.avatar.folder), cfg.avatar.model)
if model is None:
    print(f"  no Live2D model in {body._full(cfg, cfg.avatar.folder)} -- nothing to draw")
    sys.exit(0)

qt = QApplication.instance() or QApplication(sys.argv)
voice = {"level": 0.0, "shape": None}
window = body.AvatarWindow(cfg, model, level=lambda: voice["level"], shape=lambda: voice["shape"])
window.place()


def wait(seconds):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def grab():
    g = window.geometry()
    with mss.MSS() as sct:
        return np.array(sct.grab({"left": g.x(), "top": g.y(), "width": g.width(), "height": g.height()}))[:, :, :3].astype(int)


before = grab()
window.show()
wait(2.0)
check("the model loads and draws", window.model is not None and window.frames > 0, f"{window.frames} frames")

frames, wall, cpu = window.frames, time.perf_counter(), time.process_time()
wait(4.0)
seconds = time.perf_counter() - wall
fps = (window.frames - frames) / seconds
cpu_percent = 100 * (time.process_time() - cpu) / seconds
print(f"    measured at {cfg.avatar.fps} fps setting: {fps:.1f} fps, {cpu_percent:.1f}% of one core")
check(f"it holds about the configured {cfg.avatar.fps} fps", abs(fps - cfg.avatar.fps) <= 5, f"{fps:.1f}")

shown = grab()
h, w = shown.shape[:2]
edge = float(np.abs(shown[:20, :20] - before[:20, :20]).mean())
body_diff = float(np.abs(shown[h // 4: 3 * h // 4, w // 3: 2 * w // 3] - before[h // 4: 3 * h // 4, w // 3: 2 * w // 3]).mean())
check("the desktop shows through around it, and it is drawn in the middle",
      edge < 8 and body_diff > 20, f"corner diff {edge:.1f}, middle diff {body_diff:.1f}")

opened = []
voice["level"] = 0.12
wait(0.6)
opened.append(window.mouth.value)
voice["level"] = 0.0
wait(1.0)
check("the mouth opens with a voice and closes after it",
      opened[0] > 0.8 and window.mouth.value == 0.0, f"open {opened[0]:.2f}, after {window.mouth.value:.2f}")

voice["shape"], voice["level"] = "i", 0.1
wait(0.5)
wide = (window.mouth.open, window.mouth.form)
voice["shape"] = "u"
wait(0.5)
rounded = window.mouth.form
voice["shape"], voice["level"] = None, 0.0
check("a speech sound's shape reaches the model while it draws: 'i' wide, 'u' rounded",
      wide[1] > 0.9 and rounded < -0.9 and window._has_form, f"i {wide}, u form {rounded:.2f}")

cfg.avatar.follow_mouse = True
g = window.geometry()
ids = list(window.model.GetParamIds())


def param(name):
    return window.model.GetParameterValue(ids.index(name)) if name in ids else 0.0


window.cursor = lambda: QPoint(g.x() - 3000, g.y() + g.height() // 5)
wait(3.0)
left = (param("ParamAngleX"), param("ParamEyeBallX"))
window.cursor = lambda: QPoint(g.right() + 3000, g.y() + g.height() // 5)
wait(3.0)
right = (param("ParamAngleX"), param("ParamEyeBallX"))
cfg.avatar.follow_mouse = False
wait(3.0)
ahead = param("ParamAngleX")
check("the head and eyes turn towards the pointer, and back ahead when following is off",
      left[0] < -15 and left[1] < -0.5 and right[0] > 15 and right[1] > 0.5 and abs(ahead) < 12,
      f"left {left[0]:.1f}/{left[1]:.2f}, right {right[0]:.1f}/{right[1]:.2f}, ahead {ahead:.1f}")

cfg.avatar.expressions = True
window.face.hold_s = 1.5
window.show_mood("surprised")
eyes = brows = 0.0
for _ in range(12):
    wait(0.1)
    eyes, brows = max(eyes, param("ParamEyeLOpen")), max(brows, param("ParamBrowLY"))
wait(3.0)
after = (param("ParamBrowLY"), param("ParamEyeBallForm"))
check("a mood shows on the drawn face and goes after its time: surprised widens the eyes and raises the brows",
      eyes > 1.2 and brows > 0.7 and abs(after[0]) < 0.1 and abs(after[1]) < 0.1,
      f"eyes {eyes:.2f}, brows {brows:.2f}, after {after[0]:.2f}/{after[1]:.2f}")

import ctypes  # noqa: E402


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]


def send_input(kind, **fields):
    item = INPUT()
    item.type = kind
    if kind == 0:
        item.u.mi = MOUSEINPUT(0, 0, fields.get("data", 0), fields["flags"], 0, 0)
    else:
        item.u.ki = KEYBDINPUT(fields["vk"], 0, fields.get("flags", 0), 0, 0)
    user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))


g, hwnd = window.geometry(), int(window.winId())


def spot(fx, fy):
    return QPoint(int(g.x() + g.width() * fx), int(g.y() + g.height() * fy))


def clicks_go_to(point):
    """Which window a click at this point would reach, as Windows decides it."""
    found = user32.GetAncestor(user32.WindowFromPoint(POINT(point.x(), point.y())), 2)
    return "avatar" if found == hwnd else "behind"


face = spot(0.5, 0.2)
through = (clicks_go_to(face), clicks_go_to(spot(0.03, 0.03)))
check("not in move mode, clicks pass through it everywhere, the face too", through == ("behind", "behind"),
      str(through))

# Move mode, as the shortcut turns it on: a real drag, a notch of the wheel, then Esc.
window.cursor = QCursor.pos
home = POINT()
user32.GetCursorPos(ctypes.byref(home))
start_pos, start_height = window.pos(), cfg.avatar.height
dragged = grown = ended = None
window.set_moving(True)
wait(0.3)
takes = clicks_go_to(face)
try:
    if takes == "avatar":
        user32.SetCursorPos(face.x(), face.y())
        wait(0.2)
        send_input(0, flags=0x2)  # left button down on the face
        for step in range(1, 13):  # 120 px to the right, in steps, as a hand drags
            user32.SetCursorPos(face.x() + 10 * step, face.y())
            wait(0.02)
        send_input(0, flags=0x4)  # let go
        wait(0.3)
        dragged = window.x() - start_pos.x()
        send_input(0, flags=0x0800, data=120)  # one notch of the wheel, up
        wait(0.3)
        grown = cfg.avatar.height - start_height
        if user32.GetForegroundWindow() == hwnd:
            send_input(1, vk=0x1B)  # Esc
            send_input(1, vk=0x1B, flags=0x2)
            wait(0.3)
            ended = not window.moving
finally:
    user32.SetCursorPos(home.x, home.y)
    window.set_moving(False)
check("move mode takes clicks; a real drag moves it 120 px and a notch of the wheel makes it 50 px taller",
      takes == "avatar" and dragged is not None and abs(dragged - 120) <= 2 and grown == 50,
      f"takes clicks: {takes}, dragged {dragged}, grew {grown}")
check("...and a real Esc ends move mode", ended is True, str(ended))
cfg.avatar.height = start_height
window.apply_settings()
window.move(start_pos)
window.cursor = lambda: QPoint(g.x() - 600, g.y())

start, frames = window.pos(), window.frames
target = QPoint(start.x() + 250, start.y())
window.slide_to(target)
wait(0.1)
midway = window.pos()
wait(body.SLIDE_MS / 1000 + 0.3)
check("it glides sideways to a place, drawing all the way, and arrives",
      start.x() < midway.x() < target.x() and window.pos() == target and window.frames - frames >= 10,
      f"{start.x()} -> {midway.x()} -> {window.pos().x()} (wanted {target.x()}), {window.frames - frames} frames")
window.slide_to(start)
wait(body.SLIDE_MS / 1000 + 0.3)

loaded = window.model
window.hide()
frames = window.frames
wait(1.0)
cfg.avatar.height += 50
window.apply_settings()  # a setting changed while hidden must not start drawing
wait(0.5)
hidden = (window.frames - frames, window.timer.isActive())
cfg.avatar.height -= 50
window.apply_settings()
window.show()
wait(1.0)
check("hidden, it draws nothing, even when a setting changes; shown again, it draws without reloading the model",
      hidden == (0, False) and window.frames > frames and window.model is loaded,
      f"hidden: {hidden[0]} frames, timer {hidden[1]}; shown: {window.frames - frames} frames")

loaded = window.model
window.set_moving(True)
frames = window.frames
wait(1.0)
g, hwnd = window.geometry(), int(window.winId())
corner_now = QPoint(g.x() + 5, g.y() + 5)
moving_corner = clicks_go_to(corner_now)
check("move mode takes clicks all over, still drawing the same model",
      window.model is loaded and window.frames > frames and moving_corner == "avatar",
      f"{window.frames - frames} frames, corner -> {moving_corner}")
window.set_moving(False)
wait(0.5)
check("back to click-through, still drawing", window.model is loaded and clicks_go_to(corner_now) == "behind")

window.dispose()
body.shutdown_live2d()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
