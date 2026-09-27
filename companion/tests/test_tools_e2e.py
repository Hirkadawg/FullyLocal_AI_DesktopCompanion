"""Acceptance: tools work when asked, change nothing when they aren't,
and ordinary questions still stream normally with tools available.

Notes are saved by the app when a message asks for one, not through a model
tool: given one, qwen3.5:4b wrote notes nobody asked for (test_tool_requests.py
has the details and the fast checks).
"""

import sys
import tempfile
import time
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)

# This run's own timers and notes. Absolute paths, so `config.root / path`
# yields them unchanged -- root itself must not be touched, since the system
# prompt is found relative to it.
tmp = Path(tempfile.mkdtemp(prefix="companion-e2e-"))
cfg.tools.timers_file = str(tmp / "timers.json")
cfg.tools.notes_file = str(tmp / "notes.md")

IMG = FIXTURE_IMAGE
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


comp = build_companion(cfg, image_path=IMG)
comp.llm.health_check()
comp.refresh()
# Checked by presence, not count: adding a tool should not fail this suite.
expected = {"get_time", "stopwatch", "set_timer", "list_timers", "cancel_timer",
            "read_notes", "search_notes"}
missing = expected - set(comp.tools.names if comp.tools else [])
check("the core tools are registered", comp.tools is not None and not missing,
      f"missing {missing}" if missing else f"{len(comp.tools)} tools")
check("there is no note-taking tool for the model to misuse",
      "take_note" not in comp.tools.names)

store = comp.tools.timers
notes = tmp / "notes.md"


def ask(question):
    comp.memory.clear()
    return "".join(comp.ask(question, context=comp.last_context).chunks).strip()


def notebook_text():
    return notes.read_text(encoding="utf-8") if notes.exists() else ""


print("\nsetting a timer by asking for one")
answer = ask("Set a timer for 20 minutes for the pasta.")
print(f"    said: {answer[:90]}")
active = store.active()
check("a timer was actually created", len(active) == 1, f"{[t.label for t in active]}")
if active:
    minutes = active[0].remaining / 60
    check("timer has roughly the right length", 19 <= minutes <= 20.5,
          f"{minutes:.1f} min")
    check("the label reflects the request", "pasta" in active[0].label.lower(),
          f"label={active[0].label!r}")
check("it confirms in words", len(answer) > 0)

print("\nasking what is running")
answer = ask("What timers do I have running right now?")
print(f"    said: {answer[:90]}")
check("it reports the pasta timer", "pasta" in answer.lower(), answer[:80])

print("\ncancelling it")
answer = ask("Cancel the pasta timer.")
print(f"    said: {answer[:90]}")
check("the timer is really gone", store.active() == [],
      f"{[t.label for t in store.active()]}")

print("\ntaking a note by asking for one")
answer = ask("Take a note that the Antikythera mechanism has thirty bronze gears.")
print(f"    said: {answer[:90]}")
check("a note was really written", "gear" in notebook_text().lower(),
      repr(notebook_text()[:120]))
check("it confirms in words", len(answer) > 0)

print("\nreading notes back")
answer = ask("What notes have I taken?")
print(f"    said: {answer[:90]}")
check("the note comes back", "gear" in answer.lower(), answer[:90])

print("\nasking the time")
answer = ask("What time is it?")
print(f"    said: {answer[:90]}")
check("it gives a real time rather than inventing one",
      str(time.localtime().tm_year) in answer or ":" in answer, answer[:60])

print("\nordinary messages change nothing")
# A conversation, not isolated questions: the unasked writes that caused this
# were measured mid-conversation, after "Interesting." and "Yes.".
store.timers.clear()
before = notebook_text()
comp.memory.clear()
for message in ("What is this article about?", "Interesting.", "Yes.",
                "Thanks, that helps.", "Summarize the key points in three bullets.",
                "Cool, I'll keep reading."):
    "".join(comp.ask(message, context=comp.last_context).chunks)
check("no timer was started", store.active() == [], f"{[t.label for t in store.active()]}")
check("no note was written", notebook_text() == before,
      repr(notebook_text()[len(before):][:120]))

print("\nan ordinary question must still stream")
comp.memory.clear()
chunks = []
started = time.perf_counter()
first_chunk_at = None
for piece in comp.ask("In one sentence, what is this article about?",
                      context=comp.last_context).chunks:
    if first_chunk_at is None:
        first_chunk_at = time.perf_counter() - started
    chunks.append(piece)
answer = "".join(chunks).strip()
print(f"    said: {answer[:90]}")
check("ordinary question answered", len(answer) > 20)
check("it still streams in many chunks", len(chunks) > 3, f"{len(chunks)} chunks")
check("first chunk arrives promptly", first_chunk_at < 3.0, f"{first_chunk_at:.2f}s")

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
