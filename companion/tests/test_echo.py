"""Regression: the companion must not transcribe its own speech.

System-audio capture taps the output device, so Piper's playback is as audible
to it as a video's. Reported in use: asked in Turkish to translate a Japanese
video, the logs showed faster-whisper detecting Turkish at 0.99 confidence --
the companion hearing itself read the answer aloud.
"""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.logging import setup_logging
from modules.audio.transcriber import AudioTranscriber
from modules.voice.stt.base import STTEngine

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class LoudCapture:
    """Always has audible audio waiting."""

    sample_rate = 16000

    def __init__(self):
        self.drains = 0

    def drain(self, max_seconds):
        self.drains += 1
        rng = np.random.RandomState(0)
        return (rng.randn(16000 * 2) * 0.2).astype(np.float32)


class CountingSTT(STTEngine):
    sample_rate = 16000

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return "some speech was heard"


print("while the companion is silent")

capture, stt = LoudCapture(), CountingSTT()
quiet = AudioTranscriber(capture, stt, chunk_seconds=0.6, silence_rms=0.001,
                         speaking_probe=lambda: False)
quiet.start()
time.sleep(2.2)
quiet.stop()
check("audio is transcribed normally", stt.calls > 0, f"{stt.calls} calls")
check("nothing dropped as echo", quiet.chunks_dropped_to_echo == 0)
check("transcript is populated", len(quiet.transcript()) > 0)

print("\nwhile the companion is speaking")

capture2, stt2 = LoudCapture(), CountingSTT()
talking = AudioTranscriber(capture2, stt2, chunk_seconds=0.6, silence_rms=0.001,
                           speaking_probe=lambda: True)
talking.start()
time.sleep(2.2)
talking.stop()
check("its own speech is never transcribed", stt2.calls == 0,
      f"{stt2.calls} calls to the model")
check("chunks are dropped as echo", talking.chunks_dropped_to_echo > 0,
      f"{talking.chunks_dropped_to_echo} dropped")
check("transcript stays clean", talking.transcript() == "",
      f"got {talking.transcript()[:60]!r}")

print("\nafter it stops speaking")

speaking = {"now": True}
capture3, stt3 = LoudCapture(), CountingSTT()
resuming = AudioTranscriber(capture3, stt3, chunk_seconds=0.5, silence_rms=0.001,
                            speaking_probe=lambda: speaking["now"])
resuming.start()
time.sleep(1.5)
during = stt3.calls
speaking["now"] = False
# The guard holds for 1.5 chunk lengths after the last speech, so the tail of a
# part-contaminated chunk is discarded too.
time.sleep(3.0)
after = stt3.calls
resuming.stop()

check("silent during its own speech", during == 0, f"{during} calls")
check("listening resumes once it stops", after > during, f"{after} calls after")

print("\nthe probe is wired up in the real graph")

import inspect

from modules.ui import worker as worker_module

source = inspect.getsource(worker_module)
check("worker connects speaker to transcriber",
      "speaking_probe" in source and "is_speaking" in source)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
