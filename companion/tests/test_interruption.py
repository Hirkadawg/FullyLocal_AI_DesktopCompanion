"""Knowing how much of an interrupted reply was actually heard.

An answer is on screen in ~2 s and takes ~12 s to say. Stopping the companion
partway used to leave the next turn believing everything had been said, so
"go on" skipped ahead to something new -- as far as the model knew, it had
already finished.

Memory still keeps the whole reply. It is in the window, and trimming memory to
the spoken part would let the model deny saying things the transcript shows.
What changes is that the NEXT turn is told where speech stopped.
"""

import json
import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtCore import QCoreApplication

from core.attention import AttentionPolicy
from core.companion import Companion
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.orchestrator import Orchestrator
from core.types import Answer, Delivery, ScreenContext
from modules.ui import worker as worker_module
from modules.ui.worker import CompanionWorker
from modules.voice.player import AudioPlayer
from modules.voice.speaker import Speaker
from modules.voice.tts.base import TTSEngine

setup_logging("ERROR")
qt = QCoreApplication.instance() or QCoreApplication(sys.argv)
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class DeviceFreePlayer(AudioPlayer):
    def start(self):
        pass  # no real device; the test pulls audio through the callback itself


def play(player, frames):
    """Pull `frames` of audio through the output callback, as the device would."""
    player._callback(np.zeros((frames, 1), dtype=np.int16), frames, None, None)


def wait_until(condition, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class InstantEngine(TTSEngine):
    """Every sentence becomes exactly 1000 samples, so playback is countable."""

    @property
    def sample_rate(self):
        return 22050

    def synthesize(self, text):
        return np.ones(1000, dtype=np.int16)


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, json_schema=None, **kwargs):
        if json_schema is None:
            yield "reading about gears"
        else:
            yield json.dumps({"say": self.reply, "why": "a small detail worth a look"})

    def health_check(self):
        pass


print("the player reports what is playing and what has finished")

player = DeviceFreePlayer(sample_rate=22050)
check("nothing has played yet", player.progress == (None, None))
player.enqueue(np.ones(100, dtype=np.int16), tag="first")
player.enqueue(np.ones(100, dtype=np.int16), tag="second")
play(player, 50)
check("halfway through the first", player.progress == (None, "first"),
      str(player.progress))
play(player, 100)
check("first finished, second under way", player.progress == ("first", "second"),
      str(player.progress))
play(player, 50)
check("both finished", player.progress == ("second", None), str(player.progress))
player.enqueue(np.ones(100, dtype=np.int16), tag="third")
play(player, 10)
player.stop()
check("stopping clears what was playing, but not what had finished",
      player.progress == ("second", None), str(player.progress))

print("\nthe speaker turns that into how much of an utterance was heard")

player = DeviceFreePlayer(sample_rate=22050)
speaker = Speaker(InstantEngine(), player, min_sentence_chars=5)
check("before anything is said there is nothing to report",
      speaker.delivery() is None)

speaker.begin_utterance()
speaker.feed("It was found in 1901. It predicted eclipses. It tracked the games. ")
check("all three sentences reach the player", wait_until(lambda: player.queued == 3),
      f"{player.queued} queued")
d = speaker.delivery()
check("queued but not yet played: nothing heard",
      d is not None and d.finished == 0 and not d.partial and len(d.sentences) == 3,
      str(d))
check("...which is not complete", d is not None and not d.complete)

play(player, 1000)  # the first sentence, to the end
play(player, 400)   # partway into the second
d = speaker.delivery()
check("one sentence heard, the next cut off partway",
      d.heard == ("It was found in 1901.",) and d.cut_during == "It predicted eclipses.",
      f"heard={d.heard} cut={d.cut_during}")
check("sentences are recorded as written, for quoting back",
      d.sentences[2] == "It tracked the games.", str(d.sentences))

play(player, 600)   # rest of the second
play(player, 1000)  # the third
d = speaker.delivery()
check("played to the end, it is complete", d.complete and d.finished == 3, str(d))

speaker.begin_utterance()
speaker.feed("A second answer begins here. ")
wait_until(lambda: player.queued == 1)
d = speaker.delivery()
check("a new utterance is measured on its own, not against the last",
      d.finished == 0 and d.sentences == ("A second answer begins here.",), str(d))

speaker.stop()
speaker.begin_utterance()
speaker.feed("Still being written and no full stop yet")
d = speaker.delivery()
check("text still being written counts as not yet heard",
      d is not None and not d.complete and d.unqueued.startswith("Still"), str(d))
speaker.stop()
check("a stop ends the utterance", speaker.delivery() is None)
speaker.close()

print("\nthe next turn is told where speech stopped")

