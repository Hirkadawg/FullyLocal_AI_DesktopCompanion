"""Regression: a tool's list must not be padded with text from the screen.

Reported in use -- with one note saved and an article on screen, "what notes do
I have" returned the real note followed by three lines lifted off the screen,
presented as though they were also notes.
"""

import sys
import tempfile
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from modules.tools.notes import NoteBook, ReadNotes, SearchNotes

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)

tmp = Path(tempfile.mkdtemp(prefix="companion-grounding-"))
cfg.tools.notes_file = str(tmp / "notes.md")
cfg.tools.timers_file = str(tmp / "timers.json")

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("the tool states its count explicitly")

book = NoteBook(tmp / "notes.md")
reader = ReadNotes(book)
check("empty notebook says exactly 0", "exactly 0" in reader.run().lower())

book.append("The pasta must include a garlic", "pasta recipe")
out = reader.run()
check("one note is described as exactly one", "exactly 1 note" in out.lower(), out[:80])
check("the note itself is present", "garlic" in out)

book.append("Second thing worth remembering")
out = reader.run()
check("two notes described as exactly two", "exactly 2 notes" in out.lower(), out[:80])

hits = SearchNotes(book).run(query="garlic")
check("search states its count", "exactly 1 note" in hits.lower(), hits[:80])

print("\nend to end: one note, an article on screen")

# The fixture is a Wikipedia-style page full of sentences that could pass for
# note entries -- exactly the situation that produced the bug.
fresh = Path(tempfile.mkdtemp(prefix="companion-grounding2-"))
cfg.tools.notes_file = str(fresh / "notes.md")
NoteBook(fresh / "notes.md").append("The pasta must include a garlic", "pasta recipe")

comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
comp.refresh()

answer = "".join(
    comp.ask("Remind me what my current notes are.", context=comp.last_context).chunks
).strip()
print(f"    said: {answer[:160]}")

lowered = answer.lower()
check("the real note is reported", "garlic" in lowered, answer[:80])

# Words that appear only on the screen, never in the notebook.
leaked = [w for w in ("antikythera", "shipwreck", "orrery", "valerios", "1901")
          if w in lowered]
check("no screen content is presented as a note", not leaked, f"leaked: {leaked}")

check("the answer is short, not a padded list", len(answer) < 400,
      f"{len(answer)} chars")

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
