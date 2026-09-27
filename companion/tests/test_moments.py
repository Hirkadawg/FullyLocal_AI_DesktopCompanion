"""Speaking at natural stopping points, on a fake clock.

Suggestions at task boundaries are engaged with most (CHI 2025), and speaking
before the content has been taken in is premature. So, best first: something
that was playing on the page ends; the end of the page is reached; a page read
for a long time is left; and only then a page just opened. A better moment
takes the place of a waiting weaker one without adding remarks beyond a page's
limits, and each remark records the moment that led to it.
"""

import json
import sys
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

import modules.audio.loopback as loopback
import modules.perception.uia as uia
from core.attention import AttentionPolicy
from core.config import AppConfig
from core.logging import setup_logging
from core.observer import DESCRIBE
from core.orchestrator import Orchestrator
from core.types import ScreenContext
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


SAYS = ["That shipwreck detail changes how the whole story reads.",
        "Would anyone have trusted a machine that old to predict eclipses?",
        "Honestly the bronze surviving at all is the real miracle here.",
        "Who decided the fragments were worth x-raying in the first place?",
        "The inscriptions being a user manual is my favourite part.",
        "How long did it take anyone to count those gear teeth?"]


class FakeLLM:
    def __init__(self):
        self.composed, self.described = [], []

    def chat(self, messages, **kwargs):
        if messages[0].content == DESCRIBE:
            self.described.append(messages[-1].content)
            yield "reading about the Antikythera mechanism"
            return
        self.composed.append(messages[-1].content)
        yield json.dumps({"say": SAYS[(len(self.composed) - 1) % len(SAYS)],
                          "why": "it is the surprising part of the page"})


now = [0.0]


def clock():
    return now[0]


def at(t):
    now[0] = float(t)


def orchestrator(natural=True):
    llm = FakeLLM()
    orch = Orchestrator(
        llm, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100, clock=clock),
        min_time_on_page_s=10, clock=clock, natural_moments=natural, media_min_s=20,
        audio_quiet_s=2, moment_wait_s=90, long_stay_s=180, parting_window_s=30,
    )
    return orch, llm


ARTICLE = ("The Antikythera mechanism is an Ancient Greek hand-powered orrery, "
           "described as the oldest known example of an analogue computer.\n") * 10


def page(title, scroll=None, text=ARTICLE):
    return ScreenContext(text=text, window_title=title, app_name="brave.exe", source="uia",
                         scroll=scroll)


print("a video ending")

at(0)
orch, llm = orchestrator()
orch.observe(page("Antikythera documentary - YouTube"))
said = []
for t in range(5, 61, 5):
    at(t)
    said.append(orch.poll(hold=True, sound=(float(t), 0.0)))
check("while it plays, even well past time on page, nothing is said", said == [None] * 12)
at(62.5)
remark = orch.poll(hold=False, sound=(60.0, 2.5))
check("when it ends, the remark comes, marked as that moment",
      remark is not None and remark.trigger == "media_end", repr(remark))
check("...and the model is told it just finished",
      llm.composed and "just finished or paused" in llm.composed[-1])

at(100)
orch.observe(page("Gear trains explained - YouTube"))
at(115)
remark = orch.poll(sound=(1.0, 20.0))
check("a short sound earlier -- a notification -- is no such moment",
      remark is not None and remark.trigger == "arrived", repr(remark))

at(200)
orch.observe(page("Reading with music on"))
at(215)
orch.poll(hold=True, sound=(65.0, 0.0))  # the music started at 150, before this page
at(231)
remark = orch.poll(sound=(79.0, 2.5))
check("music already playing when the page opened doesn't make its end a moment",
      remark is not None and remark.trigger == "arrived", repr(remark))

at(0)
plain, _ = orchestrator(natural=False)
plain.observe(page("Antikythera documentary - YouTube"))
at(30)
plain.poll(hold=True, sound=(30.0, 0.0))
at(62.5)
remark = plain.poll(sound=(60.0, 2.5))
check("with natural moments off, the same remark is an ordinary arrival",
      remark is not None and remark.trigger == "arrived", repr(remark))

print("\nthe end of a page")

at(0)
orch, llm = orchestrator()
orch.observe(page("Antikythera mechanism - Wikipedia", scroll=10))
at(15)
check("on a page that reports scrolling, the page-opened remark waits", orch.poll() is None)
at(40)
orch.observe(page("Antikythera mechanism - Wikipedia", scroll=97))
remark = orch.poll()
check("reaching the end is the moment", remark is not None and remark.trigger == "page_end",
      repr(remark))
