"""The orchestrator: when a page on screen gets a remark, and what kind.

Driven by a fake clock and a fake model, so timing is exact and nothing sleeps.
"""

import json
import sys
import tempfile
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.observer import DESCRIBE, Activity
from core.orchestrator import (
    MIN_PROSE_SHARE,
    MOVE_ORDER,
    MOVES,
    REMARK_SCHEMA,
    Orchestrator,
    load_persona,
    page_identity,
    prose_share,
)
from core.types import Message, ScreenContext

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


#: Distinct remarks, so the duplicate guard doesn't refuse a fake repeating itself.
VARIED = ["Ah, the gear one.", "Bronze lasts remarkably well underwater.",
          "Who wound it, and how often?", "Sponge divers found it by accident.",
          "Imagine carrying that on a ship.", "Eclipses were a big deal back then."]


class FakeLLM:
    """A describe call gets a clause; a remark call gets JSON. say="" declines."""

    def __init__(self, say=None, why="the gears are the surprising part"):
        self.say, self.why = say, why
        self.describes, self.composes, self.calls = 0, [], []

    def reply(self):
        say = self.say
        if say is None:
            say = VARIED[(len(self.composes) - 1) % len(VARIED)]
        return json.dumps({"say": say, "why": self.why})

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, temperature=None, json_schema=None):
        if messages[0].content == DESCRIBE:
            self.describes += 1
            self.calls.append(("describe", temperature, json_schema))
            yield "reading about Greek astronomy"
        else:
            self.composes.append(messages)
            self.calls.append(("compose", temperature, json_schema))
            yield self.reply()

    def health_check(self):
        pass


ARTICLE = ("The Antikythera mechanism is an Ancient Greek hand-powered orrery, "
           "described as the oldest known example of an analogue computer.\n") * 20
SETTINGS = "\n".join(["Settings", "Find a setting", "System", "Bluetooth & devices",
                      "Network & internet", "Display", "Brightness & color", "Night light",
                      "Use warmer colors to help block blue light", "HDR",
                      "Scale 150% (Recommended)", "Display resolution 2560 x 1440",
                      "Advanced display", "Graphics", "Get help", "Give feedback"] * 3)


def page(title="Antikythera mechanism - Wikipedia", text=ARTICLE):
    return ScreenContext(text=text, window_title=title, app_name="brave.exe", source="uia")


def rig(llm=None, cooldown_s=0.0, **kw):
    clock = Clock()
    llm = llm or FakeLLM()
    policy = AttentionPolicy(cooldown_s=cooldown_s, quiet_after_user_s=0,
                             min_chars=100, clock=clock)
    return Orchestrator(llm, policy, clock=clock, **kw), llm, clock, policy


def move_asked(messages):
    """Which kind of remark a compose call asked for."""
    return next(kind for kind, how in MOVES.items() if how in messages[-1].content)


print("which page this is")

check("scrolling doesn't change it",
      page_identity(page(text="top " * 300)) == page_identity(page(text="bottom " * 300)))
check("an unread counter doesn't change it",
      page_identity(page("(3) Pantheon dome - YouTube")) == page_identity(page("Pantheon dome - YouTube"))
      and page_identity(page("Inbox (12) - Gmail")) == page_identity(page("Inbox - Gmail")))
check("nor does a notification mark",
      page_identity(page("• Discord")) == page_identity(page("Discord")))
check("but a year in the title is part of it",
      page_identity(page("Parasite (2019) - IMDb")) != page_identity(page("Parasite - IMDb")))
check("a different title is a different page",
      page_identity(page("Photosynthesis - Wikipedia")) != page_identity(page()))

print("\nprose is worth talking about; an interface is not")

check("an article reads as prose", prose_share(ARTICLE) > MIN_PROSE_SHARE,
      f"{prose_share(ARTICLE):.0%}")
check("a settings screen doesn't", prose_share(SETTINGS) < MIN_PROSE_SHARE,
      f"{prose_share(SETTINGS):.0%}")
orch, llm, clock, _ = rig(min_time_on_page_s=10)
orch.observe(page("Settings", SETTINGS))
clock.now += 60
check("a settings screen is never remarked on", orch.poll() is None)
check("...and costs no model call deciding that", not llm.calls, str(llm.calls))

print("\nwatching is free")

