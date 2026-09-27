"""Daily reflection: a few lasting facts about the user, in a file they can edit.

Once a day, while the user is away and nothing else is using the
model, the recent activity log and what they said are looked over, and
`data/about_you.md` is updated: one fact per line, with where it came from.
Good companion memory turns many small episodes into a few lasting facts --
but a small model invents facts, so everything it proposes must pass checks in
code before a line is written:

- it rests on the user's own words, or on several different pages
  (`min_visits`) -- never on one page;
- its words are found in that evidence, so a page about the Mars rover can't
  become "loves science";
- it touches no sensitive category: health, religion, politics, sexuality,
  money, other people;
- the user never deleted it: a line removed from the file is remembered, and
  not written again.

Lines the user writes or edits are theirs and left alone. Nothing leaves this
machine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from core.activity import ActivityLog, Visit
from core.logging import get_logger
from core.orchestrator import _content_words
from core.types import Message

log = get_logger(__name__)

#: Categories no fact may touch, in the fact or in most of what it rests on.
#: Deliberately broad: a missed fact costs nothing, a wrong one about someone's
#: health or family costs trust.
SENSITIVE = re.compile(
    r"\b(?:health\w*|illness\w*|sick\w*|diseases?|diagnos\w*|symptoms?|doctors?|hospitals?|"
    r"medic\w*|therap\w*|depress\w*|anxiety|cancer\w*|diabet\w*|pregnan\w*|diet\w*|"
    r"mental|alcohol\w*|drugs?|addict\w*|religio\w*|church\w*|mosques?|pray\w*|god|"
    r"allah|islam\w*|muslims?|christian\w*|jewish|bible|quran|polit\w*|elections?|"
    r"vot(?:e|es|ed|ing)|government\w*|democrat\w*|republican\w*|sex\w*|gay|lesbian|"
    r"bisexual|dating|porn\w*|girlfriend|boyfriend|relationship\w*|debts?|loans?|salary|"
    r"income|banks?|banking|invest\w*|crypto\w*|stocks?|mortgage\w*|bankrupt\w*|money|"
    r"wife|husband|partner|mother|father|mom|mum|dad|sons?|daughters?|brothers?|sisters?|"
    r"friends?|colleagues?|boss|kids?|children|"
    r"sağlık\w*|hastal\w*|doktor\w*|hastane\w*|ilaç\w*|terapi\w*|depresyon\w*|"
    r"din(?:i|im|ler)?|cami\w*|namaz\w*|kilise\w*|siyaset\w*|seçim\w*|parti(?:si|ler)?|"
    r"cinsel\w*|sevgili\w*|borç\w*|kredi\w*|maaş\w*|banka\w*|yatırım\w*|"
    r"annem|babam|eşim|kardeş\w*|arkadaş\w*)\b",
    re.IGNORECASE,
)

#: Words a fact may use without their being in the evidence: how it is said,
#: not what it claims.
GENERIC = frozenset("""
reads read reading watches watch watching watched likes like enjoys enjoy interested
interest often regularly lots about user they them their learning learns learn spends
spend time videos video articles article pages page keeps coming back follows
following says said told tends frequently loves love
""".split())

REFLECT = (
    "You look back over someone's recent computer use to note a few LASTING facts "
    "about them: what they keep coming back to, and what they have said about "
    "themselves. A fact is about the person, not about one page. Something they "
    "do must rest on several of the numbered items; something they said must rest "
    "on their own words. Never anything about health, religion, politics, "
    "sexuality, money, or other people. Each fact under 12 words, starting with a "
    "verb, like 'Reads a lot about space exploration' or 'Is learning Turkish'. "
    "Give the numbers of the items each fact rests on. If nothing is lasting, give "
    "no facts."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "kind": {"type": "string", "enum": ["habit", "said"]},
                    "evidence": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["fact", "kind", "evidence"],
            },
        }
    },
    "required": ["facts"],
}

HEADER = [
    "# About you",
    "",
    "<!-- Written by the companion's daily reflection, on this machine only. Edit or",
    "delete any line: a fact you delete is never written again, and lines you write",
    "yourself are left as they are. -->",
    "",
]

#: A fact and the share of its words found elsewhere, above which it is the same fact.
SAME_FACT = 0.6
#: The share of a fact's words that must be found in its evidence.
GROUNDED = 0.5


@dataclass
class Fact:
    text: str
    kind: str  # "habit" or "said"
    first: datetime
    last: datetime
    count: int = 1

    def line(self) -> str:
        if self.first.date() == self.last.date():
            span = f"{self.first:%d %b}"
        elif (self.first.year, self.first.month) == (self.last.year, self.last.month):
            span = f"{self.first:%d}-{self.last:%d %b}"
        else:
            span = f"{self.first:%d %b}-{self.last:%d %b}"
        if self.kind == "habit":
            return f"- {self.text} ({self.count} pages, {span})"
        return f"- {self.text} (you said so, {span})"

    def to_state(self) -> dict:
        return {"kind": self.kind, "first": self.first.isoformat(), "last": self.last.isoformat(),
                "count": self.count}

    @classmethod
    def from_state(cls, text: str, data: dict) -> "Fact":
        return cls(text, data.get("kind", "habit"), datetime.fromisoformat(data["first"]),
                   datetime.fromisoformat(data["last"]), int(data.get("count", 1)))


@dataclass
class Report:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    skipped: str = ""


# -- the file ------------------------------------------------------------------

_SOURCE = re.compile(r"\s*\([^()]*\)\s*$")


def _bare(line: str) -> str:
    """A fact line's text, without its bullet and its "(source)"."""
    return _SOURCE.sub("", line.strip()[1:].strip()).strip()


