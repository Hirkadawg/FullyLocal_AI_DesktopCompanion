"""A mood for the avatar's face, decided in code from what was said.

Asking the model to begin each reply with a mood tag ([happy], [sad]...) was
measured first, on qwen3.5:4b. The tag itself was reliable (12 of 12 at the
start, none stray), but the moods weren't: [happy] for "17 times 23", vaccines
and why the sky is blue, [thinking] for a surprising fact; 5 of 12 right, then 8
of 12 on a second set. And the instruction made replies 19% longer, against the
user's wish for short answers.

These rules, reading the user's message and the reply, got 12 of 12 on the set
they were written on and 11 of 12 on a held-out set (the miss, "Is it better
to rent or buy a house?", led to the choice-question rule). They never touch
the prompt. English and Turkish.
"""

from __future__ import annotations

import re

MOODS = ("neutral", "happy", "surprised", "sad", "thinking")

_SAD = re.compile(
    r"\b(sorry|loss|condolences?|heartbreaking|frustrating|exhausting|tough|hard time|"
    r"üzgünüm|başın sağ olsun|geçmiş olsun|çok üzüldüm|yorucu)\b"
    r"|\b(died|passed away|lost my|tired|exhausted|sad|depressed|failed|"
    r"öldü|kaybettim|yorgunum|kaldım)\b",
    re.IGNORECASE,
)
_HAPPY = re.compile(
    r"\b(congratulations|congrats|great news|incredible news|amazing news|wonderful news|"
    r"well done|proud of you|tebrikler|tebrik ederim|harika( bir)? haber|çok sevindim)\b",
    re.IGNORECASE,
)
_SURPRISED = re.compile(
    r"\b(did you know|guess what|believe it or not|biliyor muydun|inanır mısın|tahmin et)\b"
    r"|^(wow|whoa|really\??|oh wow|vay)\b"
    r"|\b(that's (surprising|unusual|remarkable)|how unusual)\b",
    re.IGNORECASE,
)
_CHOICE = re.compile(
    r"\b(should i|which (one|is better|\w+ should)|is it better|what would you (do|choose)|"
    r"pros and cons|hangisi|ne yapmalıyım)\b|\b(mı|mi|mu|mü) yoksa\b",
    re.IGNORECASE,
)


def mood_of(message: str, reply: str = "") -> str:
    """One of MOODS for this exchange; "neutral" unless something says otherwise."""
    message, reply = message or "", reply or ""
    if _SAD.search(message) or _SAD.search(reply):
        return "sad"
    if _HAPPY.search(reply) or _HAPPY.search(message):
        return "happy"
    if _SURPRISED.search(message) or _SURPRISED.search(reply[:60]):
        return "surprised"
    # A question offering a choice: "X or Y?", "should I...", "mı yoksa".
    if _CHOICE.search(message) or (message.rstrip().endswith("?") and re.search(r"\bor\b", message, re.I)):
        return "thinking"
    return "neutral"