orch, llm, clock, _ = rig()
for i in range(15):  # flipping through tabs, a second on each
    orch.observe(page(f"Tab {i}"))
    orch.poll()
    clock.now += 1
check("fifteen tab changes cost no model calls at all", not llm.calls,
      f"{len(llm.calls)} calls")

print("\na page earns a remark once they settle in")

orch, llm, clock, _ = rig(min_time_on_page_s=10)
orch.observe(page())
clock.now += 5
check("not while they are only just arriving", orch.poll() is None)
clock.now += 5
remark = orch.poll()
check("after ten seconds on the page, it speaks",
      remark is not None and remark.text == "Ah, the gear one.", str(remark))
check("the page is described once, lazily, at that moment",
      llm.describes == 1 and len(llm.composes) == 1)
check("the remark carries its reason", remark is not None and remark.why.startswith("the gears"))

print("\na moment that isn't allowed is not lost")

# The flaw in the old event queue: blocked meant discarded.
orch, llm, clock, policy = rig(cooldown_s=75, min_time_on_page_s=10)
policy.note_spoke()
orch.observe(page())
clock.now += 20
check("held up by the cooldown, nothing is said", orch.poll() is None)
check("...and nothing is spent on it yet", not llm.calls)
clock.now += 60
check("once the cooldown ends, the remark happens", orch.poll() is not None)

orch, llm, clock, _ = rig(min_time_on_page_s=10)
orch.observe(page())
clock.now += 10
check("while busy answering, nothing is said", orch.poll(busy=True) is None)
check("...and when it's free again, the remark still happens", orch.poll() is not None)

orch, llm, clock, _ = rig(min_time_on_page_s=10)
orch.observe(page())
clock.now += 10
check("while audio plays, nothing is said", orch.poll(hold=True) is None)
check("...and once it stops, the remark still happens", orch.poll() is not None)

print("\nafter a remark it waits for something to happen")

orch, llm, clock, _ = rig(min_time_on_page_s=10, dwell_seconds=300, max_remarks_per_page=2)
orch.observe(page())
clock.now += 10
orch.poll()
clock.now += 400
check("sitting still on the page, even for minutes, earns nothing more", orch.poll() is None)
orch.observe(page(text=ARTICLE + "further down\n"))
remark = orch.poll()
check("a scroll that stops after a long stay earns a second remark",
      remark is not None and remark.trigger == "revisit", str(remark))
check("the page is still described only once", llm.describes == 1)
clock.now += 400
orch.observe(page(text=ARTICLE + "further still\n"))
check("but never more than max_remarks_per_page", orch.poll() is None)

orch, llm, clock, _ = rig(min_time_on_page_s=10, dwell_seconds=300)
orch.observe(page())
clock.now += 10
orch.poll()
clock.now += 60
orch.observe(page(text=ARTICLE + "a quick scroll\n"))
check("a scroll soon after a remark is not enough", orch.poll() is None)

print("\na page with nothing to say isn't retried every tick")

orch, llm, clock, policy = rig(FakeLLM(say=""), min_time_on_page_s=10, max_attempts_per_page=3)
orch.observe(page())
clock.now += 10
check("a declined remark is silence", orch.poll() is None)
for _ in range(20):
    clock.now += 1
    orch.poll()
check("...and isn't asked again every second", len(llm.composes) == 1,
      f"{len(llm.composes)} composes")
check("...nor counted as speaking", policy.spoken_last_hour == 0)
for _ in range(5):
    clock.now += 1  # a settle is only new if it comes after the last try
    orch.observe(page(text=ARTICLE + "scrolled\n"))
    orch.poll()
check("each scroll allows another try, up to the limit", len(llm.composes) == 3,
      f"{len(llm.composes)} composes")

print("\nmoving on starts afresh")

orch, llm, clock, _ = rig(min_time_on_page_s=10)
orch.observe(page())
clock.now += 10
orch.poll()
orch.observe(page("Photosynthesis - Wikipedia"))
clock.now += 5
check("a new page has to be settled into too", orch.poll() is None)
clock.now += 5
check("and then it can be remarked on", orch.poll() is not None)
check("each page described once", llm.describes == 2)

print("\nthe kind of remark is chosen in code")

