"""Reproduce the reported bug: Esc pressed while the companion is TALKING (after
the token stream has finished) must stop the speech, not hide the window."""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from modules.ui.app import CompanionApp
from modules.voice.player import AudioPlayer
from modules.voice.speaker import Speaker
from modules.voice.tts.base import TTSEngine

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)
cfg.ui.start_hidden = True
cfg.ollama.warm_up = False

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class LongEngine(TTSEngine):
    """Returns 10 seconds of audio, so speech far outlives generation."""

    @property
    def sample_rate(self):
        return 22050

    def synthesize(self, text):
        return np.zeros(22050 * 10, dtype=np.int16)


class SilentPlayer(AudioPlayer):
    def start(self):
        pass  # no real device


app = CompanionApp(cfg)

# Stand in for the worker's speaker, and mark generation as finished --
# exactly the state the user was in: text complete, audio still playing.
speaker = Speaker(LongEngine(), SilentPlayer(), min_sentence_chars=5)
app.worker._speaker = speaker
app.worker._answering.clear()

speaker.feed("This is a long spoken answer that keeps going. ")
time.sleep(0.4)

check("speech is playing after generation finished", speaker.is_speaking)
check("worker reports busy while only speaking", app.worker.is_busy(),
      "this is what the old code got wrong")

hidden_before = not app.window.isVisible()
app.window.show()

# Press Esc, exactly as the user did.
app._on_escape()
time.sleep(0.2)

check("Esc stopped the speech", not speaker.is_speaking)
check("Esc did NOT hide the window while speaking", app.window.isVisible(),
      "hiding was the old, wrong behaviour")
check("worker no longer busy", not app.worker.is_busy())

# With nothing running, Esc should hide as before.
app._on_escape()
check("Esc hides the window when idle", not app.window.isVisible())

speaker.close()
app.worker._speaker = None
app.tray.hide()
QTimer.singleShot(0, QApplication.instance().quit)
app.qt.exec()

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
