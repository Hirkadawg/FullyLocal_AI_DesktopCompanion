""""Say something about this": a remark on request.

Open-LLM-VTuber's "raise hand" asks the AI to speak now. Here a hotkey, a tray
item and a window button ask for a remark about the page on screen. Asking is
the moment, so every timing gate is skipped -- time on page, cooldown, hourly
budget, the per-page limit, Quiet -- while every quality check stays: an
interface or a near-empty screen is declined without a model call, and the
composed remark meets the same refusals as an unprompted one. It works with
unprompted remarks switched off, and requested remarks are marked as such.
"""

import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtWidgets import QApplication

from core.attention import AttentionPolicy
from core.config import AppConfig
from core.errors import PrivacyBlocked
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.observer import DESCRIBE
from core.orchestrator import Orchestrator
from core.types import ScreenContext
from modules.ui.app import CompanionApp
from modules.ui.hotkey import HOTKEY_REMARK, parse
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


ARTICLE = ScreenContext(
    text=("The Antikythera mechanism is an Ancient Greek hand-powered orrery, "
          "described as the oldest known example of an analogue computer.\n") * 20,
    window_title="Antikythera mechanism - Wikipedia", app_name="brave.exe", source="uia")
SETTINGS = ScreenContext(
    text="\n".join(["Settings", "Find a setting", "System", "Bluetooth & devices",
                    "Network & internet", "Display", "Brightness & color", "Night light",
                    "HDR", "Scale 150% (Recommended)", "Display resolution 2560 x 1440"]),
    window_title="Settings", app_name="SystemSettings.exe", source="uia")
SHORT = ScreenContext(text="One line of prose that has well over eight words in it.",
                      window_title="Note", app_name="notepad.exe", source="uia")

VARIED = [
    "Those tiny bronze gears are wild for something two thousand years old.",
    "Would anyone have believed a Greek shipwreck held a working computer?",
    "Honestly the fragments look more like coral than clockwork.",
    "Who taught them to cut gear teeth that precisely by hand?",
]
WHY = "the gears are the surprising part of the page"


class FakeLLM:
    """Describe gets a clause; a remark gets JSON, a different sentence each time."""

    def __init__(self):
        self.describes = self.composes = 0
        self.next_say = None

    @property
    def calls(self):
        return self.describes + self.composes

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, temperature=None, json_schema=None):
        if messages[0].content == DESCRIBE:
            self.describes += 1
            yield "reading about Greek astronomy"
            return
        say = self.next_say or VARIED[self.composes % len(VARIED)]
        self.composes += 1
        self.next_say = None
        yield json.dumps({"say": say, "why": WHY})


now = [1000.0]


def clock():
    return now[0]


print("the orchestrator: timing skipped, quality kept")

llm = FakeLLM()
memory = ConversationMemory()
policy = AttentionPolicy(cooldown_s=75, max_per_hour=25, min_chars=100, quiet_after_user_s=45)
policy.clock = clock
orch = Orchestrator(llm, policy, memory=memory, min_time_on_page_s=60,
                    max_remarks_per_page=1, clock=clock)
orch.observe(ARTICLE)
check("unprompted, a page just opened is not remarked on yet", orch.poll() is None)

remark, why_not = orch.remark_now()
check("asked for, it is remarked on straight away", remark is not None, why_not)
check("...marked as requested", remark is not None and remark.trigger == "requested",
      remark.trigger if remark else "")
check("...in one remark call (plus describing the page once)",
      llm.composes == 1 and llm.describes == 1, f"compose {llm.composes}, describe {llm.describes}")
check("...and kept in memory with its reason, like any remark",
      memory.last_reason() == (remark.text, WHY) if remark else False)

now[0] += 5
check("unprompted remarks still wait: the page has had its one remark",
      orch.poll() is None)
again, why_not = orch.remark_now()
check("asking again skips the per-page limit and the cooldown", again is not None, why_not)
check("...without describing the page again", llm.describes == 1, str(llm.describes))

policy.muted = True
check("Quiet doesn't stop a remark that was asked for", orch.remark_now()[0] is not None)
policy.muted = False

before = llm.calls
remark, why_not = orch.remark_now(SETTINGS)
check("an interface is declined", remark is None and "interface" in why_not, why_not)
check("...without a model call", llm.calls == before, f"{llm.calls - before} call(s)")

remark, why_not = orch.remark_now(SHORT)
check("a near-empty screen is declined", remark is None and "not enough" in why_not, why_not)
check("...without a model call", llm.calls == before, f"{llm.calls - before} call(s)")

orch.observe(ARTICLE)
llm.next_say = "You should read the rest of the article about the gears."
remark, why_not = orch.remark_now()
check("advice is refused on request too", remark is None and "nothing worth" in why_not,
      f"{remark!r} {why_not}")
llm.next_say = VARIED[0]
remark, why_not = orch.remark_now()
check("so is repeating something said recently", remark is None, repr(remark))

