"""What the user's own words ask the companion to do.

Some tools change things for the user: a note in their notebook, a timer that
will go off later. Whether to use one is not left to the model. Measured on
qwen3.5:4b, a plain "Interesting." or "Yes." produced notes, timers and
stopwatches nobody asked for -- 2 to 4 unasked notes in 24 ordinary messages --
and stricter tool descriptions made it worse, 8 to 13. So those decisions are
made here, in code, from the message itself.

- **Notes are never a model tool.** A note request is recognised here and saved
  by the app: the text the user dictated, or, for "note that down", the reply
  they are pointing at. The model is told what was saved.
- **Timers and the stopwatch stay model tools**, because turning "twenty minutes
  for the pasta" into arguments is what a model is good at. A call is only
  carried out if the message asks for that tool: a timer, cancelling one, or
  the stopwatch.

English and Turkish: the two languages the user speaks to it in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

#: Politeness and interjections allowed in front of an English request:
#: "okay, can you please note that down".
_PREFIX = (
    r"^\W*(?:(?:ok(?:ay)?|great|cool|nice|hey|also|and|so|oh|right|alright|"
    r"please)\b[\s,!.:-]*)*"
    r"(?:(?:can|could|would|will) you\s+)?(?:please\s+)?"
)
#: A request word must be followed by a space, punctuation or the end, so
#: "note taking apps" and "not all of it" are not requests.
_END = r"(?=[\s:;,.!?]|$)"

#: English requests lead the message: "note that…", "remember this for me: …".
_EN_START = re.compile(
    _PREFIX
    + r"(?:take (?:a )?note(?: of)?(?: that)?|make a note(?: of)?(?: that)?|"
    r"note (?:that|this|it) down|note down|note that|note\s*:|"
    r"jot (?:that |this |it )?down|write (?:that|this|it) down|"
    r"save (?:that|this|it)(?: as a note| to (?:my )?notes)?|"
    r"add (?:that|this|it) to (?:my )?notes|put (?:that|this|it) in (?:my )?notes|"
    r"remember (?:this|that)(?: for me)?|don'?t let me forget)"
    + _END,
    re.IGNORECASE,
)
#: …or close it: "the gears are bronze, note that".
_EN_END = re.compile(
    r"(?:^|[,;.]\s*)(?:please\s+)?(?:note|jot|write|save) (?:that|this|it)"
    r"(?: down)?(?: please)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
#: Turkish puts the object first, so the request can be anywhere:
#: "not al: …", "bunu not al", "dişlilerin bronz olduğunu not alır mısın?".
_TR = re.compile(
    r"(?:not (?:al(?:ır mısın|abilir misin)?|et(?:er misin)?|düş)|kaydet|"
    r"notlarıma ekle|aklında tut|unutma)" + _END,
    re.IGNORECASE,
)

#: Words that don't make a note on their own: "note that down please" points at
#: something said earlier rather than dictating anything.
_FILLER = {
    "that", "this", "it", "down", "please", "for", "me", "thanks", "thank",
    "you", "okay", "ok", "lütfen", "bunu", "şunu", "onu", "benim", "için",
    "teşekkürler", "sağol", "sağ", "ol",
}
_EDGE = " \t\r\n:;,.-–—\"'“”"


@dataclass(frozen=True)
class NoteRequest:
    #: What to save, as the user put it. Empty means "that": the reply they are
    #: referring to.
    text: str


def note_request(message: str) -> NoteRequest | None:
    """The note this message asks for, or None if it doesn't ask for one."""
    message = (message or "").strip()
    if not message:
        return None
    for pattern in (_EN_START, _TR):
        match = pattern.search(message)
        if match:
            after = _dictated(message[match.end():])
            before = _dictated(message[: match.start()])
            return NoteRequest(after or before)
    match = _EN_END.search(message)
    if match:
        return NoteRequest(_dictated(message[: match.start()]))
    return None


def _dictated(fragment: str) -> str:
    """The fragment as a note, or "" if it holds nothing but filler."""
    text = fragment.strip(_EDGE).rstrip("?!").strip(_EDGE)
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in _FILLER]
    return text if words else ""


_TIMING = re.compile(
    r"\b(?:timers?|stopwatch(?:es)?|alarms?|countdown|remind(?:ers?)?|wake me|"
    r"cancel|stop|minutes?|mins?|hours?|seconds|how long|"
    r"zamanlay\w*|kronometre\w*|alarm\w*|hatırlat\w*|iptal|durdur\w*|"
    r"geri say\w*|dakika\w*|saniye\w*)\b",
    re.IGNORECASE,
)


def asks_for_timing(message: str) -> bool:
    """Whether the message is about a timer, an alarm or the stopwatch."""
    return bool(_TIMING.search(message or ""))


