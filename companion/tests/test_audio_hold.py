"""Remarks wait for audio to stop instead of talking over it.

A remark spoken over a video destroys something the user cannot get back
without scrubbing -- and a video is exactly when a companion is most likely to
have something to say. So while system audio is playing, a remark that is due
waits, and goes ahead once it has been quiet for a moment.

Since Stage 2 every "not now" works like this -- the page is a standing
candidate that waiting cannot use up -- but audio came first, and the capture
signal it depends on is tested here.
"""

import json
import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtCore import QCoreApplication

from core.attention import AttentionPolicy
from core.config import AppConfig
from core.logging import setup_logging
from core.orchestrator import Orchestrator
from core.types import ScreenContext
from modules.audio.loopback import SystemAudioCapture
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
qt = QCoreApplication.instance() or QCoreApplication(sys.argv)
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, json_schema=None, **kwargs):
        if json_schema is None:
            yield "reading about the Antikythera mechanism"
        else:
            yield json.dumps({"say": self.reply, "why": "the gearing is the surprising part"})

    def health_check(self):
        pass


REMARK = "That gear train is absurd for its age."


def page(title="Antikythera mechanism - Wikipedia"):
    return ScreenContext(text="x " * 500, window_title=title, app_name="brave.exe")


def orchestrator():
    """An orchestrator whose page is already due for a remark."""
    policy = AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100)
    orch = Orchestrator(FakeLLM(REMARK), policy, max_words=25, min_time_on_page_s=0)
    orch.observe(page())
    return orch


print("the capture knows when sound is playing (no audio device needed)")

capture = SystemAudioCapture(sample_rate=16000, sound_rms=0.005)
check("before any sound, it reads as silent for a long time",
      capture.seconds_since_sound > 3600)
BLOCK = 4000  # 0.25 s at 16 kHz: what the capture thread reads at a time
capture._note_level(np.zeros(BLOCK, dtype=np.float32))
check("digital silence is not sound", capture.seconds_since_sound > 3600)
capture._note_level(np.full(BLOCK, 0.002, dtype=np.float32))
check("a level under the threshold is not sound either",
      capture.seconds_since_sound > 3600)
tone = (0.1 * np.sin(2 * np.pi * 440 * np.arange(BLOCK) / 16000)).astype(np.float32)
capture._note_level(tone)
check("a tone is", capture.seconds_since_sound < 0.5,
      f"{capture.seconds_since_sound:.3f}s")

started = time.perf_counter()
for _ in range(2000):
    capture._note_level(tone)
per_block_us = (time.perf_counter() - started) / 2000 * 1e6
print(f"    cost: {per_block_us:.1f} us per block "
      f"({per_block_us * 4 / 1000:.3f} ms per second of audio)")
# 500 us per 250 ms block would be 0.2% of a core -- the ceiling for something
# that runs whenever the capture does.
check("tracking it is effectively free", per_block_us < 500, f"{per_block_us:.1f} us")

print("\nwhile audio plays, a due remark waits")

orch = orchestrator()
check("nothing is said while it plays", orch.poll(hold=True) is None)
check("...and the page stays the candidate instead of being thrown away",
      orch.current is not None and orch.current.attempts == 0)
for _ in range(20):
    orch.poll(hold=True)
check("however long it keeps playing, no model call is spent on it",
      orch.current.attempts == 0, f"{orch.current.attempts} attempts")
remark = orch.poll()
check("once it stops, the held remark goes ahead",
      remark is not None and remark.text == REMARK,
      remark.text if remark else "nothing")

print("\npages changed while it plays collapse into one")

orch = orchestrator()
for i in range(10):
    orch.observe(page(f"Tab {i}"))
    orch.poll(hold=True)
check("ten tab changes leave one candidate: the page they ended on",
      orch.current.identity.endswith("Tab 9"), orch.current.identity)
remark = orch.poll()
check("...which is remarked on once, when it goes quiet",
      remark is not None and orch.current.remarks == 1)

print("\nthe worker decides from the capture")


class FakeCapture:
    def __init__(self, since):
        self.seconds_since_sound = since


class FakeAudio:
    def __init__(self, since):
        self.capture = FakeCapture(since)


class FakeCompanion:
    def __init__(self, audio):
        self.audio = audio


cfg = AppConfig.load(CONFIG_PATH)
cfg.proactive.enabled = True
worker = CompanionWorker(cfg)
quiet_needed = cfg.proactive.audio_quiet_s

worker._companion = FakeCompanion(FakeAudio(0.3))
check("sound a moment ago counts as playing", worker._audio_is_playing())
worker._companion = FakeCompanion(FakeAudio(quiet_needed + 1))
check(f"quiet for longer than audio_quiet_s ({quiet_needed}s) does not",
      not worker._audio_is_playing())
worker._companion = FakeCompanion(None)
check("without system-audio listening there is nothing to wait for",
      not worker._audio_is_playing())
cfg.proactive.hold_while_audio_plays = False
worker._companion = FakeCompanion(FakeAudio(0.3))
check("switched off, it never holds", not worker._audio_is_playing())
cfg.proactive.hold_while_audio_plays = True

print("\nend to end through the worker's event pump")

worker._companion = FakeCompanion(FakeAudio(0.3))
worker._orchestrator = orchestrator()
said = []
worker.remarked.connect(lambda text, why, remark: said.append(text))
worker._pump_events()
check("with a video playing, the pump says nothing", said == [], str(said))
check("...and keeps the page for later", worker._orchestrator.current.attempts == 0)
worker._companion.audio.capture.seconds_since_sound = quiet_needed + 1
worker._pump_events()
check("when it goes quiet, the remark is made", said == [REMARK], str(said))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
