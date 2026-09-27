"""The orchestrator: decides when the companion speaks unprompted, and what kind
of thing it says.

WHEN. The page on screen is a STANDING CANDIDATE: it persists for as long as the
user stays on it. Every tick the orchestrator asks two questions -- is the
candidate due (they have settled on it, it is prose rather than an interface, it
hasn't had its share of remarks, something has happened since the last try), and
does the moment allow speaking (the AttentionPolicy, plus whether audio is
playing). Only when both say yes does anything cost a model call. Waiting never
uses a page up; the event queue this replaced threw a blocked moment away.

Seeing and speaking stay separate. `observe()` is free and never speaks.
Describing the page costs a model call, so it happens lazily -- once per page,
when a remark is actually attempted. Flipping through tabs costs nothing.

WHAT. Code picks the kind of remark -- an opinion or a question -- and never the
same kind twice running; the model only writes it. The persona is taught by
example from a prompt file. The reply is JSON with a required `why`, so a remark
nobody can justify isn't made, and code refuses what breaks the persona however
the prompt is worded: advice, narration, greetings, and anything too close to
something said recently. Measured on six pages against the old one-paragraph
instruction: remarks opening with "That's..." went from 7 of 15 to none.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from core.logging import get_logger
from core.observer import Activity, describe
from core.types import Message, ScreenContext
from core.vision import encode, looks_like_picture

log = get_logger(__name__)

#: Used when no persona file is given, which in practice means in tests.
DEFAULT_PERSONA = (
    "You are a companion sitting beside someone at their desk while they use "
    "their computer. You are not an assistant. You keep them company and now "
    "and then say one short thing, the way a friend in the room would."
)

#: The kinds of remark, chosen in code, never the same twice running.
#:
#: Two more were tried and dropped, both measured on six pages:
#: - a "tell", a fact the page doesn't state: the most valuable kind, and the
#:   one qwen3:8b gets wrong. Three of six were dubious or invented outright
#:   ("you can set different brightness levels for each eye"), and a
#:   confidently wrong fact is the worst remark there is.
#: - a "noticing", a reaction to one detail: four of five just restated the
#:   detail -- "The hardware store manager worries about customers buying heavy
#:   items" -- which is the narration this design exists to prevent.
#: With two kinds, never twice running means they alternate.
MOVES: dict[str, str] = {
    "opinion": "give your honest take on one specific thing here",
    "question": "ask a genuine question this raises that the screen doesn't answer",
}
MOVE_ORDER = ("opinion", "question")

REMARK_SCHEMA = {
    "type": "object",
    "properties": {"say": {"type": "string"}, "why": {"type": "string"}},
    "required": ["say", "why"],
}

#: Openers that break the persona however the prompt is worded. Advice was the
#: one seen in testing -- on a coding page, "Check the cookie's secure flag".
_REFUSED_OPENERS = re.compile(
    r"^(check|try|make sure|you should|you might want|consider|remember to|"
    r"i see|it looks like|it seems like you|hi\b|hello|hey\b)",
    re.IGNORECASE,
)

#: A remark sharing this much of its vocabulary with one said recently is the
#: same remark again. Seen in testing even with recent remarks listed in the
#: prompt: "...burning the butter is a good reminder to watch the heat", twice
#: running, with only the first word changed.
MAX_OVERLAP_WITH_RECENT = 0.6

#: A remark with this many words in a row taken from the page is the page read
#: back. Scored blind, 47 remarks on six pages: a run of 6 or more
#: caught 3, all bad -- two were a YouTube comment word for word; at 5 it also
#: caught a good one ("Funny that the survey only counted the shops that wanted
#: to be counted."), and a share of the page's words caught good ones at every
#: threshold tried.
MAX_COPIED_RUN = 6


def copied_run(say: str, page: str) -> int:
    """The longest run of the remark's words, in order, that is also on the page."""
    said = re.findall(r"\w+", (say or "").lower())
    shown = " " + " ".join(re.findall(r"\w+", (page or "").lower())) + " "
    best = 0
    for start in range(len(said)):
        end = start + best + 1
        while end <= len(said) and " " + " ".join(said[start:end]) + " " in shown:
            best, end = end - start, end + 1
    return best


