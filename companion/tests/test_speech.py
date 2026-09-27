"""Speech: transcription, the recorder, and push-to-talk wiring.

Speech is generated with Piper so the expected text is known -- no microphone
and no human needed.
"""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.config import AppConfig
from core.logging import setup_logging
from modules.voice.microphone import Recorder
from modules.voice.stt.whisper_stt import FasterWhisperSTT
from modules.voice.tts.piper_tts import PiperTTS

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)
VOICES = VOICES_DIR

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def to_16k(pcm, rate):
    duration = len(pcm) / rate
    x_old = np.linspace(0, duration, len(pcm), endpoint=False)
    x_new = np.linspace(0, duration, int(duration * 16000), endpoint=False)
    return (np.interp(x_new, x_old, pcm.astype(np.float32)) / 32768.0).astype(np.float32)


print("round trip: speak a question, transcribe it back")

tts = PiperTTS(voice="en_US-lessac-medium", voices_dir=VOICES)
stt = FasterWhisperSTT(model=cfg.speech.model, device=cfg.speech.device,
                       compute_type=cfg.speech.compute_type,
                       silence_rms=cfg.speech.silence_rms,
                       vad_filter=cfg.speech.vad_filter)
stt.warm_up()

QUESTIONS = [
    "What is this article about?",
    "Explain that in simpler terms.",
    "Who wrote this and when was it published?",
]
for expected in QUESTIONS:
    audio = to_16k(tts.synthesize(expected), tts.sample_rate)
    start = time.perf_counter()
    heard = stt.transcribe(audio)
    ms = (time.perf_counter() - start) * 1000

    norm = lambda s: "".join(c.lower() for c in s if c.isalnum() or c.isspace()).split()
    check(f"{ms:4.0f} ms  {expected[:38]!r}", norm(heard) == norm(expected),
          "" if norm(heard) == norm(expected) else f"heard {heard!r}")

print("\nrejecting non-speech")

got = stt.transcribe(np.zeros(2 * 16000, dtype=np.float32))
check("digital silence yields nothing", got == "", f"got {got!r}")

rng = np.random.RandomState(0)
quiet = (rng.randn(2 * 16000) * 0.0015).astype(np.float32)
got = stt.transcribe(quiet)
check("quiet room noise yields nothing", got == "", f"got {got!r}")

check("empty audio returns empty", stt.transcribe(np.zeros(0, dtype=np.float32)) == "")
check("audio below min_seconds is ignored",
      stt.transcribe((rng.randn(1600) * 0.1).astype(np.float32)) == "",
      "0.1s of noise")

from modules.voice.stt.whisper_stt import _is_hallucination

check("'You' is recognised as a hallucination", _is_hallucination("You"))
check("'Thank you.' is recognised", _is_hallucination("Thank you."))
check("a real question is not discarded",
      not _is_hallucination("What is this article about?"))
check("a short real answer is not discarded", not _is_hallucination("Yes it is"))

print("\nrecorder")

rec = Recorder(sample_rate=16000, max_seconds=2.0)
check("not recording before start", not rec.is_recording)
try:
    rec.start()
    started_ok = True
except Exception as exc:
    started_ok = False
    print(f"      (no input device: {exc})")
if started_ok:
    check("recording after start", rec.is_recording)
    time.sleep(0.4)
    audio = rec.stop()
    check("stops and returns audio", not rec.is_recording and len(audio) > 0,
          f"{len(audio) / 16000:.2f}s captured")
    check("captured audio is float32 mono",
          audio.dtype == np.float32 and audio.ndim == 1, f"{audio.dtype} {audio.shape}")
    check("stop is idempotent", len(rec.stop()) == 0)

print("\npush-to-talk wiring")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from modules.ui.talk import PushToTalk


class FakeHotkey:
    def __init__(self):
        self.held = True
        self.vk = 0x41

    def is_held(self):
        return self.held


hotkey = FakeHotkey()
talk = PushToTalk(cfg, hotkey)
captured = []
talk.captured.connect(captured.append)

try:
    talk.pressed()
    check("recording starts on key press", talk.is_recording)
    time.sleep(0.35)
    hotkey.held = False  # user lets go
    deadline = time.time() + 3
    while not captured and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    check("release ends the recording", not talk.is_recording)
    check("audio was handed over", len(captured) == 1,
          f"{len(captured[0]) / 16000:.2f}s" if captured else "nothing captured")
except Exception as exc:
    check("push-to-talk runs", False, str(exc))
finally:
    talk.stop()

stt.close()
tts.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
