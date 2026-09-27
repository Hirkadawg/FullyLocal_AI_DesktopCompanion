"""Notes.

Plain Markdown, appended with a timestamp, in a file you can open in any editor.
Not a database: the point of a note taken by voice while reading something is
that you can find it later without the companion's help, including after this
project is abandoned.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from core.logging import get_logger
from modules.tools.base import Tool

log = get_logger(__name__)


class NoteBook:
    """Append-only Markdown notes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, text: str, context: str | None = None) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"- **{stamp}** — {text.strip()}"
        if context:
            line += f"  _(while: {context.strip()})_"

        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = "" if self.path.exists() else "# Notes\n\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(header + line + "\n")
        return stamp

    def entries(self) -> list[str]:
        if not self.path.is_file():
            return []
        return [
            line.strip()
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("- ")
        ]


# There is deliberately no tool for TAKING a note. Given one, qwen3.5:4b wrote
# notes nobody asked for after "Yes." and "Interesting.". Notes are saved by the
# app when the user's message asks for one (modules/tools/requests.py); the
# model only reads and searches them.


class ReadNotes(Tool):
    name = "read_notes"
    description = (
        "Read back recent notes. Use when the user asks what they noted, or "
        "what they were working on."
    )
    parameters = {
        "type": "object",
        "properties": {
            "count": {
                "type": "number",
                "description": "How many recent notes to return (default 10)",
            }
        },
    }
    writes = False

    def __init__(self, notebook: NoteBook, default_count: int = 10) -> None:
        self.notebook = notebook
        self.default_count = default_count

    def run(self, count: float | None = None) -> str:
        entries = self.notebook.entries()
        if not entries:
            return "The notebook is empty. There are exactly 0 notes."
        try:
            wanted = int(count) if count else self.default_count
        except (TypeError, ValueError):
            wanted = self.default_count
        recent = entries[-max(1, wanted):]
        # The count is stated explicitly, and the list is fenced. Without this
        # the model has been observed reading a short list, then continuing it
        # with unrelated text from the screen as though those were notes too.
        header = (
            f"The notebook contains exactly {len(recent)} "
            f"note{'s' if len(recent) != 1 else ''}, listed in full below. "
            f"This is the complete list; there are no others."
        )
        return header + "\n" + "\n".join(recent)


class SearchNotes(Tool):
    name = "search_notes"
    description = "Find notes containing some text."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for"}
        },
        "required": ["query"],
    }
    writes = False

    def __init__(self, notebook: NoteBook) -> None:
        self.notebook = notebook

    def run(self, query: str) -> str:
        query = (query or "").strip()
        if not query:
            return "No search text given."
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        hits = [entry for entry in self.notebook.entries() if pattern.search(entry)]
        if not hits:
            return f"No notes mention '{query}'. Exactly 0 matches."
        shown = hits[-20:]
        return (
            f"Exactly {len(shown)} note{'s' if len(shown) != 1 else ''} "
            f"mention '{query}', listed in full below.\n" + "\n".join(shown)
        )
