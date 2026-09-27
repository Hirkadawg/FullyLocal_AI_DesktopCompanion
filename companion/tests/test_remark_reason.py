"""A remark shows why it was made; the reason is shown, never spoken.

A CHI 2025 study found proactive help disrupted people less when they could see
why it came. The orchestrator has required a `why` since Stage 2, but only the
remark reached the user. Now the reason goes under the remark in the window and
into the tray notification, is kept beside the remark in memory, and reaches the
model only when the user asks why.
"""

import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtWidgets import QApplication

from core.attention import AttentionPolicy
from core.companion import Companion
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.observer import DESCRIBE
from core.orchestrator import Orchestrator
from core.types import ScreenContext
from modules.ui.app import CompanionApp
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


SAY = "Those tiny bronze gears are wild for something two thousand years old."
WHY = "the gears are the surprising part of the page"
ARTICLE = ("The Antikythera mechanism is an Ancient Greek hand-powered orrery, "
           "described as the oldest known example of an analogue computer.\n") * 20


class FakeLLM:
    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, temperature=None, json_schema=None):
        if messages[0].content == DESCRIBE:
            yield "reading about Greek astronomy"
        else:
            yield json.dumps({"say": SAY, "why": WHY})


def orchestrator(memory):
    orch = Orchestrator(
        FakeLLM(),
        AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100),
        memory=memory,
        min_time_on_page_s=0,
    )
    orch.observe(ScreenContext(text=ARTICLE, window_title="Antikythera mechanism - Wikipedia",
                               app_name="brave.exe", source="uia"))
    return orch


cfg = AppConfig.load(CONFIG_PATH)
cfg.proactive.enabled = True
check("the reason is shown by default", cfg.proactive.show_reason is True)

print("\nthe orchestrator keeps the reason beside the remark")

memory = ConversationMemory()
remark = orchestrator(memory).poll()
check("the remark carries its reason", remark is not None and remark.why == WHY,
      repr(remark))
check("memory knows why the last thing it said was said",
      memory.last_reason() == (SAY, WHY), repr(memory.last_reason()))
check("...but the reason is not in the message the model is sent every turn",
      all(WHY not in m.content for m in memory.history()))

print("\nthrough the worker: to the window, not to the speaker")


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


worker = CompanionWorker(cfg)
worker._orchestrator = orchestrator(ConversationMemory())
worker._speaker = FakeSpeaker()
cfg.proactive.speak_aloud = True
shown = []
worker.remarked.connect(lambda text, why, remark: shown.append((text, why)))
worker._pump_events()
check("the window is sent the remark and its reason", shown == [(SAY, WHY)], str(shown))
spoken = "".join(worker._speaker.fed)
check("the remark is spoken", SAY in spoken, repr(spoken))
check("...and the reason never is", WHY not in spoken, repr(spoken))

print("\nin the window and the tray")

qt = QApplication.instance() or QApplication(sys.argv)


class FakeTray:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, message, icon, ms):
        self.messages.append(message)


def show(show_reason):
    cfg.proactive.show_reason = show_reason
    app = SimpleNamespace(config=cfg, window=ChatWindow(cfg), tray=FakeTray(), icon=None,
                          _idle_status="ready")
    CompanionApp._on_remark(app, SAY, WHY)
    return app, app.window.transcript.toPlainText()


app, text = show(True)
check("the transcript shows the remark", SAY in text, repr(text))
check("...with its reason on a line underneath", f"{SAY}\nwhy: {WHY}" in text, repr(text))
check("a hidden window's tray notification carries the reason too",
      len(app.tray.messages) == 1 and WHY in app.tray.messages[0], str(app.tray.messages))

app, text = show(False)
check("with show_reason off, no reason line", "why:" not in text and SAY in text, repr(text))
check("...and the tray gets the remark alone", app.tray.messages == [SAY], str(app.tray.messages))
cfg.proactive.show_reason = True

print("\nasking why")

comp = Companion.__new__(Companion)
comp.config, comp.audio = cfg, None
comp.tools = None
comp.memory = ConversationMemory()
comp.memory.add_remark(SAY, WHY)
PAGE = ScreenContext(text=ARTICLE, window_title="Antikythera mechanism - Wikipedia",
                     app_name="brave.exe", source="uia")


def turn(question):
    return comp.build_messages(question, PAGE)[-1].content


for question in ("Why did you say that?", "why?", "What made you bring that up?",
                 "How come you mentioned the gears?", "Neden bunu söyledin?"):
    content = turn(question)
    check(f"{question!r} hands over the reason", "WHY YOU SAID IT" in content and WHY in content)
for question in ("What is this article about?", "Yes.", "Why were the gears bronze?",
                 "Interesting."):
    check(f"{question!r} doesn't", "WHY YOU SAID IT" not in turn(question))

comp.memory.add_turn("How old is it?", "About 2,100 years.")
check("once an answer follows the remark, 'why?' is about the answer, not the remark",
      "WHY YOU SAID IT" not in turn("why?"))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
