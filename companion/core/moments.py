"""Shared moments: replies they gave a thumbs up, remembered.

Conversation memory lasts one session. A 👍 is the user saying "that one was
good" -- so the companion keeps those exchanges, and brings one back when what
is happening now relates to it: the same topic, or a question about what was
said before. A remark carries why it was made, because the user votes on
remarks for their reason.

Read straight from the ratings file, so there is nothing else to keep in step:
changing a vote to 👎 forgets the moment, and resetting ratings forgets them all.
Chosen in code, not by the model -- the rule the facts in about_you.md follow,
where "use only when it fits" alone let unrelated facts into 1 answer in 8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from core.orchestrator import _BROWSER_SUFFIX, _TITLE_PARTS, _content_words, clean_title

#: Words too common in replies and titles to connect two moments by.
GENERIC = frozenset("""
actually really think thing things something anything everything stuff maybe probably
right sure know knew want wanted looks looking look seems seem made make makes making
said says saying tell told asked asks going gone getting good great nice cool
screen page window currently today yesterday first last next still also even
answer answers question questions reply replies without because while those these
mean means meant like likes work works working dont doesnt didnt isnt youre theyre thats
whats before after between basically much many please another likely missing hello thanks
okay yeah article articles text video videos image picture photo post site website
google youtube wikipedia reddit twitter github gmail outlook explorer settings
musun mısın misin müsün nedir neden niye nasıl için gibi daha şimdi şuan burada orada
hakkında bunu şunu onun benim senin bana sana evet hayır tamam lütfen biraz
""".split())

#: Asking what was said or done before: every recent moment may be meant.
_REMEMBERING = re.compile(
    r"\b(?:remember\w*|recall\w*|last time|we (?:talked|spoke|chatted|discussed)|"
    r"you (?:said|asked|told|mentioned) (?:before|earlier|last)|did we|hatırl\w*|geçen sefer|"
    r"konuşmuştuk|konuştuk|söylemiştin|sormuştun|demiştin|bahsetmiştin)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Moment:
    """One exchange they gave a 👍."""

    kind: str  # "answer" or "remark"
    at: datetime
    topic: str  # the page's own title, without the site or the browser
    site: str
    reply: str
    message: str = ""  # what they said, for an answer
    why: str = ""  # why the remark was made

    def words(self) -> set[str]:
        said = self.message if self.kind == "answer" else self.reply
        return _words(f"{self.topic} {self.site} {said} {self.why}")


def title_parts(title: str) -> tuple[str, str]:
    """(topic, site): "Sourdough starter - Wikipedia - Brave" is ("Sourdough starter", "Wikipedia")."""
    parts = [p.strip() for p in _TITLE_PARTS.split(_BROWSER_SUFFIX.sub("", clean_title(title))) if p.strip()]
    if len(parts) >= 2:
        return " - ".join(parts[:-1]), parts[-1]
    return "", parts[0] if parts else ""


def liked_moments(records: list[dict]) -> list[Moment]:
    """Every reply with a 👍 as a moment, newest first."""
    moments = []
    for record in records:
        if record.get("rating") != "up" or not str(record.get("reply") or "").strip():
            continue
        try:
            at = datetime.fromisoformat(str(record.get("time") or record.get("rated_at")))
        except ValueError:
            continue
        topic, site = title_parts(str(record.get("page") or ""))
        moments.append(Moment(
            kind="remark" if record.get("kind") == "remark" else "answer",
            at=at, topic=topic, site=site,
            reply=" ".join(str(record["reply"]).split()),
            message=" ".join(str(record.get("message") or "").split()),
            why=" ".join(str(record.get("why") or "").split()),
        ))
    moments.sort(key=lambda m: m.at, reverse=True)
    return moments


def asks_to_remember(text: str) -> bool:
    return bool(_REMEMBERING.search(text or ""))


#: How close in meaning a moment must be to count (modules/llm/embeddings.py).
#: Measured: unrelated messages at most 0.26, real matches from 0.32.
MIN_SIMILARITY = 0.30


def document(moment: Moment) -> str:
    """A moment as EmbeddingGemma reads it: its page, and what was said."""
    from modules.llm.embeddings import DOCUMENT

    said = (f'they asked "{moment.message}" and you answered "{moment.reply}"' if moment.kind == "answer"
            else f'you said "{moment.reply}" because {moment.why}')
    return DOCUMENT.format(title=moment.topic or moment.site or "none", text=said)


def relevant_moments(moments: list[Moment], text: str, title: str = "", limit: int = 2,
                     embedder=None, min_similarity: float = MIN_SIMILARITY) -> list[Moment]:
    """The moments that bear on now, newest first among equals. With an
    `embedder`, those closest in meaning; without one, or when it can't be
    reached, those sharing the most words. Asked what was said before, the
    latest ones as well."""
    if embedder is not None and moments:
        query = f"{title} — {text}" if title else text
        scores = embedder.similarities(query, [document(m) for m in moments])
        if scores is not None:
            ranked = [moments[i] for i in sorted(range(len(moments)), key=lambda i: -scores[i])]
            matched = [m for m, i in zip(ranked, sorted(scores, reverse=True)) if i >= min_similarity]
            if asks_to_remember(text):
                # Asked what was said before, the rest follow by closeness, not
                # by age: "ekşi maya hakkında ne konuşmuştuk?" scores 0.13-0.18
                # against everything -- too low to match, but the closest of
                # them is still the one they mean, and the newest was not.
                matched += [m for m in ranked if m not in matched]
            return matched[:limit]
    topic, site = title_parts(title)
    # The site too: "Orbit Racer" or "MusicBox" is what they were doing. The
    # everyday ones -- Google, YouTube, Wikipedia -- are in GENERIC.
    now_words = _words(f"{topic} {site} {text}")
    shared = {m: len(m.words() & now_words) for m in moments}
    # sorted() keeps newest first among moments sharing as many words.
    matched = sorted((m for m in moments if shared[m]), key=lambda m: -shared[m])
    if asks_to_remember(text):
        matched += [m for m in moments if m not in matched]
    return matched[:limit]


def describe(moment: Moment, now: datetime | None = None) -> str:
    """One line: when, where, and what was said -- with the reason, for a remark."""
    now = now or datetime.now()
    days = (now.date() - moment.at.date()).days
    when = "today" if days == 0 else "yesterday" if days == 1 else f"{moment.at:%d %b}".lstrip("0")
    where = moment.topic or moment.site
    where = f", on {where}" if where else ""
    if moment.kind == "remark":
        reason = f' (your reason for saying it: "{_quote(moment.why)}")' if moment.why else ""
        return f'{when}{where}: YOU said to them, without being asked: "{_quote(moment.reply)}"{reason}'
    return f'{when}{where}: THEY asked "{_quote(moment.message)}", and YOU answered "{_quote(moment.reply)}"'


def _words(text: str) -> set[str]:
    return {w for w in _content_words(text) if w not in GENERIC}


def _quote(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