_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at by for with
from to into about as is are was were be been being do does did have has had
can could will would should may might must not no so just it its they them
their there here what which who when where why how all any some more most very
you your i me my we our he she his her
""".split())


def _content_words(text: str) -> set[str]:
    """The words that carry meaning: no stopwords, nothing under four letters."""
    words = set()
    for raw in text.lower().split():
        word = "".join(ch for ch in raw if ch.isalpha())
        if len(word) > 3 and word not in _STOPWORDS:
            words.add(word)
    return words

#: A page is prose if at least this share of its text sits in lines of eight
#: words or more. Measured: a Windows settings screen 14%; an article, a recipe,
#: a news story, a forum thread and a video page 55-87%. Interfaces were the one
#: kind of page the model never declined -- it found something to say about the
#: display settings three times out of three -- so this is decided in code.
MIN_PROSE_SHARE = 0.3

#: Unread counters and notification marks change a title without the page
#: changing: "(3) Inbox", "Inbox (12) - Gmail", "* Slack". Three digits at most,
#: so a year in a title -- "Parasite (2019)" -- is left alone.
_COUNTER = re.compile(r"\(\d{1,3}\+?\)")
_MARK = re.compile(r"^[•●◉*]\s+")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def clean_title(title: str) -> str:
    """A window title without unread counters or notification marks."""
    return " ".join(_MARK.sub("", _COUNTER.sub("", title or "")).split())


#: Browser names that end every window title and say nothing about the site.
_BROWSER_SUFFIX = re.compile(
    r"\s*[-–—|]\s*(?:Brave|Google Chrome|Chromium|Microsoft​? Edge|Mozilla Firefox|"
    r"Firefox|Opera|Vivaldi)\s*$",
    re.IGNORECASE,
)
_TITLE_PARTS = re.compile(r"\s+[-–—|·]\s+")


def site_of(title: str) -> str:
    """The site or app a window title belongs to.

    "Mars rover - NASA - Brave" is NASA; "Inbox - Outlook" is Outlook. The last
    part of the title, once the browser's own name is taken off.
    """
    title = _BROWSER_SUFFIX.sub("", clean_title(title))
    parts = [part.strip() for part in _TITLE_PARTS.split(title) if part.strip()]
    if len(parts) >= 2:
        return parts[-1][:40]
    return (parts[0] if parts else "(untitled)")[:40]


def _bucket(identity: str) -> float:
    """A fixed number in [0, 1) for a page, so a learned allowance skips the
    same pages every time rather than rolling dice."""
    return int(hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8], 16) / 0x100000000


def page_identity(context: ScreenContext) -> str:
    """Which page this is, stable across scrolling and unread counters.

    Title first, because for a browser the title IS the page: it changes when
    you navigate and holds still while you scroll. Falls back to a slice of the
    text only when a window has no title of its own.
    """
    title = clean_title(context.window_title or "")
    app = (context.app_name or "").strip()
    if title:
        return f"{app}::{title}"
    return f"{app}::{(context.text or '')[:100]}"


def prose_share(text: str) -> float:
    """How much of the text is in lines long enough to be sentences."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    total = sum(len(line) for line in lines)
    if not total:
        return 0.0
    return sum(len(line) for line in lines if len(line.split()) >= 8) / total


#: What the moment adds to the situation, when it is more than arriving.
_MOMENT_NOTES = {
    "media_end": " What they were watching or listening to has just finished or paused.",
    "page_end": " They have just reached the end of the page.",
}

_EXAMPLE_LINE = re.compile(r'^\s*[-*]\s*["“](.+?)["”]\s*$', re.MULTILINE)


def persona_examples(persona: str) -> list[str]:
    """The example remarks in a persona: its quoted bullet lines."""
    return [match.group(1) for match in _EXAMPLE_LINE.finditer(persona or "")]


def load_persona(path: Path | str) -> str:
    """The persona file, minus the comments left for whoever edits it."""
    return _COMMENT.sub("", Path(path).read_text(encoding="utf-8")).strip()


@dataclass
class Remark:
    text: str
    move: str
    why: str
    trigger: str
    activity: str
    #: The window the remark was about.
    page: str = ""


@dataclass
class Candidate:
    """The page on screen, as something that might be worth a remark."""

    identity: str
    context: ScreenContext
    arrived_at: float
    #: Last time the screen settled on it -- arriving, or a scroll stopping.
    settled_at: float
    remarks: int = 0
    last_remark_at: float = float("-inf")
    attempts: int = 0
    last_attempt_at: float = float("-inf")
    #: Described lazily, at the first attempt, then reused for the page.
    activity: Activity | None = None
    #: For a page short on prose: whether it shows a picture (None: not looked
    #: at yet), and that screenshot, encoded, for describing and remarking.
    picture: bool | None = None
    image: bytes | None = None
    #: After a remark here: "replied" or "dismissed", once the user did either.
    outcome: str = ""
    #: Natural moments: the latest scroll position the app
    #: reported, whether the end of the page was reached (and remarked at), and
    #: the longest stretch of sound that played while on it.
    scroll: float | None = None
    reached_end: bool = False
    end_used: bool = False
    media_s: float = 0.0
    #: How welcome remarks are on this page's site, learned; and
    #: the kind and moment of the last remark here, for counting its outcome.
    allowance: float = 1.0
    last_move: str = ""
    last_trigger: str = ""


