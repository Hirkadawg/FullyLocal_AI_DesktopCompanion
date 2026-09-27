"""The system prompt, with only the sections a message needs.

`prompts/system.md` used to go whole with every message: ~1,300 tokens of rules on
tools, audio, the screen and remarks, most of them beside the point of any one
message. Instruction following falls as instructions pile up, fastest in small
models (IFScale, 2025), and a standing rule has broken unrelated replies here
before. So the file is cut at its `## ` headings, and code decides
which go:

- the part before the first heading -- who it is, the language, how to answer --
  always;
- "When you spoke first" when the conversation holds something it said unasked;
- "Grounding" when the screen was sent (core/relevance.py);
- "Tools" when a tool is offered for this message, or a note was saved;
- "Audio" when audio was heard, or the message asks about what was said.

A heading not listed here always goes, so a new section is never lost silently.

The paragraph on the screen's text ("You are given text extracted from their
screen...") stays in the part that always goes. Moved into a section sent only
with the screen, "watch my screen" was acknowledged 4-5 times in 10 instead of
9-10: without it the model doesn't take itself to have the screen at all.
"""

from __future__ import annotations

import re

#: Which need each heading serves.
SECTIONS: dict[str, str] = {
    "When you spoke first": "remarks",
    "Grounding": "screen",
    "Tools": "tools",
    "Audio": "audio",
}
NEEDS = frozenset(SECTIONS.values())

#: Asking about something heard: what was said, a song, a voice.
_ABOUT_AUDIO = re.compile(
    r"\b(?:said|say|says|saying|hear\w*|heard|listen\w*|sound\w*|audio|voice\w*|speak\w*|spoke\w*|talk\w*|"
    r"lyrics|songs?|music|podcast|dedi\w*|diyor\w*|söyle\w*|duy\w*|dinle\w*|ses\w*|konuş\w*|şarkı\w*|"
    r"müzik\w*|şarkı sözü\w*)\b",
    re.IGNORECASE,
)


def asks_about_audio(message: str) -> bool:
    return bool(_ABOUT_AUDIO.search(message or ""))


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, text) pairs; the part before the first heading has heading ""."""
    parts: list[tuple[str, str]] = []
    heading, lines = "", []
    for line in text.splitlines():
        if line.startswith("## "):
            parts.append((heading, "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), [line]
        else:
            lines.append(line)
    parts.append((heading, "\n".join(lines).strip()))
    return [(h, t) for h, t in parts if t]


def compose(text: str, needs: set[str]) -> str:
    """The system prompt with only the sections `needs` calls for."""
    kept = [body for heading, body in sections(text)
            if not heading or heading not in SECTIONS or SECTIONS[heading] in needs]
    return "\n\n".join(kept)