cfg = AppConfig.load(CONFIG_PATH)
comp = Companion.__new__(Companion)
comp.config, comp.memory, comp.audio = cfg, ConversationMemory(), None
comp.tools = None
ctx = ScreenContext(text="SCREEN CONTENT", source="uia")

plain = comp.build_messages("go on", ctx)[-1].content
check("no interruption, no note", "INTERRUPTED" not in plain)

cut = Delivery(
    sentences=("It was found in 1901.", "It predicted eclipses.", "It tracked the games."),
    finished=1,
    partial=True,
)
messages = comp.build_messages("go on", ctx, interrupted=cut)
noted = messages[-1].content
check("an interruption is noted on the turn", "INTERRUPTED" in noted)
check("it says what was heard", "It was found in 1901." in noted, noted[-260:])
check("...and which sentence it was cut off in", "It predicted eclipses." in noted)
check("...and what to do about it", "continue from that point" in noted, noted[-200:])
check("it doesn't quote what speech never reached", "It tracked the games." not in noted)
check("the question stays last", noted.rstrip().endswith("Question: go on"),
      noted[-40:])
check("the note rides on the current turn only; history is untouched",
      [m.role for m in messages] == ["system", "user"], str([m.role for m in messages]))

silent = Delivery(sentences=("It was found in 1901.",), finished=0)
check("cut off before a word was spoken is still an interruption",
      "INTERRUPTED" in comp.build_messages("go on", ctx, interrupted=silent)[-1].content)
spoken = Delivery(sentences=("It was found in 1901.",), finished=1)
check("a reply spoken to the end needs no note",
      "INTERRUPTED" not in comp.build_messages("go on", ctx, interrupted=spoken)[-1].content)
# The rule lives in the note, not the system prompt: as a standing section of
# prompts/system.md it made a bare "Yes." after a remark come back as a 31-word
# article summary in 1 of 3 tries (measurement in _interruption_note).
check("the system prompt carries no standing rule about interruptions",
      "INTERRUPTED" not in cfg.system_prompt
      and "cut off" not in cfg.system_prompt.lower())

print("\ninterrupting through the worker")

cfg = AppConfig.load(CONFIG_PATH)
cfg.proactive.enabled = False
worker = CompanionWorker(cfg)
player = DeviceFreePlayer(sample_rate=22050)
worker._speaker = Speaker(InstantEngine(), player, min_sentence_chars=5)

worker._speaker.begin_utterance()
worker._speaker.feed("First sentence of the answer. Second sentence of the answer. ")
wait_until(lambda: player.queued == 2)
play(player, 1000)
play(player, 300)
worker.cancel()
taken = worker._take_interruption()
check("cancelling mid-speech records how far it got",
      taken is not None and taken.heard == ("First sentence of the answer.",)
      and taken.cut_during == "Second sentence of the answer.", str(taken))
check("...once: the next turn uses it up", worker._take_interruption() is None)

worker._speaker.begin_utterance()
worker._speaker.feed("Short and complete. ")
wait_until(lambda: player.queued == 1)
play(player, 1000)
worker.cancel()
check("stopping after it had already finished records nothing",
      worker._take_interruption() is None)

worker._interrupted = (time.time() - worker_module.INTERRUPTION_RELEVANT_S - 1, cut)
check("an interruption from minutes ago is no longer mentioned",
      worker._take_interruption() is None)
worker._speaker.close()


class RecordingCompanion:
    def __init__(self):
        self.asked = []
        self.memory = ConversationMemory()

    def observe(self, max_age_s=0.0):
        return ScreenContext(text="SCREEN CONTENT", source="uia")

    def ask(self, question, context=None, interrupted=None):
        self.asked.append((question, interrupted))
        return Answer(context=context, chunks=iter(["Continuing."]))


answering = CompanionWorker(cfg)
answering._companion = RecordingCompanion()
answering._interrupted = (time.time(), cut)
answering._answer("go on")
check("the next question is asked with the interruption attached",
      answering._companion.asked == [("go on", cut)], str(answering._companion.asked))
answering._answer("and then?")
check("...and only that one", answering._companion.asked[-1] == ("and then?", None),
      str(answering._companion.asked[-1]))

print("\nsaying something new makes an older interruption stale")

cfg2 = AppConfig.load(CONFIG_PATH)
cfg2.proactive.enabled = True
remarking = CompanionWorker(cfg2)
remarking._orchestrator = Orchestrator(
    FakeLLM("Those gears are tiny."),
    AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100),
    min_time_on_page_s=0,
)
remarking._orchestrator.observe(
    ScreenContext(text="x " * 500, window_title="Gears", app_name="brave.exe")
)
remarking._interrupted = (time.time(), cut)
remarking._pump_events()
check("after a remark, the interruption before it no longer applies",
      remarking._interrupted is None)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
