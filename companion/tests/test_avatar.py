"""The avatar's logic, without drawing it.

Models are found in their folder; a missing model or library is explained, not
crashed on; the mouth follows the voice's loudness, opening fast and closing
slower; the default place is beside the chat window, not on it; where it was
moved is remembered; the audio player reports the loudness of what it plays and
the worker hands it on; the window is click-through except in move mode; and the
app builds the avatar only when switched on.
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from modules.ui import avatar as body
from modules.ui.app import CompanionApp
from modules.ui.worker import CompanionWorker
from modules.voice.player import AudioPlayer

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("finding a model")

folder = Path(tempfile.mkdtemp(prefix="companion-avatar-"))
(folder / "haru" / "runtime").mkdir(parents=True)
(folder / "haru" / "runtime" / "haru.model3.json").write_text("{}", encoding="utf-8")
(folder / "mao").mkdir()
(folder / "mao" / "mao_pro.model3.json").write_text("{}", encoding="utf-8")
(folder / "mao" / "notes.txt").write_text("not a model", encoding="utf-8")
check("every .model3.json in the folder, however deep, in name order",
      [p.name for p in body.find_models(folder)] == ["haru.model3.json", "mao_pro.model3.json"])
check("with no name, the first is chosen", body.choose_model(folder).name == "haru.model3.json")
check("a name picks that one, with or without .model3.json",
      body.choose_model(folder, "mao_pro").name == "mao_pro.model3.json"
      and body.choose_model(folder, "MAO_PRO.model3.json") is not None)
check("an unknown name or an empty folder gives nothing",
      body.choose_model(folder, "hiyori") is None and body.choose_model(folder / "missing") is None)

cfg = AppConfig.load(CONFIG_PATH)
cfg.avatar.folder = str(folder / "empty")
check("no model: explained in words", "No Live2D model" in (body.problem(cfg) or ""), str(body.problem(cfg)))
cfg.avatar.folder, cfg.avatar.model = str(folder), "hiyori"
check("a named model that isn't there: named in the explanation", "'hiyori'" in (body.problem(cfg) or ""))
cfg.avatar.model = ""
check("a model and live2d-py present: no problem", body.problem(cfg) is None, str(body.problem(cfg)))

print("\nthe mouth")

mouth = body.MouthFollower()
check("silence keeps it shut", all(mouth.step(0.0) == 0.0 for _ in range(10)))
opened = [mouth.step(0.15) for _ in range(6)]
check("speech opens it within a few frames", opened[2] > 0.8 and opened[-1] <= 1.0, str([round(v, 2) for v in opened]))
check("loudness beyond full doesn't overshoot", max(mouth.step(1.0) for _ in range(5)) <= 1.0)
closing = [mouth.step(0.0) for _ in range(12)]
check("it closes after the voice stops, a little slower than it opened",
      closing[0] > 0.5 and closing[-1] == 0.0, str([round(v, 2) for v in closing]))

print("\nwhere it stands")

area = QRect(0, 0, 2560, 1400)
ui = SimpleNamespace(width=520, margin=24, corner="bottom-right")
x, y = body.default_position(area, ui, 400, 600)
chat_left = area.right() - ui.width - ui.margin
check("beside the chat window, not over it, standing on the taskbar",
      x + 400 <= chat_left and y + 600 - 1 == area.bottom(), f"x {x}..{x + 400}, chat from {chat_left}")
ui.corner = "bottom-left"
x, _ = body.default_position(area, ui, 400, 600)
check("...on the other side when the chat window docks left", x >= ui.width + ui.margin, str(x))

state = body.AvatarState(folder / "state" / "avatar.json")
check("no position saved yet", state.position() is None)
state.save_position(1200, 700)
check("a saved position comes back", body.AvatarState(folder / "state" / "avatar.json").position() == (1200, 700))
(folder / "state" / "avatar.json").write_text("{broken", encoding="utf-8")
check("a damaged file is no position, not a crash", state.position() is None)

print("\nthe voice's loudness")

player = AudioPlayer(sample_rate=22050, blocksize=1024)
tone = (0.25 * 32767 * np.sin(np.arange(22050) / 22050 * 2 * np.pi * 220)).astype(np.int16)
with player._lock:
    player._pending.append((tone, None))
out = np.zeros((1024, 1), dtype=np.int16)
player._callback(out, 1024, None, None)
check("the player reports the loudness of the block it is playing",
      abs(player.level - 0.25 / np.sqrt(2)) < 0.02, f"{player.level:.3f}")
player.stop()
player._callback(out, 1024, None, None)
check("...and 0 once it stops", player.level == 0.0)

worker = CompanionWorker(cfg)
check("the worker gives 0 with no voice", worker.speech_level() == 0.0)
worker._speaker = SimpleNamespace(player=SimpleNamespace(level=0.12))
check("...and the player's loudness with one", worker.speech_level() == 0.12)

print("\nthe window, without drawing")

qt = QApplication.instance() or QApplication(sys.argv)
cfg = AppConfig.load(CONFIG_PATH)
window = body.AvatarWindow(cfg, folder / "haru" / "runtime" / "haru.model3.json")
flags = window.windowFlags()
check("frameless and always on top -- never with Qt's WindowTransparentForInput, which drops its clicks for "
      "good; clicks pass through by the Windows style instead",
      bool(flags & Qt.WindowType.FramelessWindowHint) and bool(flags & Qt.WindowType.WindowStaysOnTopHint)
      and not flags & Qt.WindowType.WindowTransparentForInput)
check("sized from the settings, 2:3", (window.width(), window.height()) == (400, 600),
      f"{window.width()}x{window.height()}")
check("drawing at the configured frame rate", window.timer.interval() == 1000 // cfg.avatar.fps,
      str(window.timer.interval()))
switches = []
real_switch = body.set_click_through
body.set_click_through = lambda hwnd, on: switches.append(on)
window.isVisible = lambda: True  # shown, as far as set_moving can tell
window.move(900, 500)
window.set_moving(True)
check("move mode takes clicks, switched in place by the Windows style -- no rebuilt window, no reloaded model",
      switches == [False] and window.moving
      and not window.windowFlags() & Qt.WindowType.WindowTransparentForInput, str(switches))
window.move(950, 520)
window.set_moving(False)
check("leaving move mode passes clicks through again, and remembers the place",
      switches == [False, True]
      and body.AvatarState(helpers.TEST_DATA_DIR / "avatar.json").position() == (950, 520),
      f"{switches} {body.AvatarState(helpers.TEST_DATA_DIR / 'avatar.json').position()}")
body.set_click_through = real_switch
del window.isVisible
cfg.avatar.height, cfg.avatar.fps = 900, 20
window.apply_settings()
check("height and frame rate change while running",
      (window.width(), window.height()) == (600, 900) and window.timer.interval() == 50)

print("\nwhere it looks")

screen = QRect(0, 0, 2560, 1440)
spot = QRect(1600, 800, 400, 600)
face = (1600 + 200, 800 + 120)


def gaze(cursor, still=0.0, speaking=False):
    return body.gaze_point(cursor, spot, screen, still, speaking)


check("the mouse on its face: straight ahead (the window's centre)", gaze(face) == (200.0, 300.0), str(gaze(face)))
x, y = gaze((0, face[1]))
check("far to the left: turned fully left, level", x == 0.0 and y == 300.0, f"{x}, {y}")
x, _ = gaze((2559, face[1]))
check("a little to the right: turned partway, in proportion, not pinned", 250 < x < 400, str(x))
_, y = gaze((face[0], 0))
check("above: looking up", y < 250, str(y))
check("the mouse resting 5 s: it looks back at you", gaze((0, 0), still=5.0) == (200.0, 300.0))
check("while it speaks: it looks at you", gaze((0, 0), speaking=True) == (200.0, 300.0))
check("a pointer far away on another monitor is a full turn, never past it", gaze((9000, face[1]))[0] == 400.0)

follow_cfg = AppConfig.load(CONFIG_PATH)
follow_cfg.avatar.follow_mouse = True
looker = body.AvatarWindow(follow_cfg, folder / "haru" / "runtime" / "haru.model3.json")
looker.move(300, 100)
clock = {"t": 100.0}
pointer = {"p": QPoint(-2000, 220)}
voice_level = {"v": 0.0}
looker.clock = lambda: clock["t"]
looker.cursor = lambda: pointer["p"]
looker.level = lambda: voice_level["v"]
centre = (looker.width() / 2, looker.height() / 2)
first = looker.gaze_target()
clock["t"] = 104.0
held = looker.gaze_target()
clock["t"] = 105.5
rested = looker.gaze_target()
check("the window follows the pointer, and looks back once it has rested 5 s",
      first[0] < centre[0] and held == first and rested == centre, f"{first} {held} {rested}")
pointer["p"] = QPoint(5000, 220)
moved = looker.gaze_target()
check("...and follows again when it moves", moved[0] > centre[0], str(moved))
voice_level["v"] = 0.1
speaking = looker.gaze_target()
voice_level["v"] = 0.0
clock["t"] = 105.9
just_after = looker.gaze_target()
clock["t"] = 107.0
later = looker.gaze_target()
check("while its voice plays, and a moment after, it looks at you; then follows again",
      speaking == centre and just_after == centre and later[0] > centre[0], f"{speaking} {just_after} {later}")
follow_cfg.avatar.follow_mouse = False
check("switched off: straight ahead whatever the mouse does", looker.gaze_target() == centre)

print("\nmoods on the face")

defaults = {"ParamTere": 0.0, "ParamEyeLOpen": 1.0, "ParamEyeBallX": 0.0, "ParamBrowLY": 0.0,
            "ParamMouthForm": 1.0}
values, rest = body.expression_values("happy", 1.0, {"ParamEyeLOpen": 0.2, "ParamEyeBallX": 0.3}, defaults)
check("happy at full strength: its blush and raised brows, and a smiling resting mouth",
      values["ParamTere"] == 0.6 and values["ParamBrowLY"] == 0.4 and rest == 1.0, f"{values} {rest}")
values, rest = body.expression_values("surprised", 0.5, {"ParamEyeLOpen": 0.2, "ParamEyeBallX": 0.3}, defaults)
check("half-way to surprised: eased, and the eyes widened from wherever blinking left them",
      abs(values["ParamBrowLY"] - 0.5) < 1e-9 and abs(values["ParamEyeLOpen"] - 0.2 * 1.3) < 1e-9
      and abs(rest - (1.0 + (-0.2 - 1.0) * 0.5)) < 1e-9, f"{values} {rest}")
values, _ = body.expression_values("thinking", 1.0, {"ParamEyeLOpen": 1.0, "ParamEyeBallX": 0.3}, defaults)
check("thinking moves the eyes on from where following the mouse put them",
      abs(values["ParamEyeBallX"] - 0.7) < 1e-9, str(values))
values, rest = body.expression_values("neutral", 0.0, {}, defaults)
check("no mood writes the moods' parameters back to their defaults (live2d-py keeps what is set)",
      values.get("ParamTere") == 0.0 and values.get("ParamBrowLY") == 0.0 and rest == 1.0, f"{values} {rest}")
check("a parameter the model lacks is left alone",
      "ParamEyeLSmile" not in body.expression_values("happy", 1.0, {}, defaults)[0])

face = body.FaceMood(hold_s=4.0, after_speech_s=2.0, rate=0.25)
face.show("happy", now=0.0)
steps = [face.step(t / 30, speaking=False) for t in range(1, 6)]
check("a mood fades in over a few frames", steps[0] == ("happy", 0.0) and steps[4] == ("happy", 1.0), str(steps))
face.show("sad", now=0.2)
switching = [face.step(0.2 + t / 30, speaking=False) for t in range(1, 9)]
check("a new mood waits for the old one to fade out, then fades in",
      switching[0] == ("happy", 0.75) and switching[3] == ("sad", 0.0) and switching[7] == ("sad", 1.0),
      str(switching))
ending = [face.step(4.3 + t / 30, speaking=False) for t in range(1, 6)]
check("after its time it fades back to no mood", ending[-1] == ("neutral", 0.0), str(ending))
face.show("happy", now=10.0)
for t in (10.1, 10.2, 10.3, 10.4, 10.5):
    face.step(t, speaking=False)
face.step(15.0, speaking=True)
held = face.step(16.9, speaking=False)
face.step(17.05, speaking=False)
check("while it speaks the mood stays, until a moment after", held == ("happy", 1.0) and face.wanted == "neutral",
      f"{held} then wanted {face.wanted}")
face.show("grumpy", now=20.0)
check("an unknown mood is no mood", face.wanted == "neutral")

mouth = body.MouthShaper()
for _ in range(15):
    opened, form = mouth.step(None, 0.0, rest_form=1.0)
check("a silent mouth rests in the model's own form (Haru's slight smile), not flattened",
      opened == 0.0 and form > 0.98, f"{opened:.2f}, {form:.2f}")

mood_cfg = AppConfig.load(CONFIG_PATH)
mood_cfg.avatar.expressions = False
moody = body.AvatarWindow(mood_cfg, folder / "haru" / "runtime" / "haru.model3.json")
moody.show_mood("happy")
off_mood = moody.face.wanted
mood_cfg.avatar.expressions = True
moody.show_mood("happy")
check("the window takes a mood only while expressions are on", off_mood == "neutral" and moody.face.wanted == "happy")

print("\nthe app")

notices = []
fake = SimpleNamespace(
    config=AppConfig.load(CONFIG_PATH), avatar=None, _avatar_move_action=None,
    window=SimpleNamespace(add_notice=lambda text, colour="": notices.append(text)),
    worker=SimpleNamespace(speech_level=lambda: 0.0),
)
fake._sync_avatar_action = lambda: None
fake.config.avatar.enabled = False
CompanionApp._build_avatar(fake)
check("switched off: no avatar and nothing said", fake.avatar is None and not notices)
fake.config.avatar.enabled = True
fake.config.avatar.folder = str(folder / "empty")
CompanionApp._build_avatar(fake)
check("switched on without a model: no avatar, and a notice saying why",
      fake.avatar is None and notices and "No Live2D model" in notices[-1], str(notices))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
