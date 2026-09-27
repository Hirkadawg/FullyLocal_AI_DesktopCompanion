"""Exporting liked replies, for someone to review before any fine-tuning.

Neuro-sama's one confirmed way of learning is offline fine-tuning on hand-picked
transcripts, never learning live. Ratings (F3) are the hand-picking: this turns
every thumbs-up answer and remark into a training example in the chat format
fine-tuning tools read -- one JSON object per line, {"messages": [system, user,
assistant]} -- plus a Markdown file to read them in. Nothing is trained, nothing
is sent anywhere; the files stay in data/exports.

What an example can't hold: the screen. Ratings keep the window title and the
words, not the page text, so a question's example says which page it was about
and no more. Whoever reviews the file should know that before training on it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from core.logging import get_logger
from core.ratings import RatingStore

log = get_logger(__name__)

SCREEN_NOTE = "(The screen's text isn't kept with ratings; only the window title is.)"


@dataclass
class ExportResult:
    answers: int = 0
    remarks: int = 0
    skipped: int = 0
    jsonl: Path | None = None
    review: Path | None = None

    @property
    def examples(self) -> int:
        return self.answers + self.remarks


def _answer_example(record: dict, system: str) -> list[dict]:
    page = record.get("page") or "(unknown window)"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Window title: {page}\n{SCREEN_NOTE}\n\nQuestion: {record.get('message', '')}"},
        {"role": "assistant", "content": record["reply"]},
    ]


def _remark_example(record: dict, persona: str) -> list[dict]:
    from core.orchestrator import MOVES

    page = record.get("page") or "(unknown window)"
    job = MOVES.get(record.get("move", ""), "say one short thing about it")
    return [
        {"role": "system", "content": persona},
        {"role": "user", "content": f"They are on: {page}.\n{SCREEN_NOTE}\n\nSay one thing: {job}."},
        {"role": "assistant", "content": record["reply"]},
    ]


def export_liked(config, out_dir: Path | str | None = None, since: date | None = None,
                 kind: str | None = None, now: datetime | None = None) -> ExportResult:
    """Write every thumbs-up answer and remark as a training example, for review.

    `since`: only ratings of replies from that day on. `kind`: "answer" or
    "remark" only. Writes nothing when there is nothing to export.
    """
    from core.orchestrator import DEFAULT_PERSONA, load_persona

    now = now or datetime.now()
    result = ExportResult()
    liked = []
    for record in RatingStore(config.root / config.ratings.file).ratings():
        if record.get("rating") != "up":
            continue
        if kind and record.get("kind") != kind:
            continue
        if since and str(record.get("time", ""))[:10] < since.isoformat():
            continue
        if not str(record.get("reply") or "").strip() or record.get("kind") not in ("answer", "remark"):
            result.skipped += 1
            continue
        liked.append(record)
    if not liked:
        return result

    try:
        system = config.system_prompt
    except Exception:
        system = ""
    try:
        persona = load_persona(config.root / config.proactive.persona_file)
    except OSError:
        persona = DEFAULT_PERSONA

    out = Path(out_dir) if out_dir else config.root / "data" / "exports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = f"{now:%Y-%m-%d-%H%M}"
    result.jsonl = out / f"liked-{stamp}.jsonl"
    result.review = out / f"liked-{stamp}.md"

    lines, review = [], [
        f"# Liked replies, exported {now:%d %B %Y %H:%M}",
        "",
        f"{len(liked)} thumbs-up replies, as training examples in `{result.jsonl.name}`.",
        "Read them before training on them: delete an example's line from the JSONL file to",
        "leave it out. " + SCREEN_NOTE,
        "",
    ]
    for number, record in enumerate(liked, start=1):
        is_answer = record["kind"] == "answer"
        messages = _answer_example(record, system) if is_answer else _remark_example(record, persona)
        meta = {key: record.get(key, "") for key in ("id", "time", "kind", "page", "move", "trigger", "why")}
        lines.append(json.dumps({"messages": messages, "meta": meta}, ensure_ascii=False))
        if is_answer:
            result.answers += 1
        else:
            result.remarks += 1
        when = str(record.get("time", ""))[:16].replace("T", " ")
        review += [f"## {number}. {record['kind']} -- {when} -- {record.get('page') or '(unknown window)'}", ""]
        if is_answer:
            review.append(f"**They asked:** {record.get('message', '')}")
        else:
            review.append(f"**Moment:** {record.get('trigger') or '?'}, a {record.get('move') or '?'}")
        review.append(f"**It said:** {record['reply']}")
        if record.get("why"):
            review.append(f"**Why:** {record['why']}")
        review.append("")

    result.jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result.review.write_text("\n".join(review), encoding="utf-8")
    log.info("exported %d liked replies to %s", result.examples, result.jsonl)
    return result
