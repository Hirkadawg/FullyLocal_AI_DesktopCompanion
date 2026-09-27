"""Looking at the screen when asked, and watching it for remarks.

Reported: when it got the screen wrong and the user asked it to just look, it
said it couldn't -- or, with only the screen's text, "yes, I can see your
screen". Measured before on qwen3.5:4b: asked to look while the text was a
moment stale, 0 of 6 answers came from the screen; with seeing switched off,
"can you see my screen?" got "yes" 5 of 6.

Now "look at my screen" ("look again", "ekranıma bak", ...) gets a screenshot
taken that moment, with a note that it outranks the text; with seeing off, a
note to say so. "watch my screen" starts watching: while the settings allow it,
each remark gets a screenshot of its own, which nothing keeps once the remark is
written. "stop watching", or closing the app, ends it.
"""

import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PIL import Image
from PySide6.QtWidgets import QApplication

from core.attention import AttentionPolicy
from core.companion import Companion
from core.config import AppConfig, VisionConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.observer import DESCRIBE
from core.orchestrator import Orchestrator
from core.settings import SETTINGS
from core.types import ScreenContext
from core.vision import asks_to_look, wants_image, watch_request
from modules.ui.app import CompanionApp
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


qt = QApplication.instance() or QApplication(sys.argv)
ARTICLE = (
    "The Antikythera mechanism is an ancient Greek hand-powered orrery, described as the oldest known "
    "example of an analogue computer. It was used to predict astronomical positions and eclipses decades "
    "in advance, and to track the four-year cycle of athletic games. The artefact was among wreckage "
    "retrieved from a shipwreck off the coast of the Greek island Antikythera in 1901. In 1902, during a "
    "visit to the National Archaeological Museum in Athens, it was noticed by the archaeologist Valerios "
    "Stais to contain a gear wheel. The device, housed in the remains of a wooden-framed case, was found "
    "as one lump, later separated into three main fragments which are now divided into 82 separate "
    "fragments after conservation efforts."
)

print("what asks it to look")

LOOK = ["Look at my screen. What's on it?", "check my screen", "You're wrong, look again.", "Take a look.",
        "can you see my screen?", "watch my screen", "Ekranıma bak.", "Ekranımı kontrol et, ne var?",
        "ekranımı görebiliyor musun", "tekrar bak"]
NOT_LOOK = ["What is this article about?", "I look forward to it.", "check the timer",
            "Bu makale ne hakkında?", "My screen is too bright, any tips?", "Stop looking at my screen."]
check("asking to look, in English and Turkish", not [q for q in LOOK if not asks_to_look(q)],
      str([q for q in LOOK if not asks_to_look(q)]))
check("...and not other messages, nor asking it to stop", not [q for q in NOT_LOOK if asks_to_look(q)],
      str([q for q in NOT_LOOK if asks_to_look(q)]))
check("asked to look, a page full of text still gets a screenshot",
      wants_image("thin_text", "Look at my screen.", ARTICLE, 400)
      and not wants_image("thin_text", "What is this article about?", ARTICLE, 400))
check("...unless seeing is set to never", not wants_image("never", "Look at my screen.", ARTICLE, 400))

print("\nwhat starts and stops watching")

cases = {"watch my screen": "start", "Keep an eye on my screen.": "start", "keep watching my screen": "start",
         "Ekranımı izle.": "start", "ekranımı izler misin": "start",
         "stop watching": "stop", "Stop looking at my screen.": "stop", "don't watch my screen": "stop",
         "izlemeyi bırak": "stop", "ekranımı izleme": "stop",
         "I'll keep watching this show.": None, "ekranda izlediğim video ne?": None,
         "What am I watching?": None, "Look at my screen.": None}
wrong = {m: (watch_request(m), want) for m, want in cases.items() if watch_request(m) != want}
check("watch and stop, in English and Turkish -- 'ekranımı izleme' is a stop, 'keep watching this show' nothing",
      not wrong, str(wrong))


