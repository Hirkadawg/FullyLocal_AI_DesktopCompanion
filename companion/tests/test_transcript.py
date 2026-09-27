"""Verify the transcript renders questions and answers as separate, distinctly
formatted paragraphs -- checked against the document model, not by eyeballing."""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from modules.ui.window import ChatWindow

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)

app = QApplication(sys.argv)
win = ChatWindow(cfg)

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


# Simulate two streamed turns, exactly as the worker drives it.
win.add_question("what is this article about?")
for piece in ["The Antikythera ", "mechanism is an ", "ancient orrery."]:
    win.add_chunk(piece)
win.add_question("explain that in simpler terms")
for piece in ["It is a very old ", "geared machine."]:
    win.add_chunk(piece)

doc = win.transcript.document()
blocks = []
block = doc.begin()
while block.isValid():
    blocks.append(block)
    block = block.next()

texts = [b.text() for b in blocks]
print("\n  rendered blocks:")
for i, b in enumerate(blocks):
    fmt = b.begin().fragment().charFormat() if b.begin().fragment().isValid() else None
    colour = fmt.foreground().color().name() if fmt else "?"
    weight = fmt.fontWeight() if fmt else "?"
    top = b.blockFormat().topMargin()
    print(f"   [{i}] top={top:>4.0f} colour={colour} weight={weight}  {b.text()[:44]!r}")

check("four separate paragraphs", len(blocks) == 4, f"got {len(blocks)}")
check("question 1 is its own block",
      texts[0] == "what is this article about?", f"got {texts[0]!r}")
check("streamed answer joined into one block",
      texts[1] == "The Antikythera mechanism is an ancient orrery.", f"got {texts[1]!r}")
check("question 2 is its own block",
      texts[2] == "explain that in simpler terms", f"got {texts[2]!r}")
check("second answer joined", texts[3] == "It is a very old geared machine.")

q_fmt = blocks[0].begin().fragment().charFormat()
a_fmt = blocks[1].begin().fragment().charFormat()
check("question and answer differ in colour",
      q_fmt.foreground().color() != a_fmt.foreground().color(),
      f"{q_fmt.foreground().color().name()} vs {a_fmt.foreground().color().name()}")
check("question is bolder than the answer",
      q_fmt.fontWeight() > a_fmt.fontWeight(),
      f"{q_fmt.fontWeight()} vs {a_fmt.fontWeight()}")
check("questions have breathing room above",
      blocks[2].blockFormat().topMargin() >= 16,
      f"top margin {blocks[2].blockFormat().topMargin()}")
check("no blank first line",
      blocks[0].blockFormat().topMargin() >= 0 and texts[0] != "")

win.add_notice("Capture blocked.", "#e06c6c")
blocks_after = doc.blockCount()
check("notices land in their own block", blocks_after == 5, f"got {blocks_after}")

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
