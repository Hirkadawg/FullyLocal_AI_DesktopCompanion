"""Exporting liked replies for fine-tuning review.

Only thumbs-up answers and remarks become examples; each is a chat-format
JSONL line with its system prompt (the companion's for answers, the persona for
remarks), what it was asked or where it was, and the reply; a Markdown file
shows them to read; filters by date and kind work; nothing is written when
nothing is liked; and the screen text a rating doesn't keep is said, not faked.
"""

import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.config import AppConfig
from core.export import SCREEN_NOTE, export_liked
from core.logging import setup_logging
from core.ratings import Rated, RatingStore

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


folder = Path(tempfile.mkdtemp(prefix="companion-export-"))
cfg = AppConfig.load(CONFIG_PATH)
cfg.ratings.file = str(folder / "ratings.jsonl")
store = RatingStore(cfg.root / cfg.ratings.file)

short = Rated(kind="answer", reply="Done. F3 ratings are built; next is the mic toggle.",
              message="please answer much more shortly", page="Notes", at="2026-09-14T15:43:02")
long_one = Rated(kind="answer", reply="You're in a notes app with the sidebar open... " * 5,
                 message="what can you see right now", page="Notes", at="2026-09-14T15:41:11")
turkish = Rated(kind="answer", reply="Dişliler bronzdan yapılmış.", message="Dişliler neden yapılmış?",
                page="Antikythera mechanism - Wikipedia", at="2026-08-02T10:00:00")
remark = Rated(kind="remark", reply="Would anyone have trusted a machine that old to predict eclipses?",
               page="Antikythera mechanism - Wikipedia", move="question",
               why="the eclipse prediction is the surprising part", trigger="page_end",
               at="2026-09-14T16:00:00")
empty = Rated(kind="remark", reply="  ", page="x", move="opinion", at="2026-09-14T16:10:00")
store.rate(short, "up")
store.rate(long_one, "down")
store.rate(turkish, "up")
store.rate(remark, "up")
store.rate(empty, "up")

print("what is exported")

out = folder / "out"
result = export_liked(cfg, out_dir=out, now=datetime(2026, 9, 15, 9, 30))
check("thumbs-up answers and remarks become examples; thumbs down doesn't",
      (result.answers, result.remarks) == (2, 1), f"{result.answers} answers, {result.remarks} remarks")
check("an empty reply is skipped", result.skipped == 1, str(result.skipped))
check("into a JSONL file and a review file, named by when",
      result.jsonl == out / "liked-2026-09-15-0930.jsonl" and result.review.name == "liked-2026-09-15-0930.md"
      and result.jsonl.is_file() and result.review.is_file())

examples = [json.loads(line) for line in result.jsonl.read_text(encoding="utf-8").splitlines()]
check("one example per line, each a system, user and assistant turn",
      len(examples) == 3 and all([m["role"] for m in e["messages"]] == ["system", "user", "assistant"]
                                 for e in examples))
by_reply = {e["messages"][2]["content"]: e for e in examples}
answer = by_reply[short.reply]
check("an answer's system turn is the companion's own prompt",
      answer["messages"][0]["content"] == cfg.system_prompt)
check("...its user turn holds the question and the window, and says the screen isn't there",
      "please answer much more shortly" in answer["messages"][1]["content"]
      and "Window title: Notes" in answer["messages"][1]["content"]
      and SCREEN_NOTE in answer["messages"][1]["content"])
liked_remark = by_reply[remark.reply]
check("a remark's system turn is the persona", "companion sitting beside" in liked_remark["messages"][0]["content"])
check("...and its job is the kind of remark it was",
      "ask a genuine question" in liked_remark["messages"][1]["content"])
check("each keeps its id, time, moment and reason, apart from the messages",
      liked_remark["meta"]["trigger"] == "page_end" and liked_remark["meta"]["why"] == remark.why
      and liked_remark["meta"]["id"] == remark.id)
check("Turkish is kept as written", "Dişliler bronzdan yapılmış." in result.jsonl.read_text(encoding="utf-8"))
check("the thumbs-down reply is nowhere", long_one.reply[:30] not in result.jsonl.read_text(encoding="utf-8"))

review = result.review.read_text(encoding="utf-8")
check("the review file shows each example to read",
      "**They asked:** please answer much more shortly" in review
      and f"**It said:** {remark.reply}" in review and "**Why:**" in review, review[:300])
check("...and says how to leave one out, and that the screen isn't kept",
      "delete an example's line" in review and SCREEN_NOTE in review)

print("\nfilters, and nothing to export")

recent = export_liked(cfg, out_dir=folder / "recent", since=date(2026, 9, 1), now=datetime(2026, 9, 15, 9, 31))
check("--since leaves out older ratings", (recent.answers, recent.remarks) == (1, 1),
      f"{recent.answers} answers, {recent.remarks} remarks")
remarks_only = export_liked(cfg, out_dir=folder / "remarks", kind="remark", now=datetime(2026, 9, 15, 9, 32))
check("--kind remark exports remarks only", (remarks_only.answers, remarks_only.remarks) == (0, 1))
cfg.ratings.file = str(folder / "no-ratings.jsonl")
nothing = export_liked(cfg, out_dir=folder / "nothing")
check("with nothing liked, nothing is written",
      nothing.examples == 0 and nothing.jsonl is None and not (folder / "nothing").exists())

print("\nthe command line")

import main  # noqa: E402

cfg = AppConfig.load(CONFIG_PATH)
RatingStore(cfg.root / cfg.ratings.file).rate(short, "up")
cli_out = folder / "cli"
code = main._export_liked(cfg, str(cli_out), None, None)
check("main.py --export-liked writes the files and succeeds",
      code == 0 and len(list(cli_out.glob("liked-*.jsonl"))) == 1, f"exit {code}")
check("tests read ratings from the test folder, not companion/data",
      helpers.TEST_DATA_DIR.resolve() in (cfg.root / cfg.ratings.file).resolve().parents)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