empty = Orchestrator(FakeLLM(), AttentionPolicy(), clock=clock)
check("with nothing read yet, it says so", empty.remark_now()[1].startswith("nothing on screen"))

print("\nthe persona's examples never come back as remarks")

# Seen in use at launch: on a chat window full of test output, the model said
# the persona's "Wait, if the ice core is that old, how did they date the
# bottom layer?" word for word, with a reason it made up.
from core.orchestrator import load_persona, persona_examples  # noqa: E402

cfg_for_persona = AppConfig.load(CONFIG_PATH)
persona = load_persona(cfg_for_persona.root / cfg_for_persona.proactive.persona_file)
examples = persona_examples(persona)
check("the persona's example remarks are recognised",
      any("ice core" in e for e in examples) and len(examples) >= 5, str(len(examples)))
copier = FakeLLM()
copying = Orchestrator(copier, AttentionPolicy(min_chars=100, clock=clock), persona=persona, clock=clock)
copying.observe(ARTICLE)
copier.next_say = "Wait, if the ice core is that old, how did they date the bottom layer?"
check("an example said word for word is refused", copying.remark_now()[0] is None)
copying.observe(ARTICLE)
copier.next_say = "Wait -- if that ice core is so old, how did anyone date its bottom layer?"
check("...and so is a lightly reworded copy", copying.remark_now()[0] is None)
copier.next_say = VARIED[1]
check("an original remark still goes through", copying.remark_now()[0] is not None)

print("\nthe worker, with unprompted remarks switched off")


class FakeSpeaker:
    is_speaking = False

    def __init__(self):
        self.fed = []

    def begin_utterance(self):
        pass

    def feed(self, text):
        self.fed.append(text)

    def flush(self):
        pass


class FakeCompanion:
    def __init__(self, context):
        self.llm = FakeLLM()
        self.memory = ConversationMemory()
        self.audio = None
        self.context = context

    def observe(self, max_age_s=0.0):
        if isinstance(self.context, Exception):
            raise self.context
        return self.context


cfg = AppConfig.load(CONFIG_PATH)
cfg.proactive.enabled = False
cfg.proactive.speak_aloud = True
worker = CompanionWorker(cfg)
worker._companion = FakeCompanion(ARTICLE)
worker._speaker = FakeSpeaker()
worker._build_orchestrator()
check("the orchestrator exists even with remarks off", worker._orchestrator is not None)

shown, declined = [], []
worker.remarked.connect(lambda text, why, remark: shown.append((text, why)))
worker.remark_declined.connect(declined.append)
worker._orchestrator.min_time_on_page_s = 0
worker._orchestrator.observe(ARTICLE)
worker._pump_events()
check("switched off, nothing is said unprompted", shown == [], str(shown))

worker._remark_now()
check("asked for, a remark is shown with its reason", len(shown) == 1 and shown[0][1] == WHY,
      str(shown))
check("...and spoken", len(worker._speaker.fed) == 1 and shown and shown[0][0] in worker._speaker.fed[0],
      str(worker._speaker.fed))

worker._companion.context = SETTINGS
worker._remark_now()
check("on an interface it declines in the window", len(declined) == 1 and "interface" in declined[0],
      str(declined))
check("...and says so briefly", worker._speaker.fed[-1].startswith("Nothing worth saying"),
      str(worker._speaker.fed[-1:]))

worker._companion.context = PrivacyBlocked(
    SimpleNamespace(reason="blocked process", window="KeePass"))
worker._remark_now()
check("a window on the privacy list stops it before anything is read",
      len(declined) == 2 and "privacy" in declined[1], str(declined))

print("\nhotkey, tray and window")

keys = [cfg.ui.hotkey, cfg.speech.hotkey, cfg.proactive.remark_now_hotkey]
check("the hotkey parses", bool(cfg.proactive.remark_now_hotkey) and parse(keys[2]) is not None)
check("...and differs from the other two", len({parse(k) for k in keys}) == 3, str(keys))

requests, toggles = [], []
fake = SimpleNamespace(
    talk=None,
    worker=SimpleNamespace(remark_now=lambda: requests.append(1), is_busy=lambda: False),
    window=SimpleNamespace(set_status=lambda text: None),
    toggle=lambda: toggles.append(1),
)
fake._on_remark_now = lambda: CompanionApp._on_remark_now(fake)
CompanionApp._on_hotkey(fake, HOTKEY_REMARK)
check("the hotkey asks the worker for a remark", requests == [1] and toggles == [],
      f"requests {requests}, toggles {toggles}")

qt = QApplication.instance() or QApplication(sys.argv)
window = ChatWindow(cfg)
clicked = []
window.remark_requested.connect(lambda: clicked.append(1))
window.say_button.click()
check("the window's say button asks too, with remarks switched off", clicked == [1])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