class Screen:
    is_live = False

    def __init__(self, fail=False):
        self.grabs, self.fail = 0, fail

    def grab(self):
        if self.fail:
            raise RuntimeError("capture failed")
        self.grabs += 1
        return Image.new("RGB", (80, 50), (40 * self.grabs % 256, 90, 160))


class Capturing:
    def __init__(self):
        self.seen = []

    def chat(self, messages, images=None, tools=None, stream=False, collect_tool_calls=None, **kwargs):
        self.seen.append(messages)
        yield "OK."


def companion(vision=True, watch=True, screen=None):
    comp = Companion.__new__(Companion)
    comp.config = AppConfig.load(CONFIG_PATH)
    comp.config.vision.enabled, comp.config.vision.watch_remarks = vision, watch
    comp.tools, comp.memory, comp.audio = None, ConversationMemory(), None
    comp.screen, comp.llm = screen or Screen(), Capturing()
    return comp


READ = ScreenContext(text=ARTICLE, window_title="Antikythera mechanism - Wikipedia", app_name="brave.exe",
                     source="ocr", image=Image.new("RGB", (80, 50), "white"))


def turn(comp, message):
    comp.ask(message, context=READ).text()
    return comp.llm.seen[-1][-1]


print("\nanswers")

comp = companion()
last = turn(comp, "Look at my screen. What's on it?")
check("asked to look, a screenshot taken that moment goes with the answer -- not the image the text came from",
      len(last.images or ()) == 1 and comp.screen.grabs == 1, f"grabs {comp.screen.grabs}")
check("...with a note that it was taken just now and outranks what was said",
      "[SCREENSHOT —" in last.content and "the screenshot is right" in last.content)
check("...and the screen's text left out -- with it there, the model measurably answered from the text",
      ARTICLE[:60] not in last.content and "[SCREEN TEXT left out" in last.content)
last = turn(comp, "What's in this photo?")
check("a visual question still uses the image the text was read from, with the text",
      len(last.images or ()) == 1 and comp.screen.grabs == 1 and "[SCREENSHOT" not in last.content
      and ARTICLE[:60] in last.content)
last = turn(comp, "What is this article about?")
check("an ordinary question gets neither", not last.images and "SCREENSHOT" not in last.content
      and "CAN'T SEE" not in last.content)
comp = companion(vision=False)
last = turn(comp, "ekranımı görebiliyor musun")
check("seeing switched off: no screenshot, and told to say so rather than claim to see",
      not last.images and comp.screen.grabs == 0 and "switched off" in last.content
      and "never say you can see it" in last.content)
check("...answering from the text, which is kept", ARTICLE[:60] in last.content)
comp = companion(screen=Screen(fail=True))
last = turn(comp, "Look again.")
check("a screenshot that can't be taken is said, not pretended",
      not last.images and "no screenshot could be taken" in last.content)

print("\nwatching")

comp = companion()
last = turn(comp, "watch my screen")
check("'watch my screen' starts watching, says so, and looks now too",
      comp.watching and "[WATCHING —" in last.content and len(last.images or ()) == 1)
last = turn(comp, "stop watching")
check("'stop watching' ends it and says so, with no screenshot",
      not comp.watching and "[STOPPED WATCHING —" in last.content and not last.images)
comp = companion(watch=False)
last = turn(comp, "watch my screen")
check("with the setting off it doesn't start, and says why", not comp.watching and "[NOT WATCHING —" in last.content)
comp = companion(vision=False)
last = turn(comp, "watch my screen")
check("...nor with seeing off", not comp.watching and "NOT WATCHING" in last.content and "CAN'T SEE" in last.content)
check("off in code, on in config.yaml, and on the settings page",
      VisionConfig().watch_remarks is False and AppConfig.load(CONFIG_PATH).vision.watch_remarks is True
      and any(s.key == "vision.watch_remarks" and s.kind == "bool" for s in SETTINGS))

