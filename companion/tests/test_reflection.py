"""Daily reflection into an editable facts file, with a scripted model.

The model proposes; code decides. A fact needs the user's own words or several
different pages, its words must be in that evidence, nothing sensitive gets in,
a deleted fact stays deleted, the user's own lines are left alone, it runs at
most once a day and only while the user is away and nothing is busy, and the
facts reach answers and remarks as context.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.activity import ActivityLog, Visit
from core.attention import AttentionPolicy
from core.companion import Companion
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.orchestrator import Orchestrator
from core.reflection import about_facts, gather, judge, reflect
from core.types import ScreenContext
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


NOW = datetime(2026, 9, 14, 21, 0)
folder = Path(tempfile.mkdtemp(prefix="companion-reflection-"))
log = ActivityLog(folder / "activity")
PAGES = [
    (4, "Mars rover Perseverance finds organic molecules - NASA", "reading about space exploration on Mars"),
    (3, "SpaceX Starship launch recap - The Verge", "reading about a space rocket launch"),
    (2, "James Webb telescope's deepest image - ESA", "reading about space telescopes and exploration"),
    (1, "Sourdough starter guide - King Arthur Baking", "reading a bread baking guide"),
    (1, "Early signs of diabetes - Mayo Clinic", "reading about diabetes symptoms"),
    (1, "Type 2 diabetes treatment options - NHS", "reading about diabetes treatment"),
    (0, "Blood sugar levels explained - Healthline", "reading about blood sugar and diabetes"),
]
for days_ago, title, activity in PAGES:
    started = NOW - timedelta(days=days_ago, hours=3)
    log.record(Visit(started, started + timedelta(minutes=15), "brave.exe", title, activity))
SAID = [(NOW - timedelta(hours=2), "I've been learning Turkish for a year now"),
        (NOW - timedelta(hours=1), "My wife hates this song")]

items = gather(log, SAID, NOW)
number = {(_kind, (v.title if _kind == "visit" else v[1])): i for i, (_kind, v) in enumerate(items, start=1)}


def n(text):
    return next(i for (kind, key), i in number.items() if text in key)


SPACE = [n("Mars rover"), n("Starship"), n("James Webb")]
print("checking what the model proposes")

cases = [
    ({"fact": "Reads a lot about space exploration", "kind": "habit", "evidence": SPACE}, True, ""),
    ({"fact": "Loves baking bread", "kind": "habit", "evidence": [n("Sourdough")]}, False, "page"),
    ({"fact": "Reads about blood sugar levels", "kind": "habit",
      "evidence": [n("Early signs"), n("Type 2"), n("Blood sugar")]}, False, "sensitive"),
    ({"fact": "Is learning Turkish", "kind": "said", "evidence": [n("learning Turkish")]}, True, ""),
    ({"fact": "Dislikes the song their wife hates", "kind": "said", "evidence": [n("My wife")]}, False, "sensitive"),
    ({"fact": "Enjoys cooking shows", "kind": "habit", "evidence": SPACE}, False, "not what"),
    ({"fact": "Is learning Japanese", "kind": "said", "evidence": [n("learning Turkish")]}, False, "not in their words"),
    ({"fact": "Reads a lot about space exploration", "kind": "habit", "evidence": [999]}, False, "no evidence"),
    ({"fact": "Is fascinated by the history of rockets and space travel in general", "kind": "habit",
      "evidence": SPACE[:2]}, False, "page"),
    # Measured: the real model labelled this habit "said" five times in five.
    ({"fact": "Reads about space exploration often", "kind": "said", "evidence": SPACE}, True, ""),
]
for proposal, accepted, reason in cases:
    fact, why = judge(proposal, items)
    ok = (fact is not None) == accepted and (accepted or reason in why)
    check(f"{'accepted' if accepted else 'kept out'}: {proposal['fact']!r}", ok, why)

fact, _ = judge(cases[0][0], items)
check("a habit's line says how many pages and when",
      fact.line() == "- Reads a lot about space exploration (3 pages, 10-12 Sep)", fact.line())


class ScriptedLLM:
    def __init__(self, proposals):
        self.proposals, self.prompts = proposals, []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages[-1].content)
        yield json.dumps({"facts": self.proposals})


print("\nwriting the file")

about = folder / "about_you.md"
llm = ScriptedLLM([c[0] for c in cases])
report = reflect(llm, about, log, SAID, now=NOW)
text = about.read_text(encoding="utf-8")
check("only the facts that passed are written, a near-duplicate once",
      about_facts(about) == ["Reads a lot about space exploration", "Is learning Turkish"],
      str(about_facts(about)))
check("...with where each came from", "(3 pages, 10-12 Sep)" in text and "(you said so, 14 Sep)" in text, text)
check("...under a note saying the file is theirs to edit", "Edit or" in text and "never written again" in text)
check("the model was shown numbered pages and words, never page text",
      "[1]" in llm.prompts[0] and 'they said: "I\'ve been learning Turkish' in llm.prompts[0])
check("everything kept out is reported with a reason",
      len(report.rejected) == 7 and all(why for _, why in report.rejected), str(report.rejected))
check("it runs at most once a day", reflect(llm, about, log, SAID, now=NOW).skipped == "already reflected today")

print("\nthe user's edits")

lines = about.read_text(encoding="utf-8").splitlines()
lines = [line for line in lines if "Turkish" not in line] + ["- Plays chess on weekends"]
about.write_text("\n".join(lines) + "\n", encoding="utf-8")
tomorrow = NOW + timedelta(days=1)
log.record(Visit(tomorrow - timedelta(hours=2), tomorrow - timedelta(hours=1), "brave.exe",
                 "Europa Clipper mission overview - NASA", "reading about space exploration of Europa"))
items2 = gather(log, SAID, tomorrow)  # the new page shifts the numbers
number2 = {(kind, (v.title if kind == "visit" else v[1])): i for i, (kind, v) in enumerate(items2, start=1)}


def n2(text):
    return next(i for (kind, key), i in number2.items() if text in key)


again = ScriptedLLM([
    {"fact": "Is learning Turkish", "kind": "said", "evidence": [n2("learning Turkish")]},
    {"fact": "Reads a lot about space exploration", "kind": "habit",
     "evidence": [n2("Mars rover"), n2("Starship"), n2("James Webb"), n2("Europa")]},
    {"fact": "Plays chess on the weekends", "kind": "said", "evidence": [n2("learning Turkish")]},
])
report = reflect(again, about, log, SAID, now=tomorrow)
facts = about_facts(about)
check("a fact the user deleted is not written again", "Is learning Turkish" not in facts,
      str(report.rejected))
check("...and says why", ("Is learning Turkish", "you deleted it before") in report.rejected, str(report.rejected))
check("a fact seen again is updated in place, not duplicated",
      facts.count("Reads a lot about space exploration") == 1
      and report.updated == ["Reads a lot about space exploration"], str(report.updated))
check("the user's own line is left exactly as they wrote it",
      "- Plays chess on weekends" in about.read_text(encoding="utf-8").splitlines())

print("\nwhen it runs")

cfg = AppConfig.load(CONFIG_PATH)
cfg.reflection.enabled, cfg.reflection.idle_min = True, 10
worker = CompanionWorker(cfg)
worker._companion = SimpleNamespace(llm=ScriptedLLM([]))
calls = []
import core.reflection as reflection_module  # noqa: E402
real_reflect = reflection_module.reflect
reflection_module.reflect = lambda *a, **k: calls.append(k["now"]) or SimpleNamespace(rejected=[])
start = datetime(2026, 9, 14, 20, 0).timestamp()
worker._last_user_at = start
check("not while they have only just been active", not worker._maybe_reflect(now=start + 5 * 60))
worker._answering.set()
check("not while it is busy", not worker._maybe_reflect(now=start + 15 * 60))
worker._answering.clear()
check("after idle_min away, it runs", worker._maybe_reflect(now=start + 15 * 60) and len(calls) == 1)
check("...once that day", not worker._maybe_reflect(now=start + 60 * 60) and len(calls) == 1)
check("...and again the next day", worker._maybe_reflect(now=start + 26 * 3600) and len(calls) == 2)
cfg.reflection.enabled = False
worker._reflected_on = ""
check("switched off, never", not worker._maybe_reflect(now=start + 50 * 3600))
reflection_module.reflect = real_reflect

said_worker = CompanionWorker(cfg)
said_worker._companion = SimpleNamespace(
    observe=lambda max_age_s=0: ScreenContext(text="x", source="uia"),
    ask=lambda q, context=None, interrupted=None: SimpleNamespace(chunks=iter([])))
try:
    said_worker._answer("I've been learning Turkish for a year now")
except Exception:
    pass
check("what they say is kept (in memory) for reflection",
      said_worker._said and said_worker._said[-1][1] == "I've been learning Turkish for a year now")

print("\nfacts as context")

cfg = AppConfig.load(CONFIG_PATH)
cfg.reflection.file = str(about)
comp = Companion.__new__(Companion)
comp.config, comp.audio, comp.memory = cfg, None, ConversationMemory()
comp.tools = None
PAGE = ScreenContext(text="An article.", window_title="Article", app_name="brave.exe", source="uia")
content = comp.build_messages("What should I read next?", PAGE)[-1].content
check("an answer gets the facts, marked as context", "[ABOUT THEM" in content
      and "Reads a lot about space exploration" in content, content[:200])
check("a question about something else gets none of them",
      "[ABOUT THEM" not in comp.build_messages("What is this article about?", PAGE)[-1].content)
related = comp.build_messages("Any news on space exploration today?", PAGE)[-1].content
check("...while one sharing a fact's words gets that fact only",
      "Reads a lot about space exploration" in related and "Turkish" not in related, related[:300])
from core.reflection import relevant_facts  # noqa: E402
FACTS = ["Reads a lot about space exploration", "Is learning Turkish"]
check("a question about them gets every fact",
      relevant_facts(FACTS, "What should I watch tonight?") == FACTS
      and relevant_facts(FACTS, "Bana bir şey öner") == FACTS)
check("an unrelated question gets none", relevant_facts(FACTS, "What time is it?") == [])
cfg.reflection.use_in_replies = False
check("...unless switched off", "[ABOUT THEM" not in comp.build_messages("What should I read?", PAGE)[-1].content)
cfg.reflection.use_in_replies = True
cfg.reflection.file = str(folder / "nothing_here.md")
check("...and there is no block when there are no facts",
      "[ABOUT THEM" not in comp.build_messages("Hi", PAGE)[-1].content)


class RemarkLLM:
    def __init__(self):
        self.composed = []

    def chat(self, messages, **kwargs):
        from core.observer import DESCRIBE
        if messages[0].content == DESCRIBE:
            yield "reading about Mars"
            return
        self.composed.append(messages[-1].content)
        says = ["Organic molecules on Mars would rewrite a lot of textbooks.",
                "Would a sample return mission even fit in that budget?",
                "Honestly the rover's camera shots are the best part of this."]
        yield json.dumps({"say": says[len(self.composed) - 1], "why": "the finding is surprising"})


clock_now = [datetime(2026, 9, 14, 10, 0).timestamp()]
remarks = RemarkLLM()
ARTICLE = "Perseverance found organic molecules in an ancient lakebed on Mars, scientists said.\n" * 12
orch = Orchestrator(remarks, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100,
                                             clock=lambda: clock_now[0]),
                    min_time_on_page_s=0, clock=lambda: clock_now[0],
                    about=lambda c: ["Reads a lot about space exploration"],
                    recall=lambda c: "SpaceX Starship launch recap (Friday)", callbacks=True)
orch.observe(ScreenContext(text=ARTICLE, window_title="Mars rover - NASA", app_name="brave.exe"))
orch.poll()
check("a remark gets the facts as context", "About them, for context only: Reads a lot about space"
      in remarks.composed[-1], remarks.composed[-1][:200])
check("...and, with callbacks, a related page from an earlier day",
      "On an earlier day they were on: SpaceX Starship launch recap (Friday)" in remarks.composed[-1])
clock_now[0] += 3600
orch.observe(ScreenContext(text=ARTICLE, window_title="Mars sample return - ESA", app_name="brave.exe"))
orch.poll()
check("...but only one remark a day gets an earlier page", "earlier day" not in remarks.composed[-1])
clock_now[0] += 86400
orch.observe(ScreenContext(text=ARTICLE, window_title="Mars helicopter - NASA", app_name="brave.exe"))
orch.poll()
check("...and the next day, one may again", "earlier day" in remarks.composed[-1])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
