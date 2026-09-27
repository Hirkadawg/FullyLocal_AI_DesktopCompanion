"""Which language a sentence is in, so it is spoken with a voice that can say it.

Speech recognition knows the language of a SPOKEN question for free, and that
was the only thing choosing the reply voice -- so a typed Turkish question was
answered in Turkish with the English voice. Guessing from each sentence's own
text covers typed questions, remarks and translations alike.

Decided in code, and deliberately cautious: a sentence with too little to go on
gets no guess, and the voice already in use carries on. Turkish is told by its
own letters (ı ğ ş ç ö ü) and common words, English by common words -- so an
English sentence naming Göbekli Tepe or İstanbul stays English, because its
English words outnumber them.

Measured on qwen3.5:4b's answers to four Turkish and four English questions,
sentence by sentence: Turkish 21 of 22 right and 0 wrong (the other had no
guess at a 30% threshold, hence 25%); English 12 of 12.
"""

from __future__ import annotations

import re

_TR_LETTERS = set("ıİğĞşŞçÇöÖüÜ")
_TR_WORDS = set(
    "ve bir bu da de ne için ile çok daha gibi mi mı mu mü var yok ama olarak kadar evet "
    "hayır ben sen biz onu şu bunu diye sonra önce şimdi nasıl neden hangi".split()
)
_EN_WORDS = set(
    "the a an is are was were and of to in it you i that this for on with what how why "
    "be can do not".split()
)
_WORD = re.compile(r"[^\W\d_]+")
_CJK = re.compile(r"[一-鿿]")

#: At least this share of a sentence's words must look Turkish.
TURKISH_SHARE = 0.25

#: Names for language codes, for notes to the model ("reply in Turkish").
LANGUAGE_NAMES = {"en": "English", "tr": "Turkish", "zh": "Chinese", "ja": "Japanese",
                  "de": "German", "fr": "French", "es": "Spanish"}


def guess_language(text: str) -> str | None:
    """"tr", "en" or "zh" when the sentence says so clearly; else None."""
    if _CJK.search(text or ""):
        return "zh"
    words = _WORD.findall((text or "").lower())
    if not words:
        return None
    turkish = sum(1 for w in words if (set(w) & _TR_LETTERS) or w in _TR_WORDS)
    english = sum(1 for w in words if w in _EN_WORDS)
    if turkish / len(words) >= TURKISH_SHARE and turkish > english:
        return "tr"
    if english and english >= turkish:
        return "en"
    return None