class Orchestrator:
    """Turns a standing candidate into a remark, or into nothing at all."""

    def __init__(
        self,
        llm,
        policy,
        max_words: int = 25,
        memory=None,
        temperature: float | None = None,
        min_time_on_page_s: float = 10.0,
        dwell_seconds: float = 300.0,
        max_remarks_per_page: int = 2,
        max_attempts_per_page: int = 3,
        persona: str = DEFAULT_PERSONA,
        clock: Callable[[], float] = time.time,
        screenshot: Callable[[], object] | None = None,
        vision_edge: int = 768,
        min_picture_colours: int = 48,
        watching: Callable[[], bool] | None = None,
        on_leave: Callable[["Candidate", float], None] | None = None,
        natural_moments: bool = False,
        media_min_s: float = 20.0,
        audio_quiet_s: float = 2.0,
        page_end_percent: float = 95.0,
        moment_wait_s: float = 90.0,
        long_stay_s: float = 180.0,
        parting_window_s: float = 30.0,
        about: Callable[["Candidate"], list[str]] | None = None,
        recall: Callable[["Candidate"], str | None] | None = None,
        callbacks: bool = False,
        shared: Callable[["Candidate"], list[tuple[str, str]]] | None = None,
        learning=None,
        outcome_window_s: float = 120.0,
    ) -> None:
        # what was learned about where remarks are welcome
        # (core.learning.Learning), and how long a remark waits for a reaction
        # before it counts as ignored.
        self.learning = learning
        self.outcome_window_s = outcome_window_s
        #: The last unprompted remark still waiting for a reaction, and when.
        self._awaiting: tuple[Candidate, float] | None = None
        # `about` gives the facts in about_you.md that bear on a
        # page (the caller decides which, in code); `recall` finds a
        # page from an earlier day related to this one. A remark gets that
        # earlier page at most once a day, and only with `callbacks`.
        self.about = about
        self.recall = recall
        self.callbacks = callbacks
        self._recalled_on = ""
        # `shared` gives the liked moments that bear on a page, as
        # (the line for the prompt, what was said then). A remark gets one, and
        # each is offered once a session, so a page doesn't keep circling back.
        self.shared = shared
        self._shared_offered: set[str] = set()
        # Natural moments, best first: something that was playing
        # ends; the end of the page is reached; a long-read page is left; the
        # page just opened, which is all there was before. Off: only the last.
        self.natural_moments = natural_moments
        self.media_min_s = media_min_s
        self.audio_quiet_s = audio_quiet_s
        self.page_end_percent = page_end_percent
        self.moment_wait_s = moment_wait_s
        self.long_stay_s = long_stay_s
        self.parting_window_s = parting_window_s
        #: A page just left after a long stay, and when: remarkable for a moment.
        self.parting: tuple[Candidate, float] | None = None
        # Called with the page just left and the time it was left: the
        # activity log's source.
        self.on_leave = on_leave
        # `screenshot` returns the screen as a PIL image, or None when vision is
        # off; it runs the privacy check first. Without it, a page short on
        # prose is always treated as an interface, as before F6.
        self.screenshot = screenshot
        self.vision_edge = vision_edge
        self.min_picture_colours = min_picture_colours
        # True while they have asked it to watch the screen and the settings
        # allow it: each remark then gets a screenshot of its own.
        self.watching = watching
        self.llm = llm
        self.policy = policy
        self.max_words = max_words
        # Composing only. Describing the activity stays at the model's own
        # temperature, where a stable one-line summary is the point.
        self.temperature = temperature
        # Remarks are composed without history, but recorded into it: the
        # user's next message is often a reply to one. `is not None` checks
        # only -- an empty ConversationMemory is falsy.
        self.memory = memory
        self.min_time_on_page_s = min_time_on_page_s
        self.dwell_seconds = dwell_seconds
        self.max_remarks_per_page = max_remarks_per_page
        # Bounds the model calls spent on a page that never yields anything.
        self.max_attempts_per_page = max_attempts_per_page
        self.persona = persona or DEFAULT_PERSONA
        #: The language pinned with the lang button: remarks are made in it.
        #: None leaves the language to the persona and the page.
        self.reply_language: str | None = None
        #: The persona's example remarks, which must never come back as remarks.
        self.examples = persona_examples(self.persona)
        self.clock = clock
        self.current: Candidate | None = None
        #: The kind of the last remark actually made, and of the last one tried.
        self.last_move: str | None = None
        self._last_tried: str | None = None
        #: Recent remarks, shown to the model as things not to repeat.
        self.recent: list[str] = []
        self._waiting_for: str | None = None

    # -- what happens ---------------------------------------------------------

    def observe(self, context: ScreenContext) -> None:
        """The screen settled. Free: no model call, and it never speaks."""
        now = self.clock()
        identity = page_identity(context)
        if self.current is not None and self.current.identity == identity:
            # Same page, settled again: they scrolled and stopped somewhere.
            self.current.context = context
            self.current.settled_at = now
            self._note_scroll(self.current, context)
            return
        previous = self.current
        if (
            self.natural_moments
            and previous is not None
            and previous.remarks == 0
            and now - previous.arrived_at >= self.long_stay_s
            and self._moment_liked("leaving")
        ):
            # Leaving a page read for a long time, without a word said about
            # it, is a natural moment to say one -- about that page.
            self.parting = (previous, now)
        self.leave(now)
        self.current = Candidate(identity, context, arrived_at=now, settled_at=now)
        if self.learning is not None:
            self.current.allowance = self.learning.allowance(site_of(context.window_title or ""))
        self._note_scroll(self.current, context)
        self._waiting_for = None
        log.debug("on a new page: %s", identity)

    def _note_scroll(self, c: Candidate, context: ScreenContext) -> None:
        if context.scroll is None:
            return
        if c.scroll is None:
            log.debug("%s reports its scroll position", c.identity)
        c.scroll = context.scroll
        if context.scroll >= self.page_end_percent and not c.reached_end:
            c.reached_end = True
            log.info("reached the end of %s", c.identity)

    def _note_sound(self, c: Candidate, sound: tuple[float, float] | None) -> None:
        """Remember sound that played on this page for long enough to matter.

        `sound` is (length of the latest stretch, seconds since it stopped).
        Only a stretch that began after arriving counts: music already playing
        when the page was opened says nothing about the page.
        """
        if sound is None:
            return
        stretch, since = sound
        began = self.clock() - since - stretch
        if since < self.audio_quiet_s and stretch >= self.media_min_s and began >= c.arrived_at - 2:
            c.media_s = max(c.media_s, stretch)

    def note_user_message(self, reply: bool = True) -> None:
        """The user asked or spoke (`reply`), or stopped the companion.

        Either is the answer to a remark still waiting for one: a reply counts
        for remarks there, stopping it counts against.
        """
        self.policy.note_user_activity()
        c = self._awaiting_reaction()
        if c is not None:
            c.outcome = "replied" if reply else "dismissed"
            self._learn(c, "positive" if reply else "negative")
            self._awaiting = None

    def note_quiet(self) -> None:
        """Quiet was switched on. Right after a remark, that remark wasn't wanted."""
        c = self._awaiting_reaction()
        if c is not None:
            c.outcome = "dismissed"
            self._learn(c, "negative")
            self._awaiting = None

    def note_rating(self, title: str, move: str, trigger: str, rating: str) -> None:
        """A thumbs up or down on a remark in the window."""
        if self.learning is not None:
            self.learning.record(site_of(title), move, trigger,
                                 "positive" if rating == "up" else "negative")

    def _awaiting_reaction(self) -> Candidate | None:
        if self._awaiting is None:
            return None
        c, at = self._awaiting
        if c.outcome or self.clock() - at > self.outcome_window_s:
            return None
        return c

    def _expire_awaiting(self, now: float) -> None:
        """A remark with no reaction in time was ignored: logged, counted as nothing."""
        if self._awaiting is None:
            return
        c, at = self._awaiting
        if c.outcome:
            self._awaiting = None
        elif now - at > self.outcome_window_s:
            c.outcome = "ignored"
            self._learn(c, "ignored")
            self._awaiting = None

    def _learn(self, c: Candidate, outcome: str) -> None:
        if self.learning is not None:
            self.learning.record(site_of(c.context.window_title or ""), c.last_move,
                                 c.last_trigger, outcome)

    def _moment_liked(self, moment: str) -> bool:
        return self.learning is None or self.learning.liked("moments", moment)

    def leave(self, now: float | None = None) -> None:
        """The page on screen is no longer this one, or the app is closing."""
        c, self.current = self.current, None
        if c is None or self.on_leave is None:
            return
        try:
            self.on_leave(c, self.clock() if now is None else now)
        except Exception:  # the log must never break watching
            log.warning("could not record the visit to %s", c.identity, exc_info=True)

    # -- the tick -------------------------------------------------------------

    def poll(
        self,
        busy: bool = False,
        hold: bool = False,
        sound: tuple[float, float] | None = None,
    ) -> Remark | None:
        """Called every idle tick. Returns a remark if this is the moment.

        Nothing is consumed by waiting: a candidate that is not due, or is held
        up by the policy or by audio, is simply asked again on the next tick.
        `sound`: (latest stretch of sound, seconds since it stopped), when
        system audio is being listened to.
        """
        now = self.clock()
        self._expire_awaiting(now)
        if self.parting is not None:
            remark = self._poll_parting(now, busy, hold)
            if remark is not None or self.parting is not None:
                return remark
        candidate = self.current
        if candidate is None:
            return None
        self._note_sound(candidate, sound)

        not_due = self._not_due(candidate, now)
        if not_due is not None:
            self._waiting(not_due, candidate, important=False)
            return None
        moment = self._moment(candidate, now)
        if moment is None:
            self._waiting("waiting for a better moment: the end of the page", candidate,
                          important=False)
            return None
        wait = "audio is playing" if hold else self.policy.reason_to_wait(
            busy=busy,
            # A picture is something on screen even with no text at all.
            text_chars=None if candidate.picture else len(candidate.context.text or ""),
        )
        if wait is not None:
            self._waiting(wait, candidate, important=True)
            return None

        self._waiting_for = None
        return self._attempt(candidate, now, moment=moment)

    def _moment(self, c: Candidate, now: float) -> str | None:
        """Which moment this is, best first -- or None to wait for a better one.

        A better moment takes the place of a weaker one that is waiting; it
        never adds a remark beyond the page's limits, which `_not_due` keeps.
        """
        revisit = c.remarks > 0
        if not self.natural_moments:
            return "revisit" if revisit else "arrived"
        # A moment the user has clearly shown they don't want is passed over.
        if c.media_s >= self.media_min_s and self._moment_liked("media_end"):
            return "media_end"  # still playing is `hold`, so this is after it
        if c.reached_end and not c.end_used and self._moment_liked("page_end"):
            return "page_end"
        if revisit:
            return "revisit"
        if (c.scroll is not None and now - c.arrived_at < self.moment_wait_s
                and self._moment_liked("page_end")):
            return None  # the page reports scrolling: the end of it may come
        return "arrived"

    def _poll_parting(self, now: float, busy: bool, hold: bool) -> Remark | None:
        """A remark about the page just left, if it still fits the moment."""
        c, left_at = self.parting
        if now - left_at > self.parting_window_s:
            log.debug("the moment to remark on %s has passed", c.identity)
            self.parting = None
            return None
        text = c.context.text or ""
        # Checked from the text alone: a screenshot now would show the new page.
        thin = not c.picture and (
            prose_share(text) < MIN_PROSE_SHARE
            or len(text) < getattr(self.policy, "min_chars", 0)
        )
        if thin or c.attempts >= self.max_attempts_per_page:
            self.parting = None
            return None
        wait = "audio is playing" if hold else self.policy.reason_to_wait(
            busy=busy, text_chars=None if c.picture else len(text)
        )
        if wait is not None:
            self._waiting(wait, c, important=True)
            return None
        self.parting = None
        return self._attempt(c, now, moment="leaving", stayed=left_at - c.arrived_at)

    def _not_due(self, c: Candidate, now: float) -> str | None:
        # The learned allowance (1 unless learning is on) scales the waiting and
        # the limits for this site: under 1, a longer settle, some pages passed
        # over, fewer remarks a page, longer between them; over 1, sooner.
        allowance = c.allowance
        if now - c.arrived_at < self.min_time_on_page_s / allowance:
            return "settling in"
        thin = self._thin(c)
        if thin == "interface":
            return "an interface, not something to talk about"
        if allowance < 1 and _bucket(c.identity) >= allowance:
            return "remarks here are rarely welcome (learned)"
        if c.remarks >= max(1, round(self.max_remarks_per_page * min(allowance, 1.0))):
            return "said enough about this page"
        if c.attempts >= self.max_attempts_per_page:
            return "tried enough on this page"
        if c.attempts and c.settled_at <= c.last_attempt_at:
            # After a try -- successful or not -- wait for something to happen:
            # a scroll that stops. Otherwise a declined page is retried every tick.
            return "nothing new since the last try"
        if c.remarks and now - c.last_remark_at < self.dwell_seconds / allowance:
            return "remarked on this page recently"
        return None

    def _thin(self, c: Candidate) -> str | None:
        """"interface" or "short" if the page has too little to talk about;
        None if it has prose enough, or a picture.

        A page short on prose OR on text gets its picture looked at. A photo's
        caption can be one long line -- prose, but a few dozen characters -- and
        checking only the prose share left the gallery test page refused by the
        length check 8 times in 8.
        """
        text = c.context.text or ""
        is_prose = prose_share(text) >= MIN_PROSE_SHARE
        enough = len(text) >= getattr(self.policy, "min_chars", 0)
        if is_prose and enough:
            return None
        if self._is_picture(c):
            return None
        return "interface" if not is_prose else "short"

    def _is_picture(self, c: Candidate) -> bool:
        """Whether a page short on prose shows a picture worth talking about.

        Looked at once per page -- one screenshot, when the page is otherwise
        due -- never on a timer. An interface (few colours) stays refused
        without a model call; a photo or a video frame becomes a candidate.
        """
        if c.picture is not None:
            return c.picture
        c.picture = False
        if self.screenshot is None:
            return False
        try:
            shot = self.screenshot()
        except Exception:  # a blocked window, a failed grab: no picture
            log.debug("no screenshot for %s", c.identity, exc_info=True)
            return False
        if shot is not None and looks_like_picture(shot, self.min_picture_colours):
            c.picture = True
            c.image = encode(shot, self.vision_edge)
            log.info("a picture on %s: remarks can use it", c.identity)
        return c.picture

    def _waiting(self, why: str, c: Candidate, important: bool) -> None:
        """Log a reason once when it changes, not once per tick."""
        if why == self._waiting_for:
            return
        self._waiting_for = why
        if important:
            log.info("waiting to remark on %s: %s", c.identity, why)
        else:
            log.debug("not remarking on %s: %s", c.identity, why)

    def remark_now(
        self, context: ScreenContext | None = None
    ) -> tuple[Remark | None, str]:
        """A remark they asked for, about the page on screen now.

        Skips the waiting -- time on page, cooldown, hourly budget, the per-page
        limit, Quiet -- because asking is the moment. Keeps every check on what
        is worth saying: an interface or a near-empty screen is declined before
        any model call, and the composed remark meets the same refusals as an
        unprompted one. Returns the remark, or None and why it wasn't made.
        """
        if context is not None:
            self.observe(context)
        c = self.current
        if c is None:
            return None, "nothing on screen to talk about"
        thin = self._thin(c)
        if thin == "interface":
            return None, "an interface, not something to talk about"
        if thin == "short":
            return None, "not enough on screen"
        remark = self._attempt(c, self.clock(), requested=True)
        if remark is None:
            return None, "nothing worth saying"
        self._learn(c, "positive")  # asking for a remark is the plainest welcome
        return remark, ""

    def _attempt(
        self,
        c: Candidate,
        now: float,
        requested: bool = False,
        moment: str | None = None,
        stayed: float | None = None,
    ) -> Remark | None:
        c.attempts += 1
        c.last_attempt_at = now
        if c.activity is None:
            c.activity = describe(self.llm, c.context, image=c.image if c.picture else None)
        if not c.activity:
            return None

        move = self._next_move()
        revisit = c.remarks > 0
        # Requested remarks are told apart from unprompted ones, so what the
        # user asks for never counts as evidence about unprompted timing; the
        # moment is recorded too, for learning which moments work (F10).
        trigger = "requested" if requested else (
            moment or ("revisit" if revisit else "arrived")
        )
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        earlier = None
        if self.callbacks and self.recall is not None and self._recalled_on != today:
            try:
                earlier = self.recall(c)
            except Exception:
                log.debug("could not recall an earlier page", exc_info=True)
        moment, liked = None, []
        if self.shared is not None:
            try:
                liked = list(self.shared(c))
                moment = next((m for m in liked if m[0] not in self._shared_offered), None)
            except Exception:
                log.debug("could not read the shared moments", exc_info=True)
        facts = []
        if self.about is not None:
            try:
                facts = list(self.about(c))[:5]
            except Exception:
                log.debug("could not read the facts file", exc_info=True)
        # Watching: a screenshot taken now, for this remark alone. Nothing keeps
        # it -- not the candidate, not memory -- and it is dropped once composed.
        fresh = self._watch_screenshot()
        messages = self.build_prompt(
            c.activity, c.context.text or "",
            stayed if stayed is not None else now - c.arrived_at,
            revisit, move, moment=trigger, about=facts, earlier=earlier,
            screenshot=fresh is not None, shared=moment[0] if moment else None,
        )
        if fresh is not None:
            messages[-1] = replace(messages[-1], images=(fresh,))
        elif c.picture and c.image is not None:
            messages[-1] = replace(messages[-1], images=(c.image,))
        # Every liked moment here, not only the one offered: saying any of them
        # again is a replay.
        written = self._compose(messages, avoid=[said for _line, said in liked], page=c.context.text or "")
        del fresh, messages
        if written is None:
            log.info("nothing worth saying (%s) about: %s", move, c.activity.summary)
            return None

        text, why = written
        c.remarks += 1
        c.last_remark_at = now
        c.last_move, c.last_trigger = move, trigger
        if not requested:
            # An earlier remark still unanswered is overtaken: ignored.
            if self._awaiting is not None and not self._awaiting[0].outcome:
                self._awaiting[0].outcome = "ignored"
                self._learn(self._awaiting[0], "ignored")
            self._awaiting = (c, now)
        self.last_move = move
        self.recent = (self.recent + [text])[-3:]
        self.policy.note_spoke()
        if self.memory is not None:
            self.memory.add_remark(text, why)
        if earlier:
            self._recalled_on = today  # one remark a day gets an earlier page
        if moment:
            self._shared_offered.add(moment[0])
        if trigger == "media_end":
            c.media_s = 0.0  # that stretch has had its remark
        elif trigger == "page_end":
            c.end_used = True
        log.info("remark [%s, %s]: %s  (why: %s)", trigger, move, text, why)
        return Remark(text=text, move=move, why=why, trigger=trigger,
                      activity=c.activity.summary,
                      page=c.context.window_title or c.context.app_name or "")

    def _watch_screenshot(self) -> bytes | None:
        """A screenshot taken now for one remark, while they have asked it to
        watch; None otherwise, or when one can't be taken."""
        if self.watching is None or self.screenshot is None or not self.watching():
            return None
        try:
            shot = self.screenshot()
        except Exception:  # a blocked window, a failed grab: the text alone
            log.debug("no screenshot for a remark while watching", exc_info=True)
            return None
        if shot is None:
            return None
        log.info("remark written from a screenshot taken for it (watching)")
        return encode(shot, self.vision_edge)

    def _next_move(self) -> str:
        """The next kind in turn, never the kind of the last remark made.

        Rotates through attempts, so a declined kind isn't simply asked again.
        """
        start = (
            MOVE_ORDER.index(self._last_tried) + 1
            if self._last_tried in MOVE_ORDER
            else 0
        )
        order = [MOVE_ORDER[(start + i) % len(MOVE_ORDER)] for i in range(len(MOVE_ORDER))]
        # A kind the user clearly doesn't want is dropped; then
        # the one left may come twice running.
        kinds = [m for m in order if self.learning is None or self.learning.liked("moves", m)] or order
        for move in kinds:
            if move != self.last_move or len(kinds) == 1:
                self._last_tried = move
                return move
        return kinds[0]

    # -- the prompt -----------------------------------------------------------

    def build_prompt(
        self,
        activity: Activity,
        detail: str,
        dwell: float,
        revisit: bool,
        move: str,
        moment: str = "arrived",
        about: list[str] | None = None,
        earlier: str | None = None,
        screenshot: bool = False,
        shared: str | None = None,
    ) -> list[Message]:
        """Persona as the system prompt; the situation and the job as the turn.

        `screenshot`: a screenshot taken just now goes with it (watching).
        """
        if moment == "leaving":
            situation = f"They were {activity.summary}"
            if dwell > 120:
                situation += f" for about {dwell / 60:.0f} minutes"
            situation += ", and have just moved on to something else."
        else:
            situation = f"They are {activity.summary}."
            if dwell > 120:
                situation += f" They have been at it for about {dwell / 60:.0f} minutes."
        if revisit:
            situation += (
                " You have already said something about this page, so this has "
                "to be a new thought."
            )
        situation += _MOMENT_NOTES.get(moment, "")
        if about:
            situation += " About them, for context only: " + "; ".join(about) + "."
        if earlier:
            situation += (
                f" On an earlier day they were on: {earlier}. Connect to it only if "
                "it truly relates, in a few words."
            )
        if shared:
            # Measured on the page the liked remark was made on, 12 remarks each:
            # "connect to it only if it truly relates, without repeating it" had
            # 6 ask the same question again in other words; this, 3 -- still
            # calling back to it 8 times.
            situation += (
                f" Something from before that they liked — {shared}. That point is made: say something "
                "NEW about this page. You may build on it or call back to it in a few words, but not ask "
                "or say the same thing again."
            )
        recent = ""
        if self.recent:
            listed = "\n".join(f'- "{r}"' for r in self.recent)
            recent = (
                "\n\nThings you said recently. Don't repeat them or say "
                f"anything like them:\n{listed}"
            )
        language = ""
        if self.reply_language:
            from modules.voice.language import LANGUAGE_NAMES

            language = f", in {LANGUAGE_NAMES.get(self.reply_language, self.reply_language)}"
        if screenshot:
            # Watching: the screenshot instead of the text. With the text there
            # too, 2 of 5 remarks used the screenshot; without it, 4 of 5.
            screen = ("What is on their screen is in the attached screenshot, taken just now. "
                      "They can see it, so describing it tells them nothing.\n\n")
        else:
            screen = ("What is on their screen, for reference. They can see it, so "
                      f"repeating any of it tells them nothing:\n{detail[:1500]}\n\n")
        user = (
            f"{situation}\n\n"
            f"{screen}"
            f"Say one thing: {MOVES[move]}.{recent}\n\n"
            f"One sentence, at most {self.max_words} words{language}. Reply as JSON with "
            '"say" (the sentence) and "why" (why it is worth saying to them '
            "now)."
        )
        # No invitation to decline. Offered "if nothing is worth saying, use
        # empty strings", small models took it: qwen3.5:4b declined 3 remarks
        # in 10, qwen3-vl:4b-instruct 10 in 10, while qwen3:8b made 9 or 10
        # either way. Whether a page deserves a remark is decided in code --
        # the prose check, the required reason, the refusals -- not invited.
        return [
            Message(role="system", content=self.persona),
            Message(role="user", content=user),
        ]

    def _compose(self, messages: list[Message], avoid: list[str] = (), page: str = "") -> tuple[str, str] | None:
        """(remark, why), or None if there is nothing that should be said."""
        try:
            raw = "".join(
                self.llm.chat(
                    messages,
                    stream=False,
                    temperature=self.temperature,
                    json_schema=REMARK_SCHEMA,
                )
            ).strip()
        except Exception:
            log.warning("could not compose a remark", exc_info=True)
            return None
        try:
            reply = json.loads(raw)
        except ValueError:
            log.info("remark was not valid JSON: %r", raw[:80])
            return None
        if not isinstance(reply, dict):
            return None

        say = str(reply.get("say") or "").strip().strip('"').strip()
        why = str(reply.get("why") or "").strip()
        if not say or say.upper().startswith("NOTHING"):
            return None
        if len(why.split()) < 3:
            # Accountability as structure: a remark nobody can justify isn't made.
            log.info("dropped a remark with no reason given: %s", say)
            return None
        if _REFUSED_OPENERS.match(say):
            log.info("dropped a remark that advises, narrates or greets: %s", say)
            return None
        if copied_run(say, page) >= MAX_COPIED_RUN:
            log.info("dropped a remark copied from the page: %s", say)
            return None
        said = _content_words(say)
        for earlier in self.recent:
            if said and len(said & _content_words(earlier)) / len(said) >= MAX_OVERLAP_WITH_RECENT:
                log.info("dropped a remark too close to one said recently: %s", say)
                return None
        for before in avoid:
            # A liked remark coming back as itself is a replay, not a memory.
            if said and len(said & _content_words(before)) / len(said) >= MAX_OVERLAP_WITH_RECENT:
                log.info("dropped a remark repeating a shared moment: %s", say)
                return None
        for example in self.examples:
            # Seen in use: on a chat window full of test output, the model said
            # the persona's "Wait, if the ice core is that old, how did they
            # date the bottom layer?" word for word, with an invented reason.
            if said and len(said & _content_words(example)) / len(said) >= MAX_OVERLAP_WITH_RECENT:
                log.info("dropped a remark copied from the persona's examples: %s", say)
                return None
        words = say.split()
        if len(words) > self.max_words * 2:
            # A runaway paragraph is a failed remark; better cut than recited.
            say = " ".join(words[: self.max_words * 2]).rstrip(",;:") + "…"
        return say, why
