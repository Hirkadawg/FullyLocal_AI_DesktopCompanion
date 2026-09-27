"""Hands-free listening with the real speech detector and recogniser.

Synthesised speech, at this microphone's measured room-noise level, fed in
150 ms polls through the real UtteranceDetector (Silero VAD) and transcribed by
Whisper. The fast suite checks the logic with a stand-in detector; this checks
what the stand-in can't: Silero misjudged short windows, and a 0.4 s pause
inside "What is this article about, and how old is it?" split the question in
two. No microphone or speakers are used.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.companion import build_stt
from core.config import AppConfig
from core.logging import setup_logging
from modules.voice.tts.piper_tts import PiperTTS
from modules.voice.vad import STARTED, UtteranceDetector

setup_logging("ERROR")
failures = 0
RATE = 16000


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)
tts = PiperTTS(voice="en_US-lessac-medium", voices_dir=VOICES_DIR)
stt = build_stt(cfg)


def speech(text):
    pcm = tts.synthesize(text).astype(np.float32)
    if np.abs(pcm).max() > 1.5:
        pcm = pcm / 32768.0
    n = int(len(pcm) * RATE / tts.sample_rate)
    return np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)


def room(seconds, seed=0):
    # Room noise measured on this microphone: median frame RMS ~0.0001-0.0002.
    return np.random.default_rng(seed).normal(0, 0.0002, int(seconds * RATE)).astype(np.float32)


def listen(stream):
    detector = UtteranceDetector(pause_s=cfg.speech.pause_s)
    heard = []
    for i in range(0, len(stream), 2400):
        detector.feed(stream[i:i + 2400])
        event = detector.step()
        if event is not None and not isinstance(event, str):
            heard.append(((i + 2400) / RATE, event))
    return heard


first_a, first_b = speech("What is this article about,"), speech("and how old is it?")
second = speech("Tell me more about the gears.")
parts = [room(1.0, 1), first_a, room(0.4, 2), first_b, room(1.5, 3), second, room(1.5, 4)]
bounds, t = [], 0.0
for part in parts:
    bounds.append(t + len(part) / RATE)
    t += len(part) / RATE
first_ends, second_ends = bounds[3], bounds[5]

print("two questions, the first with a short pause inside it")
heard = listen(np.concatenate(parts))
check("two utterances, not three", len(heard) == 2, f"{len(heard)} utterance(s)")
texts = [stt.transcribe(audio) for _, audio in heard]
for (at, audio), text in zip(heard, texts):
    print(f"    at {at:.2f}s, {len(audio) / RATE:.1f}s: {text!r}")
if len(heard) == 2:
    check("the short pause didn't split the first question",
          "article" in texts[0].lower() and "old" in texts[0].lower(), texts[0])
    check("the second is heard whole", "gears" in texts[1].lower(), texts[1])
    delays = [heard[0][0] - first_ends, heard[1][0] - second_ends]
    check(f"each is handed over after a pause of pause_s ({cfg.speech.pause_s}s), not before",
          all(d >= cfg.speech.pause_s - 0.2 for d in delays), str([round(d, 2) for d in delays]))
    check("...and without a long wait (under pause_s + 0.7s)",
          all(d <= cfg.speech.pause_s + 0.7 for d in delays), str([round(d, 2) for d in delays]))

print("\nroom noise alone")
check("five seconds of room noise are never taken for speech", listen(room(5.0, 9)) == [])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
