"""The avatar's mouth shapes from speech sounds.

Phonemes sort into shapes, with stress and length marks borrowing their
neighbour's; the shapes keep every sample, so they stay in step with the audio;
real Piper speech comes with shapes adding up to its audio, and without them
when alignments are off; the player reports the shape sounding at a moment,
allowing for the device's latency; the speaker hands shapes to the player only
when there are some; the mouth takes the shape, or follows loudness without
one; and the voice is loaded with timings only for an avatar that wants them.
"""

import sys
import threading
import time
from types import SimpleNamespace

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.companion import build_speaker
from core.config import AppConfig
from core.logging import setup_logging
from modules.ui.avatar import MouthShaper
from modules.ui.worker import CompanionWorker
from modules.voice.player import AudioPlayer, _Marks
from modules.voice.speaker import Speaker
from modules.voice.tts.base import TTSEngine
from modules.voice.tts.piper_tts import PiperTTS
from modules.voice.visemes import REST, SHAPES, mouth_shapes, shape_of

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("sounds to shapes")

expected = {"a": "a", "ɑ": "a", "ɪ": "i", "ɯ": "i", "ʊ": "u", "y": "u", "ə": "e", "ɛ": "e",
            "ɔ": "o", "ø": "o", "m": "closed", "b": "closed", "s": "consonant", "ʃ": "consonant",
            " ": REST, "^": REST, "$": REST, "!": REST, ",": REST}
wrong = {p: shape_of(p) for p, want in expected.items() if shape_of(p) != want}
check("vowels, lips-together sounds, other consonants and pauses (English and Turkish IPA)",
      not wrong, str(wrong))

aligned = [("^", 100), ("h", 50), ("ˈ", 40), ("ɛ", 80), ("l", 60), ("o", 70), ("ː", 30), (" ", 20)]
shapes = mouth_shapes(aligned)
check("a stress mark takes the next sound's shape, a length mark the previous one's",
      shapes == [(REST, 100), ("consonant", 50), ("e", 120), ("consonant", 60), ("o", 100), (REST, 20)],
      str(shapes))
check("every sample is kept", sum(n for _, n in shapes) == sum(n for _, n in aligned))
check("every shape has an opening and a form", all(s in SHAPES for s, _ in shapes))

print("\nreal Piper speech")

tts = PiperTTS(voice="en_US-lessac-medium", voices_dir=VOICES_DIR, alignments=True)
samples, marks = tts.synthesize_marked("Hello! How are you doing today?")
check("with alignments, shapes come with the audio and add up to it exactly",
      marks is not None and sum(n for _, n in marks) == len(samples),
      f"{sum(n for _, n in marks) if marks else None} vs {len(samples)}")
names = {s for s, _ in marks or []}
check("...and include open vowels and rounded ones", {"a", "o"} & names and "u" in names, str(sorted(names)))
tts.alignments = False
plain, none = tts.synthesize_marked("Hello! How are you doing today?")
# No length comparison: Piper varies each rendering (three in a row: 46336,
# 44800, 42752 samples; a 15% tolerance still failed once at 50944 vs 43520),
# and a check that fails at random is worse than none.
check("with alignments off: no shapes, and still speech",
      none is None and len(plain) > 0.3 * tts.sample_rate, f"{len(plain)} samples")
check("synthesize() still gives the samples alone", isinstance(tts.synthesize("Yes."), np.ndarray))
tts.close()

print("\nthe player knows the shape sounding now")

player = AudioPlayer(sample_rate=1000, blocksize=100)
clock = {"t": 10.0}
player.clock = lambda: clock["t"]
player._latency_s = 0.05
player._pending.append((np.ones(500, dtype=np.int16), "s1", _Marks([("rest", 100), ("a", 150), ("o", 250)])))
out = np.zeros((100, 1), dtype=np.int16)
player._callback(out, 100, None, None)          # samples 0-99, heard from 10.05
clock["t"] = 10.1
player._callback(out, 100, None, None)          # samples 100-199, heard from 10.15
check("before the first block is heard, nothing", player.shape_now(10.04) is None)
check("the first block, allowing for latency", player.shape_now(10.10) == "rest", str(player.shape_now(10.10)))
check("the next block, its own shape", player.shape_now(10.20) == "a", str(player.shape_now(10.20)))
check("past what was handed over, nothing", player.shape_now(10.30) is None)
player.stop()
check("stopped: nothing", player.shape_now(10.20) is None)

print("\nthe speaker hands shapes on")


class Marked(TTSEngine):
    sample_rate = 22050

    def synthesize(self, text):
        return np.ones(200, dtype=np.int16)

    def synthesize_marked(self, text):
        return self.synthesize(text), [("a", 200)]


class Plain(TTSEngine):
    sample_rate = 22050

    def synthesize(self, text):
        return np.ones(200, dtype=np.int16)


class RecordingPlayer:
    sample_rate = 22050
    is_playing = False
    progress = (None, None)

    def __init__(self):
        self.calls = []
        self.done = threading.Event()

    def enqueue(self, samples, tag=None, **kwargs):
        self.calls.append(kwargs)
        self.done.set()

    def stop(self):
        pass

    def close(self):
        pass


for engine, want, label in ((Marked(), [{"marks": [("a", 200)]}], "shapes, when the voice gives them"),
                            (Plain(), [{}], "no marks argument at all without them")):
    recorder = RecordingPlayer()
    speaker = Speaker(engine, recorder)
    speaker.feed("This sentence is long enough. ")
    recorder.done.wait(3)
    check(f"the speaker passes {label}", recorder.calls == want, str(recorder.calls))
    speaker.close()

print("\nthe mouth")

mouth = MouthShaper()
for _ in range(10):
    opened, form = mouth.step("i", 0.1)
check("a known sound sets both how open and the form", abs(opened - 0.35) < 0.02 and form > 0.95,
      f"{opened:.2f}, {form:.2f}")
for _ in range(10):
    opened, form = mouth.step("u", 0.1)
check("...a rounded one rounds it", form < -0.95, f"{form:.2f}")
for _ in range(12):
    opened, form = mouth.step(None, 0.15)
check("without a known sound, loudness opens it and the form goes neutral",
      opened > 0.9 and abs(form) < 0.02, f"{opened:.2f}, {form:.2f}")
for _ in range(15):
    opened, form = mouth.step(REST, 0.0)
check("a pause closes it", opened == 0.0, f"{opened:.2f}")

worker = CompanionWorker(AppConfig.load(CONFIG_PATH))
check("the worker gives no shape with no voice", worker.speech_shape() is None)
worker._speaker = SimpleNamespace(player=SimpleNamespace(shape_now=lambda: "o"))
check("...and the player's shape with one", worker.speech_shape() == "o")

print("\nwhen the voice is loaded with timings")

cfg = AppConfig.load(CONFIG_PATH)
cfg.voice.enabled, cfg.voice.warm_up = True, False
for avatar_on, shapes_on, want in ((True, True, True), (True, False, False), (False, True, False)):
    cfg.avatar.enabled, cfg.avatar.mouth_shapes = avatar_on, shapes_on
    speaker = build_speaker(cfg)
    check(f"avatar {'on' if avatar_on else 'off'}, mouth shapes {'on' if shapes_on else 'off'}: "
          f"timings {'on' if want else 'off'}", speaker.engine.alignments is want)
    speaker.close()

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