check("no 'tell' and no 'noticing': they invented facts and restated details",
      "tell" not in MOVES and "noticing" not in MOVES and set(MOVE_ORDER) == set(MOVES))
orch, llm, clock, _ = rig(min_time_on_page_s=0)
kinds = []
for i in range(6):
    orch.observe(page(f"Page {i}"))
    remark = orch.poll()
    kinds.append(remark.move if remark else None)
check("it rotates through every kind", set(kinds) == set(MOVE_ORDER), str(kinds))
check("never the same kind twice running",
      all(a != b for a, b in zip(kinds, kinds[1:])), str(kinds))
check("the model is asked for the kind code chose",
      [move_asked(m) for m in llm.composes] == kinds)

declining = FakeLLM(say="")
orch, llm, clock, _ = rig(declining, min_time_on_page_s=0)
orch.observe(page())
orch.poll()
first = move_asked(llm.composes[-1])
clock.now += 1
orch.observe(page(text=ARTICLE + "scrolled\n"))
orch.poll()
check("a declined kind isn't simply asked for again",
      move_asked(llm.composes[-1]) != first,
      f"{first} then {move_asked(llm.composes[-1])}")

print("\nwhat the model is told")

orch, llm, clock, _ = rig(persona="PERSONA TEXT")
prompt = orch.build_prompt(Activity(summary="reading about Greek astronomy"),
                           "SCREEN TEXT HERE", dwell=30, revisit=False, move="opinion")
check("the persona is the system prompt", prompt[0].content == "PERSONA TEXT")
turn = prompt[1].content
check("the turn says what they are doing", "reading about Greek astronomy" in turn)
check("screen text is there as reference, marked as not news",
      "SCREEN TEXT HERE" in turn and "tells them nothing" in turn)
check("it asks for the chosen kind", MOVES["opinion"] in turn)
check("it asks for a reason as well as the remark", '"why"' in turn)
# Offered a way out, small models took it: qwen3.5:4b declined 3 remarks in 10
# and qwen3-vl:4b-instruct 10 in 10. Declining is decided in code instead.
check("it doesn't invite declining with empty strings", "empty strings" not in turn)
check("a first remark isn't told it is repeating itself", "already said" not in turn)
again = orch.build_prompt(Activity(summary="x"), "", dwell=600, revisit=True, move="question")
check("a revisit is told it needs a new thought", "already said" in again[1].content)
orch.recent = ["The gears are wild."]
later = orch.build_prompt(Activity(summary="x"), "", dwell=30, revisit=False, move="opinion")
check("recent remarks are listed as things not to repeat",
      "The gears are wild." in later[1].content and "Don't repeat" in later[1].content)

with tempfile.TemporaryDirectory() as folder:
    persona_path = Path(folder) / "persona.md"
    persona_path.write_text("<!-- a note for whoever edits this -->\nBe nice.\n", encoding="utf-8")
    check("comments in the persona file are for its editor, not the model",
          load_persona(persona_path) == "Be nice.", repr(load_persona(persona_path)))
real = load_persona(Path(CONFIG_PATH).parent / "prompts" / "remarks.md")
check("the shipped persona file loads, with its placeholder note stripped",
      len(real) > 100 and "PLACEHOLDER" not in real and "<!--" not in real)

print("\nguards applied in code, not trusted to the prompt")

for say, why, verdict, label in (
    ("Nice gears.", "", False, "a remark with no reason isn't made"),
    ("Check the cookie's secure flag.", "a common gotcha", False, "advice isn't a remark"),
    ("It looks like you're reading about gears.", "to show I noticed", False, "narrating isn't either"),
    ("Hey, cool page.", "just being friendly", False, "nor is a greeting"),
    ("NOTHING", "nothing to add here", False, "NOTHING is silence even inside JSON"),
    ("Honestly, the gearing is the real story here.", "a take on a specific detail", True,
     "an opinion with a reason is made"),
):
    orch, llm, clock, _ = rig(FakeLLM(say=say, why=why), min_time_on_page_s=0)
    orch.observe(page())
    check(label, (orch.poll() is not None) == verdict, say)


