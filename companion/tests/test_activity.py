"""The activity log: readable monthly CSV files, searched in place.

What is written and where, that a person can read and edit the files, search and
date ranges, the visit the orchestrator reports on leaving a page (with how a
remark there went), that nothing is logged while the log is off or for a screen
the privacy list blocked, and that the search tool is offered only for questions
about the past.
"""

import csv
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.activity import ActivityLog, Visit, time_range
from core.attention import AttentionPolicy
from core.companion import Companion, build_tools
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.orchestrator import Orchestrator, clean_title
from core.types import ScreenContext
from modules.tools.activity import SearchActivity
from modules.tools.requests import offered
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def at(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


print("writing readable files")

folder = Path(tempfile.mkdtemp(prefix="companion-activity-")) / "activity"
log = ActivityLog(folder)
log.record(Visit(at("2026-08-30 20:00"), at("2026-08-30 20:25"), "brave.exe",
                 "Sourdough starter guide - King Arthur", "reading a sourdough guide"))
log.record(Visit(at("2026-09-14 10:00"), at("2026-09-14 10:12"), "brave.exe",
                 "Mars rover Perseverance finds organic molecules - NASA",
                 "reading about the Mars rover", remarks=1, outcome="replied"))
log.record(Visit(at("2026-09-14 11:00"), at("2026-09-14 11:30"), "brave.exe",
                 "Dişliler nasıl çalışır - YouTube", "watching a video about gears"))
log.record(Visit(at("2026-09-14 12:00"), at("2026-09-14 12:05"), "outlook.exe", "Inbox - Outlook"))

september = folder / "activity-2026-09.csv"
check("one file per month, named by month",
      sorted(p.name for p in folder.iterdir()) == ["activity-2026-08.csv", "activity-2026-09.csv"],
      str(sorted(p.name for p in folder.iterdir())))
raw = september.read_text(encoding="utf-8")
check("it starts with a header a person can read",
      raw.lstrip("﻿").splitlines()[0] == "date,start,end,minutes,app,title,activity,remarks,outcome")
check("...marked UTF-8 once at the top, so Excel shows Turkish letters", raw.count("﻿") == 1)
check("Turkish text is stored as written", "Dişliler nasıl çalışır" in raw)
rows = list(csv.DictReader(september.open(encoding="utf-8-sig", newline="")))
check("each visit is one line: date, times, minutes, app, title, description, remark",
      rows[0] == {"date": "2026-09-14", "start": "10:00", "end": "10:12", "minutes": "12",
                  "app": "brave.exe", "title": "Mars rover Perseverance finds organic molecules - NASA",
                  "activity": "reading about the Mars rover", "remarks": "1", "outcome": "replied"},
      str(rows[0]))
check("no page text or screenshots: only those columns", set(rows[0]) == {
      "date", "start", "end", "minutes", "app", "title", "activity", "remarks", "outcome"})

print("\nsearching")

found = log.search("mars rover")
check("the right page comes first", found and "Perseverance" in found[0].title,
      str([v.title for v in found]))
check("words that say nothing about the page are ignored",
      log.search("what was that article about the mars rover I read earlier")[0].title.startswith("Mars"))
check("Turkish words find Turkish titles", log.search("dişliler")[0].title.startswith("Dişliler"))
check("nothing matching finds nothing", log.search("cryptocurrency") == [])
check("newest first when nothing specific is asked",
      [v.title for v in log.search("what did I do")][:2] == ["Inbox - Outlook", "Dişliler nasıl çalışır - YouTube"])
now = at("2026-09-15 09:00")
start, end = time_range("yesterday", now)
check("'yesterday' is yesterday only",
      {v.title for v in log.visits(start, end)} == {r["title"] for r in rows}, str((start, end)))
start, end = time_range("this month", now)
check("'this month' leaves out August", all(v.started.month == 9 for v in log.visits(start, end)))
check("'any' reaches back to August", any(v.started.month == 8 for v in log.visits()))

text = september.read_text(encoding="utf-8").splitlines()
kept = [line for line in text if "Inbox" not in line] + ["this line was typed by hand,,,"]
september.write_text("\n".join(kept) + "\n", encoding="utf-8")
check("a line deleted by hand is gone from the log", all("Inbox" not in v.title for v in log.visits()))
check("...and a mangled line is skipped, not fatal", len(list(log.visits())) == 3)

print("\nwhat the orchestrator reports when a page is left")

clock_now = [1000.0]
left = []


class FakeLLM:
    """A different remark each time: a repeat would be refused as a near-repeat."""

    SAYS = ["Organic molecules on Mars is a big deal if it holds up.",
            "Would a lakebed that old really keep anything intact?",
            "Honestly the rover outlasting its mission is the better story."]

    def __init__(self):
        self.made = 0

    def chat(self, messages, **kwargs):
        from core.observer import DESCRIBE
        if messages[0].content == DESCRIBE:
            yield "reading about the Mars rover"
        else:
            import json
            say = self.SAYS[self.made % len(self.SAYS)]
            self.made += 1
            yield json.dumps({"say": say, "why": "the finding is the surprising part"})


ARTICLE = "Perseverance found organic molecules in an ancient lakebed on Mars, scientists said.\n" * 12
orch = Orchestrator(FakeLLM(), AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100,
                                               clock=lambda: clock_now[0]),
                    min_time_on_page_s=0, clock=lambda: clock_now[0],
                    on_leave=lambda c, t: left.append((c, t)))
