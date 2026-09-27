"""How long an answer should be, decided in code for each turn.

The user asked for 2-3 sentence answers. Measured on qwen3.5:4b with the test
screenshot, sentences weren't the problem -- questions already came back in 2-3
of them -- but the sentences were long (30-100 words), replies to the
companion's own question-remark ran to 45-62 words ("Not really."), and "explain
in detail" got no more than 33-85 words.

So the length is said in words, and only in the turn it applies to: a standing
system-prompt rule once broke how "Yes." was answered. A
first try worded in sentences made "Yes." worse (5 words became 29-42). Worded
in words, 12 of 12 replies came within ~20 words, 25 of 28 questions within ~60,
and detailed requests grew to 96-242 words.

Reported in use (15 Sep), and fixed:
- "more details" was taken for a casual reply -- any four words without a
  question mark were -- so the model squeezed its last answer into one sentence
  and seemed to repeat itself. A short message is a reply now only after a
  remark, or when it is an acknowledgement ("yes", "ok", "tamam").
- "more detailed answer please" got "several paragraphs if it needs them" and
  wrote ~450 words under Markdown headings, which are also read aloud. A request
  for detail now has a size and must be plain prose.
"""

from __future__ import annotations

import re

#: The settings page's choices.
LENGTHS = ("short", "normal", "detailed")

#: Asking for more, or for detail: "more details", "elaborate", "detaylı anlat".
_DETAILED = re.compile(
    r"\b(in detail|detailed|in depth|in more depth|more depth|step by step|thoroughly|"
    r"elaborate|expand( on (it|that|this))?|walk me through|go deeper|"
    r"more (details?|info(rmation)?|about (it|that|this)|please)|give me more|tell me more|"
    r"explain (it |that |this )?(fully|properly|everything|more)|tell me everything|"
    r"detaylı|detaylıca|detay ver|detaylandır|ayrıntılı(ca)?|ayrıntı ver|adım adım|uzun uzun|"
    r"daha fazla|biraz daha (anlat|açıkla)|daha çok anlat|her şeyi anlat)\b",
    re.IGNORECASE,
)
#: Asking it to carry on: no length note, so a cut-off answer can finish.
_CONTINUE = re.compile(
    r"^\W*(go on|continue|keep going|and then|more|devam( et)?|sonra)\W*$",
    re.IGNORECASE,
)
#: A few words that acknowledge rather than ask: a reply, even with no remark.
_ACKNOWLEDGE = re.compile(
    r"^\W*(yes|yeah|yep|yup|no|nope|nah|ok|okay|sure|thanks|thank you|nice|cool|great|"
    r"right|true|maybe|not really|i see|got it|fair enough|interesting|hm+|haha|lol|wow|"
    r"evet|hayır|yok|tamam|olur|peki|teşekkürler|teşekkür ederim|sağ ol|güzel|harika|"
    r"anladım|belki|ilginç)\b",
    re.IGNORECASE,
)

_PLAIN = "in plain prose, with no headings, lists or bold"
_MORE = "If it follows your last answer, add to it rather than repeating it."

#: (kind of message, chosen length) -> the note for that turn, or "" for none.
_NOTES = {
    ("reply", "short"): "Reply in one short sentence, under 15 words.",
    ("reply", "normal"): "Reply in one short sentence, under 20 words.",
    ("reply", "detailed"): "Reply in one or two short sentences.",
    ("question", "short"): "At most about 30 words: one or two short sentences. They can ask for more.",
    ("question", "normal"): "At most about 50 words: two or three short sentences. They can ask for more.",
    ("question", "detailed"): f"A paragraph or two, up to about 120 words, {_PLAIN}.",
    ("detailed", "short"): f"They asked for more detail. Up to about 80 words, {_PLAIN}. {_MORE}",
    ("detailed", "normal"): f"They asked for more detail. Up to about 150 words: one or two short "
                            f"paragraphs, {_PLAIN}. {_MORE}",
    ("detailed", "detailed"): f"They asked for more detail. Up to about 250 words, {_PLAIN}. {_MORE}",
}


def message_kind(message: str, after_remark: bool = False) -> str:
    """"detailed", "continue", "reply" or "question"."""
    text = (message or "").strip()
    if _DETAILED.search(text):
        return "detailed"
    if _CONTINUE.match(text):
        return "continue"
    if after_remark:
        return "reply"
    if len(text.split()) <= 4 and "?" not in text and _ACKNOWLEDGE.match(text):
        return "reply"
    return "question"


def length_note(message: str, after_remark: bool = False, length: str = "normal") -> str:
    """The [LENGTH] block for this turn, or "" when none applies."""
    length = length if length in LENGTHS else "normal"
    text = _NOTES.get((message_kind(message, after_remark), length), "")
    return f"[LENGTH — {text}]\n\n" if text else ""