_STOPWATCH = re.compile(r"\b(?:stopwatch(?:es)?|kronometre\w*)\b", re.IGNORECASE)
_COUNTDOWN = re.compile(
    r"\b(?:timers?|alarms?|countdown|remind\w*|wake me|minutes?|mins?|hours?|"
    r"seconds?|zamanlay\w*|alarm\w*|hatırlat\w*|geri say\w*|dakika\w*|saniye\w*)\b",
    re.IGNORECASE,
)
_CANCEL = re.compile(
    r"\b(?:cancel\w*|stop|turn off|never mind|iptal\w*|durdur\w*|kapat\w*)\b",
    re.IGNORECASE,
)


def _wants_stopwatch(message: str, arguments: dict) -> bool:
    if str(arguments.get("action", "")).strip().lower() == "check":
        return True  # reading it changes nothing
    return bool(_STOPWATCH.search(message))


def _wants_timer(message: str, arguments: dict) -> bool:
    return bool(_COUNTDOWN.search(message))


def _wants_cancel(message: str, arguments: dict) -> bool:
    # "Stop the stopwatch." is a stopwatch request, not a timer to cancel.
    return bool(_CANCEL.search(message)) and (
        bool(_COUNTDOWN.search(message)) or not _STOPWATCH.search(message)
    )


#: Tools that change something, and the check a call to one of them must pass
#: to run. Each tool has its own: with one shared "about timing" check, "Set a
#: one minute timer called tea." also started the stopwatch on qwen3.5:4b. A
#: state-changing tool missing from this table is refused outright: adding one
#: means deciding what asking for it looks like.
_CHANGES: dict[str, Callable[[str, dict], bool]] = {
    "set_timer": _wants_timer,
    "cancel_timer": _wants_cancel,
    "stopwatch": _wants_stopwatch,
}


def asked_for(tool_name: str, message: str, arguments: dict | None = None) -> bool:
    """Whether the message asks for what this state-changing tool call would do."""
    check = _CHANGES.get(tool_name)
    return bool(check and check(message or "", dict(arguments or {})))


#: Asking about the time right now -- not any mention of a day or a date.
_NOW = re.compile(
    r"\b(?:what time|time is it|the time|clock|what day|which day|what date|"
    r"today|tonight|tomorrow|right now|how long until|how much time|o'clock|"
    r"too late|saat kaç|bugün|yarın|tarih\w*|hangi gün|ne zaman)\b",
    re.IGNORECASE,
)
#: Asking about notes already taken.
_NOTES = re.compile(
    r"\b(?:notes?|noted|notebook|jot\w*|wrote down|write down|saved|"
    r"remember\w*|notlar\w*|notum\w*|notu|kaydett\w*|yazd\w*)\b",
    re.IGNORECASE,
)

#: Asking about something done earlier: a page, an article, a video.
_PAST = re.compile(
    r"\b(?:yesterday|earlier|last (?:week|night|time|month)|this morning|"
    r"the other day|previously|recently|i (?:was )?(?:read|reading|watched|watching|"
    r"looked at|saw|visited|opened)|that (?:article|video|page|site|website|post|thread)|"
    r"my (?:history|activity)|dün\w*|geçen\w*|önceki|daha önce|bu sabah|"
    r"okudu\w*|izledi\w*|baktı\w*|açtı\w*)\b",
    re.IGNORECASE,
)

#: Asking what the music playing is made of: key, chords, tempo. Not "song" or
#: "music" alone -- "what is this song about?" is about the words, and a small
#: model shown the tool would run it anyway.
_MUSIC = re.compile(
    r"\b(?:(?:what|which) key|in (?:what|which) key|key signature|"
    r"the key of (?:this|that|the) (?:song|music|piece|track|tune)|"
    r"chords?|chord progression|tempo|bpm|beats? per minute|"
    r"how fast is (?:this|that|the) (?:song|music|beat|track|piece|tune)|"
    r"what scale|major or minor|minor or major|"
    r"akor\w*|hangi ton\w*|tonu ne|tonunda|ritmi ne|kaç bpm|hızı ne)\b",
    re.IGNORECASE,
)

#: What a message has to be about for each tool to be offered at all. A model
#: cannot misuse a tool it is not shown. Offered every tool on every turn,
#: qwen3.5:4b checked the clock before replying to "Yes." 10 times in 10 near
#: midnight -- opening each reply with "It's late" -- and answered "go on" to
#: some sleep tips by reaching for timers until the turn ran out of rounds.
_TOPICS: dict[str, Callable[[str], bool]] = {
    "get_time": lambda message: bool(_NOW.search(message)),
    "set_timer": asks_for_timing,
    "cancel_timer": asks_for_timing,
    "list_timers": asks_for_timing,
    "stopwatch": asks_for_timing,
    "read_notes": lambda message: bool(_NOTES.search(message)),
    "search_notes": lambda message: bool(_NOTES.search(message)),
    "search_activity": lambda message: bool(_PAST.search(message)),
    "analyse_music": lambda message: bool(_MUSIC.search(message)),
}


def offered(tool_name: str, message: str) -> bool:
    """Whether to show the model this tool at all, for this message.

    A tool without a rule here is always offered, so a new tool doesn't vanish
    silently. That is safe because a state-changing tool also has to pass
    `asked_for` before it runs.
    """
    check = _TOPICS.get(tool_name)
    return True if check is None else check(message or "")