check("...and the model is told they just got there",
      "just reached the end of the page" in llm.composed[-1])
at(50)
orch.observe(page("Antikythera mechanism - Wikipedia", scroll=100))
check("a better moment never adds a remark past the page's limits", orch.poll() is None)

at(1000)
orch.observe(page("A long article nobody finishes", scroll=20))
at(1015)
check("not yet at the end: waiting", orch.poll() is None)
at(1095)
remark = orch.poll()
check("...but not forever: after moment_wait_s the page-opened remark comes",
      remark is not None and remark.trigger == "arrived", repr(remark))

at(2000)
orch.observe(page("An app that doesn't report scrolling", scroll=None))
at(2015)
remark = orch.poll()
check("where scrolling isn't reported, nothing changes: the remark comes on time",
      remark is not None and remark.trigger == "arrived", repr(remark))

print("\nleaving a page read for a long time")

at(0)
orch, llm = orchestrator()
orch.observe(page("Antikythera mechanism - Wikipedia"))
for t in range(15, 200, 15):
    at(t)
    orch.poll(busy=True)  # busy the whole time: no remark while they read
at(200)
orch.observe(page("Inbox - Outlook", text="Inbox\nNothing to read here"))
at(203)
remark = orch.poll()
check("moving on after a long read is a moment to remark on that page",
      remark is not None and remark.trigger == "leaving", repr(remark))
check("...told they have just moved on, after about three minutes",
      "have just moved on" in llm.composed[-1] and "about 3 minutes" in llm.composed[-1],
      llm.composed[-1][:160])
check("...and it is described from the page left, not the inbox",
      "Antikythera" in llm.described[-1])

at(300)
orch.observe(page("A page read briefly"))
at(350)
orch.observe(page("The next page"))
at(352)
check("a short stay leaves no such moment", orch.poll() is None and orch.parting is None)

at(400)
orch.observe(page("Another long read"))
for t in range(415, 600, 15):
    at(t)
    orch.poll(busy=True)
at(600)
orch.observe(page("Somewhere else"))
at(640)
late = orch.poll()
check("if the moment passes (parting_window_s), nothing is said about the page left",
      orch.parting is None and (late is None or late.trigger != "leaving"), repr(late))

at(1000)
orch.observe(page("A long read with a remark"))
at(1015)
orch.poll()
at(1300)
orch.observe(page("After it"))
check("a page that already had a remark gets no parting one", orch.parting is None)

print("\nwhere the signals come from")

fake_time = [100.0]
loopback.time.time = lambda: fake_time[0]
capture = loopback.SystemAudioCapture()
loud = np.full(800, 0.2, dtype=np.float32)
for step in range(21):
    fake_time[0] = 100.0 + step * 0.5
    capture._note_level(loud)
check("a stretch of sound is measured", abs(capture.seconds_of_sound - 10.0) < 0.01,
      str(capture.seconds_of_sound))
fake_time[0] = 111.0
capture._note_level(loud)
check("a pause under SOUND_GAP_S doesn't end it", capture.seconds_of_sound > 10.0)
fake_time[0] = 115.0
capture._note_level(loud)
check("a longer silence starts a new stretch", capture.seconds_of_sound == 0.0,
      str(capture.seconds_of_sound))
import time as real_time  # noqa: E402
loopback.time.time = real_time.time

cfg = AppConfig.load(CONFIG_PATH)
worker = CompanionWorker(cfg)
worker._companion = SimpleNamespace(audio=SimpleNamespace(
    capture=SimpleNamespace(seconds_of_sound=42.0, seconds_since_sound=3.0)))
check("the worker hands the orchestrator the latest stretch of sound",
      worker._sound_stretch() == (42.0, 3.0))
worker._companion = SimpleNamespace(audio=None)
check("...or nothing, without system-audio listening", worker._sound_stretch() is None)


class Node:
    def __init__(self, scroll=None, parent=None):
        self.scroll, self.parent = scroll, parent

    def GetPattern(self, pattern_id):
        if self.scroll is None:
            return None
        return SimpleNamespace(VerticallyScrollable=True, VerticalScrollPercent=self.scroll)

    def GetParentControl(self):
        return self.parent


check("the scroll position is read from the text control",
      uia._scroll_percent(Node(scroll=42.0)) == 42.0)
check("...or from the element around it", uia._scroll_percent(Node(parent=Node(scroll=88.0))) == 88.0)
check("...and is None when the app reports none", uia._scroll_percent(Node(parent=Node())) is None)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