cfg = AppConfig.load(CONFIG_PATH)
cfg.vision.enabled = cfg.vision.watch_remarks = True
worker = CompanionWorker(cfg)
worker._companion = SimpleNamespace(watching=True)
allowed = worker._watching_screen()
cfg.vision.watch_remarks = False
setting_off = worker._watching_screen()
cfg.vision.watch_remarks, cfg.vision.enabled = True, False
vision_off = worker._watching_screen()
cfg.vision.enabled, worker._companion.watching = True, False
not_asked = worker._watching_screen()
check("remarks get screenshots only while asked AND both settings allow it, checked at each remark",
      allowed and not setting_off and not vision_off and not not_asked,
      str((allowed, setting_off, vision_off, not_asked)))

print("\nremarks while watching")

VARIED = ["Ah, the gear one.", "Bronze lasts remarkably well underwater.", "Who wound it, and how often?",
          "Sponge divers found it by accident.", "Imagine carrying that on a ship."]


class FakeLLM:
    def __init__(self):
        self.composes = []

    def chat(self, messages, images=None, tools=None, stream=False, collect_tool_calls=None,
             temperature=None, json_schema=None):
        if messages[0].content == DESCRIBE:
            yield "reading about Greek astronomy"
        else:
            self.composes.append(messages)
            yield json.dumps({"say": VARIED[len(self.composes) % len(VARIED)],
                              "why": "the gears are the surprising part"})


screen, asked, llm, memory = Screen(), [True], FakeLLM(), ConversationMemory()
orch = Orchestrator(llm, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0), min_time_on_page_s=0,
                    memory=memory, screenshot=screen.grab, watching=lambda: asked[0])


def remark(n):
    orch.observe(ScreenContext(text=ARTICLE, window_title=f"Page {n} - Wikipedia", app_name="brave.exe",
                               source="uia"))
    return orch.poll()


first = remark(1)
sent = llm.composes[-1][-1] if llm.composes else None
check("watching, a remark is written with a screenshot taken for it",
      first is not None and sent is not None and len(sent.images or ()) == 1 and screen.grabs == 1
      and "in the attached screenshot, taken just now" in sent.content, f"grabs {screen.grabs}")
check("...instead of the page's text, which measurably drowned it out", sent is not None
      and ARTICLE[:60] not in sent.content)
second = remark(2)
check("...each remark its own, taken at that moment", second is not None and screen.grabs == 2)
check("...and nothing keeps it afterwards: not the page, not the orchestrator, not memory",
      orch.current.image is None and not any(isinstance(v, bytes) for v in vars(orch).values())
      and all(not getattr(m, "images", None) for m in memory.history()))
asked[0] = False
third = remark(3)
sent = llm.composes[-1][-1]
check("not watching, remarks read the text alone, as before",
      third is not None and not sent.images and screen.grabs == 2 and "attached" not in sent.content
      and ARTICLE[:60] in sent.content)

print("\nthe window")


class FakeCompanion:
    watching = False
    memory = ConversationMemory()

    def observe(self, max_age_s=0.0):
        return READ

    def ask(self, question, context=None, interrupted=None):
        self.watching = question == "watch my screen"
        return SimpleNamespace(context=context, chunks=iter(["OK."]))


worker = CompanionWorker(cfg)
worker._companion = FakeCompanion()
emitted = []
worker.watching_screen.connect(emitted.append)
worker._answer("watch my screen")
worker._answer("what now?")
check("the window is told when watching starts and stops", emitted == [True, False], str(emitted))
notices = []
fake = SimpleNamespace(window=SimpleNamespace(add_notice=lambda text, colour="": notices.append(text)))
CompanionApp._on_watching_screen(fake, True)
CompanionApp._on_watching_screen(fake, False)
check("...and says so, with how to stop", "stop watching" in notices[0] and notices[1].startswith("Stopped watching"),
      str(notices))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
