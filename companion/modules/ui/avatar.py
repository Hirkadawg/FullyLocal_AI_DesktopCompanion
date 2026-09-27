"""The avatar: a Live2D model standing on the desktop.

A frameless, transparent, always-on-top window that clicks pass straight
through, drawn with live2d-py (an MIT wrapper around Live2D's Cubism SDK, whose
Core is free for private use). Measured in the prototype on this machine: +76 MB
VRAM, and 61 fps at 8-12% of one core -- so it draws at `avatar.fps` (30), not
as fast as it can.

The mouth follows the loudness of the companion's voice as it is played
(`AudioPlayer.level`), the same RMS approach as live2d-py's own WavHandler.
Blinking and breathing are the model's own.

Clicks pass straight through it. Move mode -- the move shortcut (avatar.move_hotkey)
or "Move avatar" in the tray -- makes the whole window grabbable (a faint tint
shows it): drag it, and the mouse wheel or + / - resize it. The
shortcut again, Esc or the tray end it, and where it was left is remembered.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QCursor, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication

from core.logging import get_logger

log = get_logger(__name__)

MOUTH_PARAM = "ParamMouthOpenY"
FORM_PARAM = "ParamMouthForm"
#: Window width / height.
ASPECT = 2 / 3

# -- where it looks --------------------------------------------------------------
# live2d-py's Drag(x, y) takes window pixels and eases the head, eyes and body
# towards that point itself (measured: far left gave head -30, eyes -1, body -8).
# A point outside the window pins the head to its limit, so the mouse's distance
# from the face is scaled to the screen instead: only a pointer REACH of the
# screen's width away turns the head fully.

#: Where the face is in the window, as a share of its width and height.
FACE = (0.5, 0.2)
#: Distance from the face, as a share of the screen's width, that turns it fully.
REACH = 0.5
#: Once the mouse has rested this long, it looks back at the user.
LOOK_BACK_S = 5.0
#: It keeps looking at the user this long after its voice goes quiet.
SPEECH_HOLD_S = 1.0

# -- moods on the face -----------------------------------------------------------
# Haru ships no expression files, so a mood (core/mood.py) is set straight on the
# standard Cubism parameters most models share; one a model lacks is skipped.
# Tuned by eye on Haru's screenshots. Each: parameter -> (how, value):
#   "set"   ease from the model's default to the value;
#   "scale" multiply what blinking made of it, so the eyes still blink;
#   "add"   add to what following the mouse made of it.
EXPRESSIONS: dict[str, dict[str, tuple[str, float]]] = {
    "neutral": {},
    # The smiling eye shape only shows as the eyes close: with them open, "happy"
    # looked like neutral with a blush in the screenshots.
    "happy": {"ParamEyeLSmile": ("set", 1.0), "ParamEyeRSmile": ("set", 1.0),
              "ParamEyeLOpen": ("scale", 0.35), "ParamEyeROpen": ("scale", 0.35),
              "ParamTere": ("set", 0.6), "ParamBrowLY": ("set", 0.4), "ParamBrowRY": ("set", 0.4),
              "ParamMouthForm": ("set", 1.0)},
    "surprised": {"ParamEyeLOpen": ("scale", 1.6), "ParamEyeROpen": ("scale", 1.6),
                  "ParamBrowLY": ("set", 1.0), "ParamBrowRY": ("set", 1.0),
                  "ParamEyeBallForm": ("set", -0.8), "ParamMouthForm": ("set", -0.2)},
    "sad": {"ParamBrowLAngle": ("set", -1.0), "ParamBrowRAngle": ("set", -1.0),
            "ParamBrowLForm": ("set", -1.0), "ParamBrowRForm": ("set", -1.0),
            "ParamBrowLY": ("set", -0.3), "ParamBrowRY": ("set", -0.3),
            "ParamMouthForm": ("set", -1.0),
            "ParamEyeLOpen": ("scale", 0.75), "ParamEyeROpen": ("scale", 0.75)},
    "thinking": {"ParamBrowLY": ("set", 0.6), "ParamBrowRY": ("set", -0.4),
                 "ParamBrowLAngle": ("set", 0.5), "ParamMouthForm": ("set", -0.4),
                 "ParamEyeBallX": ("add", 0.4), "ParamEyeBallY": ("add", 0.5)},
}
#: A mood shows at least this long...
MOOD_HOLD_S = 4.0
#: ...and while it speaks, until this long after.
MOOD_AFTER_SPEECH_S = 2.0
#: Every parameter a mood "sets". live2d-py keeps what is set, so these are
#: written back to their defaults whenever no mood uses them -- measured: a
#: blush set for "happy" was still on the face in the next mood's screenshot.
_SET_PARAMS = sorted({pid for table in EXPRESSIONS.values()
                      for pid, (how, _) in table.items() if how == "set"})
_MOOD_PARAMS = sorted({pid for table in EXPRESSIONS.values() for pid in table})

_live2d_started = False


def _full(config, path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else config.root / p


def find_models(folder: Path) -> list[Path]:
    """Every Live2D model (.model3.json) under the folder, in name order."""
    folder = Path(folder)
    return sorted(folder.rglob("*.model3.json")) if folder.is_dir() else []


def choose_model(folder: Path, name: str = "") -> Path | None:
    """The named model, or the first one found when no name is given."""
    models = find_models(folder)
    if name:
        wanted = name.lower()
        return next((m for m in models if m.name.lower() in (wanted, wanted + ".model3.json")), None)
    return models[0] if models else None


def problem(config) -> str | None:
    """Why the avatar can't be shown, in the user's words -- or None."""
    avatar = config.avatar
    folder = _full(config, avatar.folder)
    if choose_model(folder, avatar.model) is None:
        if avatar.model:
            return f"The avatar model {avatar.model!r} isn't in {folder}."
        return f"No Live2D model (a .model3.json file) in {folder}, so there is no avatar to show."
    try:
        import live2d.v3  # noqa: F401
    except ImportError:
        return "live2d-py isn't installed, so the avatar can't be drawn."
    return None


def _live2d():
    """live2d.v3, started once per process."""
    global _live2d_started
    import live2d.v3 as live2d

    if not _live2d_started:
        live2d.init()
        _live2d_started = True
    return live2d


def shutdown_live2d() -> None:
    global _live2d_started
    if _live2d_started:
        import live2d.v3 as live2d

        live2d.dispose()
        _live2d_started = False


class MouthFollower:
    """The voice's loudness -> how open the mouth is, 0-1.

    Opens quickly and closes a little slower, so it follows syllables without
    flickering on every audio block.
    """

    def __init__(self, gain: float = 8.0, opening: float = 0.6, closing: float = 0.35) -> None:
        self.gain = gain
        self.opening = opening
        self.closing = closing
        self.value = 0.0

    def step(self, level: float) -> float:
        target = min(1.0, max(0.0, level * self.gain))
        rate = self.opening if target > self.value else self.closing
        self.value += (target - self.value) * rate
        if self.value < 0.01:
            self.value = 0.0
        return self.value


class MouthShaper:
    """Speech sound and loudness -> (how open, form) of the mouth.

    With the sound playing known (Piper's phoneme timings), its shape
    (modules/voice/visemes.py); without, loudness alone and a neutral form. Both
    ease towards their target, so a 30 ms sound still shows without jitter.
    """

    def __init__(self, gain: float = 8.0, rate: float = 0.55) -> None:
        self.loudness = MouthFollower(gain)
        self.rate = rate
        self.open = 0.0
        self.form = 0.0

    @property
    def value(self) -> float:
        return self.open

    def step(self, shape: str | None, level: float, rest_form: float = 0.0) -> tuple[float, float]:
        """`rest_form` is the mouth's form when no sound shapes it: the model's
        own default (Haru's is a slight smile, 1), or the mood's."""
        from modules.voice.visemes import REST, SHAPES

        loud = self.loudness.step(level)
        if shape in SHAPES and shape != REST:
            target_open, target_form = SHAPES[shape]
        elif shape == REST:
            target_open, target_form = 0.0, rest_form
        else:
            target_open, target_form = loud, rest_form
        self.open += (target_open - self.open) * self.rate
        self.form += (target_form - self.form) * self.rate
        if self.open < 0.01:
            self.open = 0.0
        if abs(self.form) < 0.01:
            self.form = 0.0
        return self.open, self.form


def default_position(area, ui, width: int, height: int) -> tuple[int, int]:
    """Standing on the taskbar beside the chat window's corner, not on top of it."""
    beside = ui.width + 2 * ui.margin
    x = area.left() + beside if "left" in ui.corner else area.right() - beside - width
    return x, area.bottom() - height + 1


# -- the chat window's place --------------------------------------------
# Hidden, the chat window leaves room: the avatar glides sideways into its place.
# Shown again, the avatar goes back -- or, if it was moved in between, steps only
# as far sideways as it must to stop overlapping the window.

#: How long the glide takes.
SLIDE_MS = 300


def into_window_place(avatar: QRect, window: QRect, area: QRect) -> QPoint:
    """Where the avatar stands in the hidden window's place: moved only sideways,
    its middle under the window's, kept on the screen."""
    x = window.center().x() - avatar.width() // 2
    x = max(area.left(), min(x, area.right() - avatar.width() + 1))
    return QPoint(x, avatar.y())


def clear_of_window(avatar: QRect, window: QRect, area: QRect) -> QPoint:
    """The nearest place, moving only sideways, where the avatar doesn't overlap
    the window; where it is already when it doesn't."""
    if not avatar.intersects(window):
        return avatar.topLeft()
    left = window.left() - avatar.width()  # its right edge just left of the window
    right = window.right() + 1  # its left edge just right of it
    fitting = [x for x in (left, right) if area.left() <= x and x + avatar.width() - 1 <= area.right()]
    if fitting:
        return QPoint(min(fitting, key=lambda x: abs(x - avatar.x())), avatar.y())
    # No room beside it on either side: the roomier side, kept on the screen.
    x = left if window.left() - area.left() >= area.right() - window.right() else right
    return QPoint(max(area.left(), min(x, area.right() - avatar.width() + 1)), avatar.y())


# -- moving it ----------------------------------------------------------------
# First built as "click the character, then arrow keys", which needed the pixel under
# the pointer read back every few frames to tell the character from empty space. The
# user asked instead for a shortcut that turns moving on, then click and drag. Move
# mode makes the whole window take clicks; otherwise they pass through, with nothing
# read per frame. Both are the Windows style switched in place (set_click_through) --
# never Qt's WindowTransparentForInput, with which Qt drops the window's mouse presses
# even while Windows delivers them.

#: In move mode the mouse wheel, or + / -, changes its height this much, within the
#: Avatar height setting's range.
RESIZE_STEP = 50
MIN_HEIGHT, MAX_HEIGHT = 200, 1400

GWL_EXSTYLE, WS_EX_TRANSPARENT = -20, 0x20


def _code(key) -> int:
    return int(getattr(key, "value", key))


def resized(rect: QRect, height: int) -> QRect:
    """The avatar's rectangle at a new height, still standing where it stood:
    same bottom, same middle."""
    width = int(round(height * ASPECT))
    return QRect(rect.center().x() - width // 2, rect.bottom() - height + 1, width, height)


def set_click_through(hwnd: int, on: bool) -> None:
    """Let clicks pass through a window (on), or take them (off), in place: Qt's
    WindowTransparentForInput flag would rebuild the window and reload the model."""
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    wanted = style | WS_EX_TRANSPARENT if on else style & ~WS_EX_TRANSPARENT
    if wanted != style:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, wanted)


def gaze_point(cursor: tuple[int, int], window, screen, still_s: float,
               speaking: bool) -> tuple[float, float]:
    """The point for Drag, in window pixels: towards the mouse, or straight
    ahead (the window's centre) when the mouse rests or it is speaking."""
    width, height = window.width(), window.height()
    centre = (width / 2, height / 2)
    if speaking or still_s >= LOOK_BACK_S:
        return centre
    reach = max(1.0, screen.width() * REACH)
    face_x = window.x() + width * FACE[0]
    face_y = window.y() + height * FACE[1]
    across = max(-1.0, min(1.0, (cursor[0] - face_x) / reach))
    down = max(-1.0, min(1.0, (cursor[1] - face_y) / reach))
    # Full turn at half the window's shorter side from the centre.
    half = min(width, height) / 2
    return centre[0] + across * half, centre[1] + down * half


def expression_values(mood: str, weight: float, current: dict[str, float],
                      defaults: dict[str, float]) -> tuple[dict[str, float], float | None]:
    """The parameter values for `mood` shown at `weight` (0-1), and the mouth's
    resting form.

    `current` is what the model's own update made of each parameter (blinking,
    following the mouse); `defaults` the model's defaults, for the parameters it
    has. The mouth's form is returned apart, for the mouth to use when silent.
    """
    table = EXPRESSIONS.get(mood, {})
    values: dict[str, float] = {}
    for pid in _SET_PARAMS:
        if pid == FORM_PARAM or pid not in defaults:
            continue
        how, target = table.get(pid, ("set", defaults[pid]))
        if how == "set":
            values[pid] = defaults[pid] + (target - defaults[pid]) * weight
    for pid, (how, target) in table.items():
        if pid == FORM_PARAM or pid not in defaults or pid in values:
            continue
        now = current.get(pid, defaults[pid])
        if how == "scale":
            values[pid] = now * (1.0 + (target - 1.0) * weight)
        elif how == "add":
            values[pid] = now + target * weight
    rest_form = None
    if FORM_PARAM in defaults:
        _, target = table.get(FORM_PARAM, ("set", defaults[FORM_PARAM]))
        rest_form = defaults[FORM_PARAM] + (target - defaults[FORM_PARAM]) * weight
    return values, rest_form


class FaceMood:
    """Which mood the face shows, and how strongly: fades in, holds, fades out,
    and fades one mood out before the next comes in."""

    def __init__(self, hold_s: float = MOOD_HOLD_S, after_speech_s: float = MOOD_AFTER_SPEECH_S,
                 rate: float = 0.15) -> None:
        self.hold_s = hold_s
        self.after_speech_s = after_speech_s
        self.rate = rate
        self.shown = "neutral"
        self.wanted = "neutral"
        self.weight = 0.0
        self.until = 0.0

    def show(self, mood: str, now: float) -> None:
        self.wanted = mood if mood in EXPRESSIONS else "neutral"
        self.until = now + self.hold_s

    def step(self, now: float, speaking: bool) -> tuple[str, float]:
        if speaking and self.wanted != "neutral":
            self.until = max(self.until, now + self.after_speech_s)
        if now >= self.until:
            self.wanted = "neutral"
        if self.shown != self.wanted:
            self.weight = max(0.0, self.weight - self.rate)
            if self.weight == 0.0:
                self.shown = self.wanted
        elif self.shown != "neutral":
            self.weight = min(1.0, self.weight + self.rate)
        return self.shown, self.weight


class AvatarState:
    """Where the avatar was left, in a small JSON file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def position(self) -> tuple[int, int] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return int(data["x"]), int(data["y"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save_position(self, x: int, y: int) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            log.warning("could not save the avatar's position", exc_info=True)


class AvatarWindow(QOpenGLWidget):
    #: Drawing failed; the message says why.
    failed = Signal(str)
    #: Resized in move mode. Payload: the new height, already in the config.
    resized = Signal(int)
    #: Move mode started (True) or ended (False), however it happened.
    moving_changed = Signal(bool)

    def __init__(self, config, model_path: Path, level: Callable[[], float] = lambda: 0.0,
                 shape: Callable[[], str | None] = lambda: None, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.model_path = Path(model_path)
        self.level = level
        self.shape = shape
        self.mouth = MouthShaper()
        self.model = None
        self.frames = 0
        self.moving = False
        # Once clicked in move mode it has the keyboard: + / - and Esc.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._has_mouth = False
        self._has_form = False
        # Where it looks: the mouse, read through these so tests can drive them.
        self.cursor = QCursor.pos
        self.clock = time.monotonic
        self._last_cursor: tuple[int, int] | None = None
        self._moved_at = 0.0
        self._spoke_at = -1e9
        # Moods: what the face shows, and the model's parameters they touch.
        self.face = FaceMood()
        self._param_index: dict[str, int] = {}
        self._defaults: dict[str, float] = {}
        self._drag_from: QPoint | None = None
        self.state = AvatarState(_full(config, config.avatar.state_file))

        surface = QSurfaceFormat()
        surface.setAlphaBufferSize(8)
        self.setFormat(surface)
        self.setWindowTitle("Companion avatar")
        self.setWindowFlags(self._flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.timer = QTimer(self)
        # Precise: a coarse timer on Windows rounds 33 ms up to the next 15.6 ms
        # tick, and 30 fps measured 21.
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.update)
        # Gliding into the hidden chat window's place and back.
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.apply_settings()

    def _flags(self) -> Qt.WindowType:
        # Never Qt's WindowTransparentForInput. Reported "not working", then
        # measured with a real click: with that flag Qt drops the window's mouse
        # presses even while Windows delivers them, so the avatar could never be
        # selected. Clicks pass through by the Windows style instead
        # (set_click_through), switched for move mode.
        return (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool)

    # -- settings and place ------------------------------------------------------

    def apply_settings(self) -> None:
        """Size and frame rate from the settings; they can change while running.
        The drawing timer runs only while the avatar is shown."""
        height = int(self.config.avatar.height)
        self.resize(int(round(height * ASPECT)), height)
        self.timer.setInterval(max(1, int(1000 / max(1, int(self.config.avatar.fps)))))
        if self.isVisible():
            self.timer.start()

    def place(self) -> None:
        """Where it was left, if that is still on a screen; else beside the chat window."""
        screens = QApplication.screens()
        saved = self.state.position()
        if saved is not None and any(s.geometry().contains(QPoint(*saved)) for s in screens):
            self.move(*saved)
            return
        index = max(0, min(self.config.monitor_index - 1, len(screens) - 1))
        area = screens[index].availableGeometry()
        self.move(*default_position(area, self.config.ui, self.width(), self.height()))

    def set_moving(self, on: bool) -> None:
        """Move mode on: the whole window takes clicks, to drag it (a faint tint
        shows it), and the wheel resizes it. Off: clicks pass through. Leaving it
        saves the place."""
        if on == self.moving:
            return
        self.moving = on
        self._slide.stop()  # a glide under the pointer would fight the drag
        self._drag_from = None
        # Switched in place: rebuilding the window with other flags, as this once
        # did, reloaded the model.
        if self.isVisible():
            set_click_through(int(self.winId()), not on)
        if not on:
            self.state.save_position(self.x(), self.y())
        self.update()
        self.moving_changed.emit(on)

    def slide_to(self, point: QPoint) -> None:
        """Glide to `point` when shown; go straight there when not."""
        self._slide.stop()
        if not self.isVisible():
            self.move(point)
            return
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(point)
        self._slide.start()

    def destination(self) -> QPoint:
        """Where it is gliding to, or where it is."""
        if self._slide.state() == QAbstractAnimation.State.Running:
            return self._slide.endValue()
        return self.pos()

    # -- looking -----------------------------------------------------------------

    def gaze_target(self) -> tuple[float, float]:
        """Where the head and eyes should point now, in window pixels."""
        now = self.clock()
        if self.level() > 0.005:
            self._spoke_at = now
        if not self.config.avatar.follow_mouse:
            return self.width() / 2, self.height() / 2
        point = self.cursor()
        position = (point.x(), point.y())
        if position != self._last_cursor:
            self._last_cursor, self._moved_at = position, now
        screen = self.screen() or QApplication.primaryScreen()
        return gaze_point(position, self.geometry(), screen.geometry(),
                          still_s=now - self._moved_at,
                          speaking=now - self._spoke_at < SPEECH_HOLD_S)

    # -- moods ---------------------------------------------------------------------

    def show_mood(self, mood: str) -> None:
        """Show a mood (core/mood.py) on the face, when expressions are on."""
        if self.config.avatar.expressions:
            self.face.show(mood, self.clock())

    def _apply_mood(self) -> float | None:
        """Set this frame's mood parameters after the model's update; returns
        the mouth's resting form for the mood, or None."""
        if not self._defaults:
            return None
        if not self.config.avatar.expressions:
            self.face.wanted = "neutral"
        now = self.clock()
        mood, weight = self.face.step(now, speaking=now - self._spoke_at < SPEECH_HOLD_S)
        current = {pid: self.model.GetParameterValue(i) for pid, i in self._param_index.items()}
        values, rest_form = expression_values(mood, weight, current, self._defaults)
        for pid, value in values.items():
            self.model.SetParameterValue(pid, value, 1.0)
        return rest_form

    # -- drawing -----------------------------------------------------------------

    def initializeGL(self) -> None:
        # Called again if Qt ever gives the window a new context: the old one is
        # gone, so the model is loaded afresh into the new one.
        try:
            live2d = _live2d()
            live2d.glInit()
            self.model = live2d.LAppModel()
            self.model.LoadModelJson(str(self.model_path))
            self.model.SetAutoBlinkEnable(True)
            self.model.SetAutoBreathEnable(True)
            ids = list(self.model.GetParamIds())
            params = set(ids)
            self._has_mouth = MOUTH_PARAM in params
            self._has_form = FORM_PARAM in params
            wanted = set(_MOOD_PARAMS) | {FORM_PARAM}
            self._param_index = {pid: i for i, pid in enumerate(ids) if pid in wanted}
            self._defaults = {pid: float(self.model.GetParameter(i).default)
                              for pid, i in self._param_index.items()}
            self.resizeGL(self.width(), self.height())
        except Exception as exc:
            self.model = None
            log.warning("avatar: could not load %s", self.model_path, exc_info=True)
            QTimer.singleShot(0, lambda: self.failed.emit(f"The avatar couldn't be drawn: {exc}"))

    def resizeGL(self, w: int, h: int) -> None:
        if self.model is not None:
            dpr = self.devicePixelRatioF()
            self.model.Resize(int(w * dpr), int(h * dpr))

    def paintGL(self) -> None:
        import OpenGL.GL as GL

        if self.moving:
            GL.glClearColor(0.06, 0.1, 0.18, 0.18)  # premultiplied: a faint blue pane
        else:
            GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if self.model is None:
            return
        x, y = self.gaze_target()
        dpr = self.devicePixelRatioF()
        self.model.Drag(x * dpr, y * dpr)  # eased by the model itself
        self.model.Update()
        rest_form = self._defaults.get(FORM_PARAM, 0.0)
        mood_form = self._apply_mood()
        if mood_form is not None:
            rest_form = mood_form
        if self._has_mouth:
            opened, form = self.mouth.step(self.shape(), self.level(), rest_form)
            self.model.SetParameterValue(MOUTH_PARAM, opened, 1.0)
            if self._has_form:
                self.model.SetParameterValue(FORM_PARAM, form, 1.0)
        self.model.Draw()
        self.frames += 1

    def dispose(self) -> None:
        self.timer.stop()
        if self.model is not None and self.context() is not None:
            self.makeCurrent()
            self.model = None
            self.doneCurrent()
        self.model = None
        self.close()

    # -- moving --------------------------------------------------------------------

    def _resize_by(self, step: int) -> None:
        """Taller or shorter by `step`, within the Avatar height setting's range,
        standing where it stood."""
        height = max(MIN_HEIGHT, min(MAX_HEIGHT, int(self.config.avatar.height) + step))
        if height == int(self.config.avatar.height):
            return
        geometry = resized(self.geometry(), height)
        self.config.avatar.height = height
        self.setGeometry(geometry)
        self.state.save_position(geometry.x(), geometry.y())
        self.resized.emit(height)

    def wheelEvent(self, event) -> None:
        """In move mode the mouse wheel resizes it: up taller, down shorter."""
        notches = event.angleDelta().y()
        if self.moving and notches:
            self._resize_by(RESIZE_STEP if notches > 0 else -RESIZE_STEP)
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        """In move mode, once clicked: + / - resize it; Esc or Enter end move mode."""
        key = _code(event.key())
        if not self.moving:
            super().keyPressEvent(event)
        elif key in (_code(Qt.Key.Key_Plus), _code(Qt.Key.Key_Equal)):
            self._resize_by(RESIZE_STEP)
        elif key in (_code(Qt.Key.Key_Minus), _code(Qt.Key.Key_Underscore)):
            self._resize_by(-RESIZE_STEP)
        elif key in (_code(Qt.Key.Key_Escape), _code(Qt.Key.Key_Return), _code(Qt.Key.Key_Enter)):
            self.set_moving(False)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if self.moving and event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_from is not None:
            self._drag_from = None
            self.state.save_position(self.x(), self.y())

    def hideEvent(self, event) -> None:
        self.timer.stop()
        self.set_moving(False)
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self.apply_settings()
        self.timer.start()
        super().showEvent(event)
        # Click-through from the first moment, unless it is being moved.
        set_click_through(int(self.winId()), not self.moving)