def _is_fact_line(line: str) -> bool:
    return line.lstrip().startswith("- ")


def about_facts(path: Path | str) -> list[str]:
    """The facts in the file, as the user has left them."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    return [_bare(line) for line in lines if _is_fact_line(line) and _bare(line)]


#: A question about the user themselves -- where what is known about them fits.
_ABOUT_THEM = re.compile(
    r"\b(?:should i|could i|can i|for me|about me|recommend\w*|suggest\w*|what do i|"
    r"do you know (?:me|anything about me)|my (?:interests?|hobbies|taste)|"
    r"bana|benim için|öner\w*|tavsiye\w*|ne yapsam|hakkımda)\b",
    re.IGNORECASE,
)


def relevant_facts(facts: list[str], text: str, personal: bool = False) -> list[str]:
    """The facts that bear on `text`: sharing its words, or all of them when the
    question is about the user (`personal`).

    Decided in code because telling the model "use only when it fits" wasn't
    enough: with both facts in every answer, "What is this article about?" came
    back once in eight with "Since you are learning Turkish, did you want me to
    explain any terms?"
    """
    if personal or _ABOUT_THEM.search(text or ""):
        return list(facts)
    words = _words(text or "")
    return [fact for fact in facts if _share(_words(fact), words) > 0]


def _state_path(path: Path) -> Path:
    return path.with_name(path.stem + ".state.json")


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(_state_path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _state_path(path).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# -- evidence and checks -------------------------------------------------------


def gather(log: ActivityLog, said: list[tuple[datetime, str]], now: datetime,
           days: int = 7, max_visits: int = 100) -> list[tuple[str, object]]:
    """The numbered things a fact may rest on: recent visits, then their words."""
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    visits = sorted(log.visits(start), key=lambda v: v.started)[-max_visits:]
    items: list[tuple[str, object]] = [("visit", v) for v in visits]
    items += [("said", (when, text.strip())) for when, text in said
              if when >= start and text.strip()][-30:]
    return items


def _item_text(item: tuple[str, object]) -> str:
    kind, value = item
    if kind == "visit":
        return f"{value.title} {value.activity}"
    return value[1]


def _item_line(number: int, item: tuple[str, object]) -> str:
    kind, value = item
    if kind == "visit":
        visit: Visit = value
        line = f"[{number}] {visit.started:%a %d %b}, {visit.minutes} min: {visit.title}"
        return line + (f" -- {visit.activity}" if visit.activity else "")
    when, text = value
    return f'[{number}] {when:%a %d %b}, they said: "{text[:200]}"'


def _words(text: str) -> set[str]:
    return {w for w in _content_words(text) if w not in GENERIC}


def _related(a: str, b: str) -> bool:
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


def _share(words: set[str], others: set[str]) -> float:
    if not words:
        return 0.0
    return sum(any(_related(w, o) for o in others) for w in words) / len(words)


def _same(a: str, b: str) -> bool:
    return _share(_words(a), _words(b)) >= SAME_FACT or _share(_words(b), _words(a)) >= SAME_FACT


def judge(proposal: dict, items: list, min_visits: int = 3) -> tuple[Fact | None, str]:
    """A proposed fact, if the evidence holds it up; otherwise why not."""
    text = " ".join(str(proposal.get("fact") or "").split()).strip().rstrip(".")
    if not 2 <= len(text.split()) <= 14:
        return None, "not a short fact"
    cited = []
    for number in proposal.get("evidence") or []:
        if isinstance(number, int) and 1 <= number <= len(items):
            cited.append(items[number - 1])
    if not cited:
        return None, "no evidence"
    if SENSITIVE.search(text):
        return None, "a sensitive subject"
    if sum(bool(SENSITIVE.search(_item_text(i))) for i in cited) * 2 >= len(cited):
        return None, "rests on sensitive pages or words"

    # The kind of fact is decided by what it rests on, not by the model's label:
    # in a batch of five, qwen3.5:4b labelled "Reads a lot about space
    # exploration" -- resting on four pages -- as something said, every time.
    words = [i for i in cited if i[0] == "said"]
    visits = [i[1] for i in cited if i[0] == "visit"]
    why = "no evidence"
    if words:
        if _share(_words(text), set().union(*(_words(_item_text(i)) for i in words))) >= GROUNDED:
            dates = [i[1][0] for i in words]
            return Fact(text, "said", min(dates), max(dates)), ""
        why = "not in their words"
    if visits:
        pages = {v.title.casefold() for v in visits}
        if len(pages) < min_visits:
            return None, f"only {len(pages)} page(s)"
        evidence = set().union(*(_words(_item_text(("visit", v))) for v in visits))
        if _share(_words(text), evidence) < GROUNDED:
            return None, "not what those pages were about"
        return Fact(text, "habit", min(v.started for v in visits),
                    max(v.started for v in visits), len(pages)), ""
    return None, why


# -- reflecting ------------------------------------------------------------------


def _propose(llm, items: list) -> list[dict]:
    prompt = "What they did and said recently:\n" + "\n".join(
        _item_line(n, item) for n, item in enumerate(items, start=1)
    )
    raw = "".join(llm.chat(
        [Message(role="system", content=REFLECT), Message(role="user", content=prompt)],
        stream=False, temperature=0.2, json_schema=SCHEMA,
    )).strip()
    try:
        data = json.loads(raw)
    except ValueError:
        log.info("reflection reply was not JSON: %r", raw[:80])
        return []
    facts = data.get("facts") if isinstance(data, dict) else None
    return [f for f in facts or [] if isinstance(f, dict)]


def reflect(llm, path: Path | str, log_: ActivityLog, said: list[tuple[datetime, str]],
            now: datetime | None = None, min_visits: int = 3, days: int = 7,
            max_facts: int = 20) -> Report:
    """Look back and update the facts file. At most once a day."""
    now = now or datetime.now()
    path = Path(path)
    report = Report()
    state = _load_state(path)
    today = f"{now:%Y-%m-%d}"
    if state.get("last_run") == today:
        report.skipped = "already reflected today"
        return report

    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    present = [_bare(line) for line in lines if _is_fact_line(line)]
    written = {text: Fact.from_state(text, data) for text, data in state.get("written", {}).items()}
    removed = list(state.get("removed", []))
    for text in list(written):
        if text not in present:
            # Deleted or reworded by the user: never write it again.
            removed.append(text)
            del written[text]
    user_lines = [text for text in present if text not in written]

    state["last_run"] = today
    items = gather(log_, said, now, days)
    if not items:
        report.skipped = "nothing to look back on"
        state.update(written={t: f.to_state() for t, f in written.items()}, removed=removed)
        _save_state(path, state)
        return report

    new: list[Fact] = []
    updates: dict[str, Fact] = {}
    for proposal in _propose(llm, items):
        fact, why = judge(proposal, items, min_visits)
        label = str(proposal.get("fact") or "")[:80]
        if fact is None:
            report.rejected.append((label, why))
            continue
        if any(_same(fact.text, gone) for gone in removed):
            report.rejected.append((label, "you deleted it before"))
            continue
        if any(_same(fact.text, mine) for mine in user_lines):
            report.rejected.append((label, "already in the file, in your words"))
            continue
        match = next((t for t in written if _same(fact.text, t)), None)
        if match is not None:
            old = written[match]
            merged = Fact(match, old.kind, min(old.first, fact.first), max(old.last, fact.last),
                          max(old.count, fact.count))
            written[match] = updates[match] = merged
            report.updated.append(match)
            continue
        if any(_same(fact.text, f.text) for f in new):
            continue
        if len(present) + len(new) >= max_facts:
            report.rejected.append((label, "the file is full"))
            continue
        new.append(fact)
        report.added.append(fact.text)

    if new or updates:
        out = list(HEADER) if not lines else []
        for line in lines:
            bare = _bare(line) if _is_fact_line(line) else None
            out.append(updates[bare].line() if bare in updates else line)
        out += [fact.line() for fact in new]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    for fact in new:
        written[fact.text] = fact
    state.update(written={t: f.to_state() for t, f in written.items()}, removed=removed)
    _save_state(path, state)
    log.info("reflection: %d added, %d updated, %d rejected", len(report.added),
             len(report.updated), len(report.rejected))
    return report