orch.observe(ScreenContext(text=ARTICLE, window_title="(3) Mars rover - NASA", app_name="brave.exe"))
clock_now[0] += 5
orch.observe(ScreenContext(text=ARTICLE, window_title="(4) Mars rover - NASA", app_name="brave.exe"))
check("an unread counter changing is the same page, not a new visit", left == [])
remark = orch.poll()
clock_now[0] += 30
orch.note_user_message()
clock_now[0] += 60
orch.observe(ScreenContext(text="Inbox", window_title="Inbox - Outlook", app_name="outlook.exe"))
check("leaving a page reports it, with when it was left",
      len(left) == 1 and left[0][1] == 1095.0 and left[0][0].arrived_at == 1000.0, str(left))
visit = left[0][0]
check("...with its description, the remark made and that they replied",
      visit.activity.summary == "reading about the Mars rover" and visit.remarks == 1
      and visit.outcome == "replied", f"{visit.activity} {visit.remarks} {visit.outcome!r}")
check("the title is logged without its unread counter", clean_title(visit.context.window_title) == "Mars rover - NASA")

orch.observe(ScreenContext(text=ARTICLE, window_title="Another article", app_name="brave.exe"))
orch.poll()
orch.note_user_message(reply=False)
orch.leave()
check("stopping a remark counts as dismissing it; quitting reports the last page",
      left[-1][0].outcome == "dismissed", repr(left[-1][0].outcome))

print("\nwhat the worker writes")

cfg = AppConfig.load(CONFIG_PATH)
worker_folder = Path(tempfile.mkdtemp(prefix="companion-activity-worker-")) / "log"
cfg.activity.enabled, cfg.activity.folder, cfg.activity.min_seconds = True, str(worker_folder), 10
worker = CompanionWorker(cfg)
worker._record_visit(visit, visit.arrived_at + 95)
written = list(ActivityLog(worker_folder).visits())
check("a visit is written to the configured folder",
      len(written) == 1 and written[0].title == "Mars rover - NASA" and written[0].outcome == "replied",
      str(written))
short = SimpleNamespace(arrived_at=visit.arrived_at, context=visit.context, activity=None, remarks=0, outcome="")
worker._record_visit(short, visit.arrived_at + 4)
check("a visit shorter than min_seconds isn't", len(list(ActivityLog(worker_folder).visits())) == 1)
ignored = SimpleNamespace(arrived_at=visit.arrived_at, context=visit.context, activity=None, remarks=2, outcome="")
worker._record_visit(ignored, visit.arrived_at + 60)
check("a remark nobody answered is logged as ignored",
      list(ActivityLog(worker_folder).visits())[0].outcome == "ignored")
cfg.activity.enabled = False
worker._record_visit(visit, visit.arrived_at + 95)
check("with the log switched off, nothing is written", len(list(ActivityLog(worker_folder).visits())) == 2)
cfg.activity.enabled = True

blocked = CompanionWorker(cfg)
blocked._orchestrator = Orchestrator(FakeLLM(), AttentionPolicy(), on_leave=blocked._record_visit)
blocked._companion = SimpleNamespace(ambient_tick=lambda: None)  # what a blocked screen returns
blocked_folder = Path(tempfile.mkdtemp(prefix="companion-activity-blocked-"))
cfg.activity.folder = str(blocked_folder)
for _ in range(5):
    blocked._ambient_tick()
blocked._orchestrator.leave()
check("a screen the privacy list blocks never reaches the log", list(blocked_folder.iterdir()) == [])

print("\nthe search tool")

for message in ("What was that article about the Mars rover I read earlier?",
                "What did I watch yesterday?", "Dün izlediğim video neydi?", "that video about gears"):
    check(f"offered for {message!r}", offered("search_activity", message))
for message in ("Yes.", "What is this article about?", "Set a timer for 5 minutes.", "Interesting."):
    check(f"not offered for {message!r}", not offered("search_activity", message))

cfg.activity.folder = str(folder)
tool = SearchActivity(cfg)
answer = tool.run("mars rover")
check("it finds the page and says when and for how long",
      "Mars rover Perseverance" in answer and "12 min" in answer and "14 Sep" in answer, answer)
check("...and nothing else when nothing matches", "Nothing" in tool.run("cryptocurrency"))
cfg.activity.enabled = False
check("switched off, it says so", "switched off" in tool.run("mars rover"))
comp = Companion.__new__(Companion)
comp.config = cfg
check("...and the companion neither offers nor runs it", "search_activity" in comp._switched_off())
cfg.activity.enabled = True
check("it is in the tool registry", "search_activity" in build_tools(cfg).names)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