BUTTER = "That comment about burning the butter is a good reminder to watch the heat."
orch, llm, clock, _ = rig(FakeLLM(say=BUTTER.replace("That", "The", 1)), min_time_on_page_s=0)
orch.recent = [BUTTER]
orch.observe(page())
check("a remark too close to one said recently isn't made", orch.poll() is None)
orch, llm, clock, _ = rig(FakeLLM(say="Resting dough overnight changes how the sugar browns."),
                          min_time_on_page_s=0)
orch.recent = [BUTTER]
orch.observe(page())
check("...but a different thought about the same page is", orch.poll() is not None)


class PlainText(FakeLLM):
    def reply(self):
        return "Ah, the gear one."


orch, llm, clock, _ = rig(PlainText(), min_time_on_page_s=0)
orch.observe(page())
check("a reply that isn't JSON makes no remark", orch.poll() is None)

print("\nremarks have their own temperature and a schema; describing has neither")

orch, llm, clock, _ = rig(min_time_on_page_s=0, temperature=0.85)
orch.observe(page())
orch.poll()
check("the remark is composed at the composer's temperature, held to the schema",
      ("compose", 0.85, REMARK_SCHEMA) in llm.calls, str(llm.calls))
check("describing keeps the model's defaults", ("describe", None, None) in llm.calls)

from modules.llm.ollama_client import OllamaLLM

sent = []
provider = OllamaLLM(temperature=0.3)
provider._call = lambda kwargs: sent.append(kwargs) or {"message": {"content": "{}"}}
"".join(provider.chat([Message(role="user", content="hi")]))
"".join(provider.chat([Message(role="user", content="hi")], temperature=0.85,
                      json_schema=REMARK_SCHEMA))
check("without overrides, the request carries the configured temperature and no format",
      sent[0]["options"]["temperature"] == 0.3 and "format" not in sent[0])
check("overrides reach the request actually sent to Ollama",
      sent[1]["options"]["temperature"] == 0.85 and sent[1]["format"] == REMARK_SCHEMA)

tuned = OllamaLLM(temperature=0.3, options={"top_p": 0.8, "top_k": 20, "temperature": 9.9})
tuned._call = lambda kwargs: sent.append(kwargs) or {"message": {"content": "{}"}}
"".join(tuned.chat([Message(role="user", content="hi")], temperature=0.85))
check("configured sampling options reach the request",
      sent[-1]["options"].get("top_p") == 0.8 and sent[-1]["options"].get("top_k") == 20,
      str(sent[-1]["options"]))
check("...but never override the temperature the app chose for the call",
      sent[-1]["options"]["temperature"] == 0.85, str(sent[-1]["options"]))

print("\nthe user talking makes it wait, without losing the page")

clock = Clock()
policy = AttentionPolicy(cooldown_s=0, quiet_after_user_s=45, min_chars=100, clock=clock)
orch = Orchestrator(FakeLLM(), policy, clock=clock, min_time_on_page_s=10)
orch.observe(page())
clock.now += 10
orch.note_user_message()
check("right after they speak, nothing is said", orch.poll() is None)
clock.now += 46
check("after the quiet period, the page can still be remarked on", orch.poll() is not None)

print("\nthe worker builds it, with the persona from its file")

# The constructor check exists because a missing `import time` in worker.py
# once reached the user: --fast skipped every suite that built a worker.
from core.config import AppConfig
from modules.ui.worker import CompanionWorker

cfg = AppConfig.load(CONFIG_PATH)
for enabled in (False, True):
    cfg.proactive.enabled = enabled
    try:
        w = CompanionWorker(cfg)
        ok, detail = True, ""
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    check(f"CompanionWorker builds with proactive={enabled}", ok, detail)
check("a fresh worker is not busy", not w.is_busy())
check("muting is safe before start", w.set_muted(True) is True)


class FakeCompanion:
    def __init__(self):
        self.llm, self.memory, self.audio = FakeLLM(), ConversationMemory(), None


w = CompanionWorker(cfg)
w._companion = FakeCompanion()
w._build_orchestrator()
check("the orchestrator gets the persona file's text",
      w._orchestrator is not None and w._orchestrator.persona == real)
cfg.proactive.persona_file = "prompts/does-not-exist.md"
w = CompanionWorker(cfg)
w._companion = FakeCompanion()
try:
    w._build_orchestrator()
    ok = w._orchestrator is None
except Exception:
    ok = False
check("a missing persona file turns remarks off instead of crashing the worker", ok)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
