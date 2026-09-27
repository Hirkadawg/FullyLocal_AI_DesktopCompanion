"""Tool registry, timers and notes. No model needed."""

import sys
import tempfile
import time
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.logging import setup_logging
from core.types import ToolCall
from modules.tools.base import Tool, ToolRegistry
from modules.tools.clock import GetTime, Stopwatch
from modules.tools.notes import NoteBook, ReadNotes, SearchNotes
from modules.tools.timers import CancelTimer, ListTimers, SetTimer, TimerStore

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


tmp = Path(tempfile.mkdtemp(prefix="companion-tools-"))

print("registry")

registry = ToolRegistry([GetTime()])
check("tool is registered", registry.names == ["get_time"])
check("spec has a schema", registry.specs()[0].parameters["type"] == "object")
check("spec converts to Ollama form",
      registry.specs()[0].to_ollama()["function"]["name"] == "get_time")

out = registry.run(ToolCall("get_time", {}))
check("get_time returns a real timestamp", str(time.localtime().tm_year) in out, out)

out = registry.run(ToolCall("no_such_tool", {}))
check("unknown tool returns a message, not an exception",
      "no tool called" in out.lower(), out)


class Exploding(Tool):
    name = "explode"
    description = "always fails"

    def run(self):
        raise RuntimeError("boom")


registry.add(Exploding())
out = registry.run(ToolCall("explode", {}))
check("a failing tool is reported, not raised", "boom" in out, out)

out = registry.run(ToolCall("get_time", {"unexpected": 1}))
check("bad arguments are reported, not raised", "could not run" in out.lower(), out)

try:
    registry.add(GetTime())
    check("duplicate names rejected", False)
except ValueError:
    check("duplicate names rejected", True)

print("\ntimers")

store = TimerStore(tmp / "timers.json")
setter, lister, canceller = SetTimer(store), ListTimers(store), CancelTimer(store)

check("no timers initially", "no timers" in lister.run().lower())
out = setter.run(minutes=20, label="pasta")
check("timer set", "20 minutes" in out and "pasta" in out, out)
check("timer is listed", "pasta" in lister.run())
check("nothing is due yet", store.due() == [])

check("zero-length rejected", "positive" in setter.run(minutes=0).lower())
check("negative rejected", "positive" in setter.run(minutes=-5).lower())
check("absurdly long rejected", "shorter" in setter.run(minutes=5000).lower())
check("non-numeric rejected", "not a number" in setter.run(minutes="soon").lower())

out = canceller.run(label="nope")
check("cancelling a missing timer explains itself", "no timer called" in out.lower(), out)
out = canceller.run(label="pasta")
check("cancelling works", "cancelled" in out.lower(), out)
check("cancelled timer is gone", "no timers" in lister.run().lower())

# Expiry, without waiting.
short = store.add("tea", 0.05)
time.sleep(0.1)
due = store.due()
check("expired timer is reported due", len(due) == 1 and due[0].label == "tea")
check("a due timer is removed", store.due() == [])

print("\ntimers survive a restart")

store2 = TimerStore(tmp / "timers.json")
SetTimer(store2).run(minutes=60, label="long bake")
reopened = TimerStore(tmp / "timers.json")
check("timer persisted across restart",
      any(t.label == "long bake" for t in reopened.active()),
      f"{[t.label for t in reopened.active()]}")

# A timer that expired while closed shouldn't fire hours late.
stale = TimerStore(tmp / "stale.json")
stale.add("old", 0.01)
time.sleep(0.05)
check("stale timer is dropped on load, not fired late",
      TimerStore(tmp / "stale.json").active() == [])

corrupt = tmp / "corrupt.json"
corrupt.write_text("{not json at all", encoding="utf-8")
check("a corrupt timer file does not crash startup",
      TimerStore(corrupt).active() == [])

print("\nnotes")

notebook = NoteBook(tmp / "notes.md")
reader, searcher = ReadNotes(notebook), SearchNotes(notebook)

check("no notes initially", "exactly 0 notes" in reader.run().lower(), reader.run())

# Written the way the app writes them; the model has no note-taking tool.
notebook.append("The Antikythera mechanism has 30 bronze gears")
notebook.append("Check the orrery etymology", context="Wikipedia")
check("notes are written", len(notebook.entries()) == 2)
check("context is recorded", "Wikipedia" in notebook.entries()[-1])
check("notes read back", "bronze gears" in reader.run())
check("search finds a note", "orrery" in searcher.run(query="ORRERY").lower())
check("search reports a miss", "no notes mention" in searcher.run(query="zzz").lower())
check("empty search handled", "no search text" in searcher.run(query="").lower())
check("notes file is plain markdown readable by anything",
      (tmp / "notes.md").read_text(encoding="utf-8").startswith("# Notes"))

print("\nstopwatch")

watch = Stopwatch()
check("checking before starting says so", "not running" in watch.run(action="check").lower())
watch.run(action="start")
time.sleep(0.05)
check("check reports elapsed", "s" in watch.run(action="check"))
check("stop reports final time", "stopped at" in watch.run(action="stop").lower())
check("stopped watch is not running", "not running" in watch.run(action="check").lower())

print("\nsuites never write to the user's notebook or timers")

# Regression: suites that built the real app wrote test notes ("Remember the
# word ZEPHYRINE") into companion/data/notes.md -- 19 of them -- whenever a
# model chose to take a note, until a later test read one back. helpers.py now
# points every config a suite loads at a temporary folder; this holds it there.
import helpers
from core.config import AppConfig

loaded = AppConfig.load(CONFIG_PATH)
real_data = (Path(CONFIG_PATH).parent / "data").resolve()
for label, path in (("notes", loaded.tools.notes_file), ("timers", loaded.tools.timers_file),
                    ("ratings", loaded.ratings.file),
                    ("activity log", str(Path(loaded.activity.folder) / "activity-2026-09.csv")),
                    ("about-you facts", loaded.reflection.file),
                    ("learning", loaded.learning.file),
                    ("avatar position", loaded.avatar.state_file),
                    ("saved prompts", str(Path(loaded.llm.prompt_log_folder) / "prompts-2026-09-16.txt")),
                    ("reset archive", str(Path(loaded.archive_folder) / "ratings-2026-09-14-180500"))):
    resolved = (loaded.root / path).resolve()
    check(f"{label} go to the test folder, not companion/data",
          real_data not in resolved.parents
          and helpers.TEST_DATA_DIR.resolve() in resolved.parents,
          str(resolved))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
