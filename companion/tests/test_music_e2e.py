"""Music questions with the real model: does it use the analysis, and say it right?

The system audio is a synthetic song with a known answer, handed to the music
tool; the screen is the Antikythera fixture, which says nothing about music.
Judged over a batch, as model output must be: asked for the key of a loop whose
chords fit A minor and C major equally, the answer names both; asked for the
chords and tempo of a clear one, it gives them; and a question about the page
doesn't run the analysis.
"""

import sys
from types import SimpleNamespace

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

sys.path.insert(0, str(helpers.TESTS_DIR / "fixtures"))

from make_music import song

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging

setup_logging("ERROR")
failures = 0
BATCH = 5


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class FakeCapture:
    sample_rate = 16000

    def __init__(self, audio):
        self.audio = audio

    def recent(self, seconds):
        return self.audio[-int(seconds * self.sample_rate):]


cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False  # no real loopback or Whisper: the tool gets a fake below
cfg.music.enabled = True
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
comp.refresh()
cfg.audio.enabled = True
hearing = SimpleNamespace(capture=None, last_spoke_at=0.0)
comp.tools.get("analyse_music").audio = hearing
ran = []
original_run = comp.tools.run
comp.tools.run = lambda call: ran.append(call.name) or original_run(call)


def ask(question):
    comp.memory.clear()
    ran.clear()
    return comp.ask(question, context=comp.last_context).text()


print("an ambiguous key")
hearing.capture = FakeCapture(song(9, "minor", 100, seed=3)[0])  # Am F C G
used = both = 0
for _ in range(BATCH):
    answer = ask("What key is this song in?")
    used += "analyse_music" in ran
    both += "A minor" in answer and "C major" in answer
    print(f"    said: {answer[:160]!r}")
check(f"the model ran the analysis ({used}/{BATCH})", used == BATCH)
check(f"...and named both keys it could be ({both}/{BATCH})", both >= BATCH - 1)

print("\nchords and tempo")
hearing.capture = FakeCapture(song(2, "minor", 120, progression="minor_cadence", seed=40)[0])  # Dm Gm A Dm
right = 0
for _ in range(BATCH):
    answer = ask("What are the chords and the tempo?")
    # The model spells chords out as often as not: "Gm" or "G minor" are both right.
    right += ("analyse_music" in ran and ("Gm" in answer or "G minor" in answer)
              and "120" in answer)
    print(f"    said: {answer[:160]!r}")
check(f"it gives the chords and the tempo from the analysis ({right}/{BATCH})", right >= BATCH - 1)

print("\nnot a music question")
touched = 0
for _ in range(3):
    ask("What is this article about?")
    touched += "analyse_music" in ran
check(f"a question about the page doesn't run the analysis ({touched}/3)", touched == 0)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
