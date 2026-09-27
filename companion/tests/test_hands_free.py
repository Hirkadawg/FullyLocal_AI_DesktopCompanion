"""Hands-free listening: the microphone stays on, each thing said is asked (F4b).

The detector is driven with synthetic audio and a stand-in for Silero VAD that
calls anything loud "speech", so utterance boundaries can be checked exactly:
started once, cut at a pause with a moment of lead-in kept, fragments dropped,
long speech capped. Then the listener's suppression, the app's rules for when
the microphone must not count (the talk key, the companion's own voice, other
sound) and interrupting by voice. No microphone is opened.
"""

import os
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from modules.ui.app import CompanionApp
from modules.ui.listen import HandsFreeListener
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker
from modules.voice.microphone import Recorder
from modules.voice.vad import STARTED, UtteranceDetector, silero_speech_seconds

setup_logging("ERROR")
failures = 0
RATE = 16000
CHUNK = 2400  # what one 150 ms poll drains


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def loud_seconds(audio):
    """Stand-in for Silero: 30 ms frames with anything louder than 0.05 are speech."""
    audio = np.asarray(audio).reshape(-1)
    n = len(audio) // 480
    if not n:
        return 0.0
    frames = np.abs(audio[: n * 480]).reshape(n, 480).max(axis=1)
    return float((frames > 0.05).sum()) * 0.03


def loud_segments(audio):
    """Stand-in for Silero's segments: runs of loud 30 ms frames, in seconds."""
    audio = np.asarray(audio).reshape(-1)
    n = len(audio) // 480
    if not n:
        return []
    loud = np.abs(audio[: n * 480]).reshape(n, 480).max(axis=1) > 0.05
    segments, start = [], None
    for i, is_loud in enumerate(loud):
        if is_loud and start is None:
            start = i
        elif not is_loud and start is not None:
            segments.append((start * 0.03, i * 0.03))
            start = None
    if start is not None:
        segments.append((start * 0.03, n * 0.03))
    return segments


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def voice(seconds):
    t = np.arange(int(seconds * RATE)) / RATE
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def run(detector, audio):
    """Feed in poll-sized chunks; collect what each step reports."""
    events = []
    for i in range(0, len(audio), CHUNK):
        detector.feed(audio[i:i + CHUNK])
        event = detector.step()
        if isinstance(event, str):
            events.append(event)
        elif event is not None:
            events.append(event)
    return events


print("cutting speech into utterances")

d = UtteranceDetector(loud_segments, pause_s=0.8)
events = run(d, np.concatenate([silence(1.0), voice(1.2), silence(1.5)]))
starts = [e for e in events if isinstance(e, str)]
said = [e for e in events if not isinstance(e, str)]
check("someone talking is noticed once", starts == [STARTED], str(starts))
check("...and what they said is handed over once, after the pause", len(said) == 1,
      f"{len(said)} utterance(s)")
if said:
    heard = said[0]
    check("the utterance holds all of the speech", abs(loud_seconds(heard) - 1.2) < 0.1,
          f"{loud_seconds(heard):.2f}s of speech")
    check("...with a moment of lead-in, so the first word isn't clipped",
          np.abs(heard[:int(0.1 * RATE)]).max() < 0.05)
check("afterwards it is waiting for the next one", d.in_speech is False)

events = run(d, np.concatenate([voice(0.15), silence(1.5)]))
check("a short blip isn't taken for speech", events == [], str(len(events)))

events = run(d, np.concatenate([voice(0.3), silence(2.0)]))
check("a fragment starts listening but isn't asked",
      events == [STARTED], str([e if isinstance(e, str) else "audio" for e in events]))

capped = UtteranceDetector(loud_segments, pause_s=0.8, max_seconds=3.0)
events = run(capped, voice(4.0))
said = [e for e in events if not isinstance(e, str)]
check("speech longer than max_seconds is cut there",
      len(said) == 1 and abs(len(said[0]) / RATE - 3.0) < 0.2,
      f"{[round(len(s) / RATE, 2) for s in said]}")

d = UtteranceDetector(loud_segments, pause_s=0.8)
events = run(d, np.concatenate([voice(0.6), silence(0.4), voice(0.6), silence(1.2)]))
said = [e for e in events if not isinstance(e, str)]
check("a pause shorter than pause_s doesn't split a sentence",
      len(said) == 1 and abs(loud_seconds(said[0]) - 1.2) < 0.1,
      f"{[round(loud_seconds(s), 2) for s in said]}")

check("real Silero finds no speech in silence", silero_speech_seconds(silence(1.0)) == 0.0)
rng = np.random.default_rng(1)
check("...or in faint noise",
      silero_speech_seconds((rng.normal(0, 0.001, RATE * 2)).astype(np.float32)) == 0.0)

rec = Recorder()
rec._callback(np.full((1600, 1), 0.5, dtype=np.float32), 1600, None, None)
check("the recorder drains what arrived, and starts afresh",
      len(rec.drain()) == 1600 and len(rec.drain()) == 0)

print("\nthe listener")

qt = QApplication.instance() or QApplication(sys.argv)
cfg = AppConfig.load(CONFIG_PATH)


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.pending = []

    def start(self):
        self.is_recording = True

    def stop(self):
        self.is_recording = False

    def drain(self):
        return self.pending.pop(0) if self.pending else np.zeros(0, np.float32)


