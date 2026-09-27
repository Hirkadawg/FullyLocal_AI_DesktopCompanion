"""Speech synthesis: sentence splitting, text cleanup, the audio mixing callback,
and barge-in. No audio device and no real synthesis needed."""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.logging import setup_logging
from modules.voice.player import AudioPlayer
from modules.voice.speaker import Speaker, speakable, split_sentences
from modules.voice.tts.base import TTSEngine

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("sentence splitting:")

s, rest = split_sentences("The first sentence here. The second one follows. And a th")
check("splits complete sentences", s == ["The first sentence here.",
                                         "The second one follows."], f"{s}")
check("keeps the incomplete tail", rest.strip() == "And a th", f"{rest!r}")

s, rest = split_sentences("No boundary yet")
check("nothing emitted without a boundary", s == [] and rest == "No boundary yet")

s, _ = split_sentences("Consider water, e.g. the liquid kind, in this long example. Done.")
check("abbreviation does not end a sentence",
      s == ["Consider water, e.g. the liquid kind, in this long example.", "Done."]
      or len(s) == 1, f"{s}")
check("e.g. not split off alone", not any(x.strip().endswith("e.g.") for x in s), f"{s}")

s, _ = split_sentences('He said "stop that now." Then he left. ')
check("quotes after terminator stay attached",
      s[0] == 'He said "stop that now."', f"{s[0]!r}")

s, rest = split_sentences("Hi. This one is definitely long enough to speak.")
check("fragment below min_chars is held back", "Hi." not in s, f"{s}")

s, _ = split_sentences("Question one? Answer follows here. Exclaim it!  ", min_chars=5)
check("handles ? and !", len(s) == 3, f"{s}")

# Short sentences merge into the following one rather than being spoken as
# choppy fragments -- "Yes." on its own would sound clipped.
s, rest = split_sentences("Yes. That is the reason it happened. ")
check("short sentence merges with the next",
      s == ["Yes. That is the reason it happened."], f"{s}")

# ...but a short sentence at the very end still gets spoken, via flush().
s, rest = split_sentences("Indeed. ")
check("short trailing sentence waits in the buffer", s == [] and rest.strip() == "Indeed.",
      f"s={s} rest={rest!r}")

print("\nspoken text cleanup:")

check("bold markers removed", "*" not in speakable("This is **very** important"))
check("backticks removed", "`" not in speakable("Run `pip install` now"))
check("headings removed", "#" not in speakable("# Title here"))
check("whitespace collapsed", speakable("too   many\n\nspaces") == "too many spaces")
check("plain text untouched", speakable("Just a normal sentence.") ==
      "Just a normal sentence.")

print("\naudio mixing callback:")

player = AudioPlayer(sample_rate=22050)
player._pending.append((np.arange(100, dtype=np.int16), None))
buf = np.zeros((40, 1), dtype=np.int16)
player._callback(buf, 40, None, None)
check("fills from the queue in order", np.array_equal(buf.reshape(-1),
                                                      np.arange(40, dtype=np.int16)))
player._callback(buf, 40, None, None)
check("continues where it left off",
      np.array_equal(buf.reshape(-1), np.arange(40, 80, dtype=np.int16)))

buf2 = np.full((40, 1), 99, dtype=np.int16)
player._callback(buf2, 40, None, None)
tail = buf2.reshape(-1)
check("pads with silence past the end",
      np.array_equal(tail[:20], np.arange(80, 100, dtype=np.int16))
      and np.all(tail[20:] == 0), f"{tail[:25]}")

player._pending.append((np.arange(50, dtype=np.int16), None))
player._pending.append((np.arange(50, 100, dtype=np.int16), None))
buf3 = np.zeros((100, 1), dtype=np.int16)
player._callback(buf3, 100, None, None)
check("joins consecutive utterances seamlessly",
      np.array_equal(buf3.reshape(-1), np.arange(100, dtype=np.int16)))

player._pending.append((np.ones(1000, dtype=np.int16), None))
check("reports as playing", player.is_playing)
player.stop()
check("stop clears the queue", not player.is_playing and player.queued == 0)
buf4 = np.full((32, 1), 7, dtype=np.int16)
player._callback(buf4, 32, None, None)
check("silence after stop", np.all(buf4 == 0))

player.enqueue(np.zeros(0, dtype=np.int16))
check("empty audio is ignored", player.queued == 0)

print("\nbarge-in through the speaker:")


class SlowEngine(TTSEngine):
    """Synthesises slowly, so a stop can land mid-synthesis."""

    def __init__(self):
        self.calls = []

    @property
    def sample_rate(self):
        return 22050

    def synthesize(self, text):
        self.calls.append(text)
        time.sleep(0.15)
        return np.ones(2205, dtype=np.int16)


class FakePlayer(AudioPlayer):
    def __init__(self):
        super().__init__(sample_rate=22050)
        self.played = []

    def start(self):
        pass  # never open a real device

    def enqueue(self, samples, tag=None):
        self.played.append(samples)


engine, fake = SlowEngine(), FakePlayer()
speaker = Speaker(engine, fake, min_sentence_chars=5)

speaker.feed("First sentence here. Second sentence here. Third sentence here. ")
time.sleep(0.05)
speaker.stop()
time.sleep(0.6)
check("stop discards queued sentences", len(fake.played) == 0,
      f"played={len(fake.played)} synthesised={len(engine.calls)}")
check("audio synthesised before the stop is not played", len(fake.played) == 0)

fake.played.clear()
speaker.feed("A brand new sentence after stopping. ")
time.sleep(0.5)
check("speaker still works after a stop", len(fake.played) >= 1,
      f"played={len(fake.played)}")

speaker._queue.put.__self__  # keep reference explicit
speaker.close()

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
