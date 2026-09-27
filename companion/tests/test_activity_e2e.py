"""The activity log with the real model: "what was that article I read earlier?"

Three earlier visits are in the log; the screen shows something else entirely
(the Antikythera fixture). Asked about an earlier page, the model must search
the log and answer from it -- the page's title isn't on screen, so it can't come
from there -- and an ordinary question must not reach for the log at all.
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.activity import ActivityLog, Visit
from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False
cfg.activity.enabled = True
cfg.activity.folder = str(Path(tempfile.mkdtemp(prefix="companion-activity-e2e-")))
log = ActivityLog(cfg.activity.folder)
now = datetime.now().replace(second=0, microsecond=0)
for minutes_ago, title, activity in (
    (180, "Sourdough starter guide - King Arthur Baking", "reading a guide to sourdough starters"),
    (120, "Mars rover Perseverance finds organic molecules - NASA", "reading about the Perseverance rover"),
    (60, "Best budget mechanical keyboards 2026 - Tom's Hardware", "reading keyboard reviews"),
):
    started = now - timedelta(minutes=minutes_ago)
    log.record(Visit(started, started + timedelta(minutes=14), "brave.exe", title, activity))

comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
comp.refresh()
ran = []
original_run = comp.tools.run
comp.tools.run = lambda call: ran.append(call.name) or original_run(call)

# "a while ago", not "earlier today": the visits are logged 1-3 hours back, so
# a run just after midnight put them on yesterday and the model rightly said
# there was nothing from today.
answer = comp.ask("What was that article about the Mars rover I read a while ago?",
                  context=comp.last_context).text()
print(f"    said: {answer[:200]!r}")
check("the model searched the activity log", "search_activity" in ran, str(ran))
check("...and answered from it: the page isn't on screen",
      any(w in answer for w in ("Perseverance", "NASA", "organic")), answer[:100])

ran.clear()
answer = comp.ask("What is this article about?", context=comp.last_context).text()
check("an ordinary question about the screen doesn't touch the log", "search_activity" not in ran, str(ran))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