blocked = [False]
recorder = FakeRecorder()
listener = HandsFreeListener(cfg, suppressed=lambda: blocked[0], segments=loud_segments,
                             recorder=recorder)
got = []
listener.started.connect(lambda: got.append("started"))
listener.utterance.connect(lambda audio: got.append(("utterance", len(audio))))

listener.start()
check("start opens the microphone", listener.is_listening)
audio = np.concatenate([silence(1.0), voice(1.2), silence(1.5)])
recorder.pending = [audio[i:i + CHUNK] for i in range(0, len(audio), CHUNK)]
while recorder.pending:
    listener.poll()
check("it reports talking, then the utterance",
      got[:1] == ["started"] and len(got) == 2 and got[1][0] == "utterance", str(got))

got.clear()
blocked[0] = True
recorder.pending = [audio[i:i + CHUNK] for i in range(0, len(audio), CHUNK)]
while recorder.pending:
    listener.poll()
check("while suppressed, nothing it hears counts", got == [], str(got))
blocked[0] = False
recorder.pending = [voice(0.6)[:CHUNK]] * 2 + [silence(0.15)[:CHUNK]] * 12
while recorder.pending:
    listener.poll()
check("...and nothing heard before is carried over afterwards",
      all(e == "started" for e in got), str(got))

listener.stop()
check("stop closes the microphone", not listener.is_listening)

print("\nwhen the microphone must not count")

talk = SimpleNamespace(is_recording=False)
speaking = [False]
playing = [False]
log = []
fake = SimpleNamespace(
    config=cfg, talk=talk,
    listener=SimpleNamespace(recorder=SimpleNamespace(missing=None)),
    _tell_missing_microphone=lambda recorder: None,
    worker=SimpleNamespace(
        is_speaking_recently=lambda tail: speaking[0], sound_playing=lambda: playing[0],
        is_busy=lambda: speaking[0], cancel=lambda: log.append("cancel"),
        note_user_talking=lambda: log.append("quiet"),
        transcribe=lambda audio, quiet=False: log.append(("transcribe", quiet))),
    window=SimpleNamespace(set_status=lambda s: None, set_listening=lambda on: log.append(("listening", on)),
                           add_notice=lambda *a: log.append(("notice", a[0]))),
)
suppressed = lambda: CompanionApp._listen_suppressed(fake)  # noqa: E731

cfg.speech.mic_hears_speakers = True
check("nothing stops it by default", not suppressed())
talk.is_recording = True
check("the talk key or mic button has the microphone: suppressed", suppressed())
talk.is_recording = False
speaking[0] = True
check("microphone hears the speakers, companion talking: suppressed", suppressed())
speaking[0] = False
playing[0] = True
check("...or other sound playing: suppressed", suppressed())
playing[0] = False

cfg.speech.mic_hears_speakers = False
speaking[0] = playing[0] = True
check("microphone can't hear the speakers: it keeps listening through both", not suppressed())

log.clear()
CompanionApp._on_listen_started(fake)
check("...and starting to talk interrupts the companion", log == ["quiet", "cancel"], str(log))
cfg.speech.mic_hears_speakers = True
log.clear()
CompanionApp._on_listen_started(fake)
check("when it can hear the speakers, talking never cancels it", "cancel" not in log, str(log))
speaking[0] = playing[0] = False

log.clear()
CompanionApp._on_listen_utterance(fake, voice(1.0))
check("an utterance is transcribed quietly and asked", log == [("transcribe", True)], str(log))

print("\nthe worker, the button and the app switch")

worker = CompanionWorker(cfg)
heard = []
worker.heard.connect(heard.append)
worker._stt = SimpleNamespace(transcribe=lambda audio: "", last_language=None)
worker._transcribe(voice(1.0), quiet=True)
check("hands-free, hearing nothing intelligible says nothing", heard == [], str(heard))
worker._transcribe(voice(1.0))
check("the talk key still says it didn't catch that", heard == [""], str(heard))

worker._speaker = SimpleNamespace(is_speaking=True)
check("speaking counts as speaking recently", worker.is_speaking_recently(0.6))
worker._speaker.is_speaking = False
check("...and so does just after", worker.is_speaking_recently(0.6))
worker._last_spoke_at = time.time() - 1.0
check("...but not once the echo tail has passed", not worker.is_speaking_recently(0.6))

window = ChatWindow(cfg)
toggles = []
window.listen_toggled.connect(toggles.append)
check("the window has a listen button", hasattr(window, "listen_button"))
window.listen_button.click()
window.listen_button.click()
check("clicking it switches listening on and off", toggles == [True, False], str(toggles))
window.set_listening(True)
check("while on it says so", window.listen_button.isChecked()
      and "listening" in window.listen_button.accessibleName(), window.listen_button.accessibleName())

recorder = FakeRecorder()
fake.listener = HandsFreeListener(cfg, segments=loud_segments, recorder=recorder)
log.clear()
CompanionApp._set_listening(fake, True)
check("switching on starts listening and says how it works",
      fake.listener.is_listening and ("listening", True) in log
      and any(isinstance(e, tuple) and e[0] == "notice" and "just talk" in e[1] for e in log), str(log))
CompanionApp._set_listening(fake, False)
check("switching off closes the microphone", not fake.listener.is_listening
      and ("listening", False) in log)

cfg.speech.listen_button = False
check("speech.listen_button off: no button", not hasattr(ChatWindow(cfg), "listen_button"))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
