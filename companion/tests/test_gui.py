"""GUI smoke test: build every widget, register the hotkey, run one real
question through the worker thread, then quit. Nothing is left on screen."""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging

setup_logging("WARNING")
cfg = AppConfig.load(CONFIG_PATH)
cfg.ui.start_hidden = True  # don't flash a window across the user's desktop

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


from modules.ui.app import CompanionApp

app = CompanionApp(cfg)
check("CompanionApp constructed", app is not None)
check("tray icon visible", app.tray.isVisible())
check("window is frameless + always-on-top",
      bool(app.window.windowFlags() & 0x00000800))

screens = QApplication.screens()
index = max(0, min(cfg.monitor_index - 1, len(screens) - 1))
target = screens[index].availableGeometry()
app.window.show()
pos = app.window.frameGeometry()
check(f"docked to {cfg.ui.corner} of monitor {cfg.monitor_index}",
      target.contains(pos.center()),
      f"window at {pos.topLeft().toTuple()}, screen {target.getRect()}")

hwnd = int(app.window.winId())
from modules.ui.hotkey import GlobalHotkey, HotkeyError

try:
    hk = GlobalHotkey(hwnd, cfg.ui.hotkey)
    check(f"global hotkey {cfg.ui.hotkey} registered", hk.registered)
    hk.release()
    check("hotkey released cleanly", not hk.registered)
except HotkeyError as exc:
    check(f"global hotkey {cfg.ui.hotkey} registered", False, str(exc))

app.window.hide()

# Now drive one real question end-to-end through the worker thread.
transcript: list[str] = []
state = {"ready": None, "done": False}

app.worker.ready.connect(lambda err: state.__setitem__("ready", err))
app.worker.chunk.connect(transcript.append)
app.worker.finished_answer.connect(lambda: state.__setitem__("done", True))


def kick():
    if state["ready"] is None:
        return  # still starting up
    kick_timer.stop()
    if state["ready"]:
        check("worker started", False, state["ready"])
        QApplication.instance().quit()
        return
    check("worker started and model reachable", True)
    app.worker.ask("Reply with exactly the word OK and nothing else.")


app.worker.start()

kick_timer = QTimer()
kick_timer.timeout.connect(kick)
kick_timer.start(200)


def finish():
    if not state["done"]:
        return
    finish_timer.stop()
    answer = "".join(transcript).strip()
    check("answer streamed back to the UI thread", bool(answer), f"got {answer[:40]!r}")
    check("memory recorded the turn",
          app.worker._companion.memory.turns == 1,
          f"turns={app.worker._companion.memory.turns}")
    QApplication.instance().quit()


finish_timer = QTimer()
finish_timer.timeout.connect(finish)
finish_timer.start(200)

QTimer.singleShot(120_000, QApplication.instance().quit)  # hard timeout
app.qt.exec()
app.worker.shutdown()
app.tray.hide()

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
