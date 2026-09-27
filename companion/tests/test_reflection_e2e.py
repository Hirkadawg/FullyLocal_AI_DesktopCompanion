"""Daily reflection with the real model, judged over a batch.

A week of activity with one real habit (space, on four different pages), a
single page about baking, several pages about a health condition, noise, and
two things the user said -- one about themselves, one about someone else. Over
five runs: what the model proposes, what code lets through, and whether any
fact written is invented, over-generalised, sensitive or single-page.
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
from core.reflection import SENSITIVE, about_facts, reflect

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()

NOW = datetime.now().replace(hour=21, minute=0, second=0, microsecond=0)
PAGES = [
    (5, "Mars rover Perseverance finds organic molecules - NASA", "reading about the Perseverance rover"),
    (4, "SpaceX Starship launch recap - The Verge", "reading about a rocket launch"),
    (2, "James Webb telescope's deepest image - ESA", "reading about the James Webb telescope"),
    (0, "Europa Clipper mission overview - NASA", "reading about a mission to Europa"),
    (3, "Sourdough starter guide - King Arthur Baking", "reading a bread baking guide"),
    (3, "Early signs of diabetes - Mayo Clinic", "reading about diabetes symptoms"),
    (1, "Type 2 diabetes treatment options - NHS", "reading about diabetes treatment"),
    (0, "Blood sugar levels explained - Healthline", "reading about blood sugar"),
    (5, "Inbox - Outlook", "reading email"), (4, "Inbox - Outlook", "reading email"),
    (2, "Weather forecast - AccuWeather", "checking the weather"),
    (1, "YouTube", "browsing videos"),
]
SAID = [(NOW - timedelta(hours=3), "I've been learning to sail for about a year now"),
        (NOW - timedelta(hours=2), "My brother keeps sending me these memes"),
        (NOW - timedelta(hours=1), "What is this article about?")]
TRUE = ("space", "rocket", "telescope", "mars", "nasa", "astronom", "sail")

written, proposed_count, all_rejected = [], 0, []
for run in range(5):
    folder = Path(tempfile.mkdtemp(prefix="companion-reflection-e2e-"))
    log = ActivityLog(folder / "activity")
    for days_ago, title, activity in PAGES:
        started = NOW - timedelta(days=days_ago, hours=2 + run % 3)
        log.record(Visit(started, started + timedelta(minutes=12), "brave.exe", title, activity))
    report = reflect(comp.llm, folder / "about_you.md", log, SAID, now=NOW)
    facts = about_facts(folder / "about_you.md")
    proposed_count += len(report.added) + len(report.updated) + len(report.rejected)
    all_rejected += report.rejected
    written += facts
    print(f"  run {run + 1}: written {facts}")
    for text, why in report.rejected:
        print(f"           kept out {text!r}: {why}")

print(f"\n  {len(written)} facts written, {len(all_rejected)} kept out, of {proposed_count} proposed")
invented = [f for f in written if not any(t in f.lower() for t in TRUE)]
check("no fact written is invented or over-generalised (all about space or sailing)",
      invented == [], str(invented))
check("nothing sensitive is written", not any(SENSITIVE.search(f) for f in written), str(written))
check("nothing about baking, from a single page", not any("bak" in f.lower() or "bread" in f.lower()
      for f in written), str(written))
space = sum(1 for f in written if any(t in f.lower() for t in TRUE[:-1]))
stated = sum(1 for f in written if "sail" in f.lower())
check("the real habit comes through in at least 3 runs of 5", space >= 3, f"{space}/5")
check("...and what they said about themselves too", stated >= 3, f"{stated}/5")

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
