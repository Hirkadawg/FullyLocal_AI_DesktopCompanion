"""Whether a message is about the screen, decided in code.

Every message used to go to the model with up to 12,000 characters of whatever
was on screen. Measured on two busy screens -- an article, and a chat full of
test output -- 35 of 48 everyday messages were pulled onto it: "bugün çok
yoruldum" got "Antikythera Mekanismi hakkında bilgi veriyorsun", "what should I
cook tonight?" got "the screen doesn't mention any recipes", "tell me a joke"
got "the screen shows your test suites passed". Irrelevant context is known to
distract small models (Shi et al., 2023).

So the screen's text goes only with a message that is about it. When in doubt it
goes: sending it is how the app always worked, and a question about the screen
answered without it is worse than a greeting answered with it.
"""

from __future__ import annotations

import re

from core.orchestrator import _STOPWORDS, _content_words

#: Pointing at something: "this", "it", "he", "bunu", "burada".
_POINTING = re.compile(
    r"\b(?:this|that|these|those|it|its|here|he|she|they|him|her|them|his|their|"
    r"bu|şu|bunu|şunu|bunun|şunun|bunlar\w*|şunlar\w*|burada|burası|buradaki|orada|oradaki)\b",
    re.IGNORECASE,
)
#: Things that are on a screen, in English and Turkish (with Turkish endings).
_SCREEN_THINGS = re.compile(
    r"\b(?:screens?|pages?|articles?|videos?|streams?|posts?|tweets?|threads?|comments?|texts?|"
    r"paragraphs?|sentences?|words?|images?|pictures?|photos?|charts?|graphs?|tables?|code|errors?|"
    r"messages?|e-?mails?|documents?|docs?|files?|tabs?|windows?|sites?|websites?|apps?|games?|"
    r"songs?|tracks?|playlists?|lyrics|titles?|headlines?|links?|buttons?|menus?|lists?|maps?|slides?|"
    r"results?|scores?|chat|conversation|"
    r"ekran\w*|sayfa\w*|makale\w*|video\w*|yayın\w*|gönderi\w*|yorum\w*|yazı\w*|metin\w*|paragraf\w*|"
    r"cümle\w*|kelime\w*|resim\w*|fotoğraf\w*|görsel\w*|grafi\w*|tablo\w*|kod\w*|hata\w*|mesaj\w*|"
    r"belge\w*|dosya\w*|sekme\w*|pencere\w*|site\w*|uygulama\w*|oyun\w*|şarkı\w*|başlık\w*|"
    r"bağlantı\w*|liste\w*|sonuç\w*|skor\w*)\b",
    re.IGNORECASE,
)
#: Asking what they are doing, or what is happening.
_DOING = re.compile(
    r"\bwhat(?:'s| is| am| are)? (?:i|we|you)? ?(?:doing|reading|watching|looking at|playing|listening to|"
    r"seeing|working on)|what(?:'s| is) (?:on|in|happening|going on)|"
    r"ne (?:yapıyorum|okuyorum|izliyorum|dinliyorum|oynuyorum|oluyor)|neye bakıyorum|neler oluyor",
    re.IGNORECASE,
)
#: Doing something with what is shown: summarise it, explain it, translate it.
_WORK_ON = re.compile(
    r"\b(?:summar\w*|explain\w*|translat\w*|read|define|meaning|means|mean|"
    r"özetle\w*|açıkla\w*|çevir\w*|oku\w*|anlam\w*|ne demek)\b",
    re.IGNORECASE,
)
#: Words too common to tie a message to a screen.
_COMMON = frozenset("""
what when where which would could should about there their think know want need like just really
good great nice today tonight tomorrow yesterday morning evening night time thanks thank please
make made much many some something anything everything someone people thing things going doing
feel feeling tired hello hey okay sure yeah maybe actually right well also even still very
merhaba nasılsın teşekkürler lütfen bugün yarın şimdi biraz çok neden nasıl
""".split())


#: A Turkish question: a question word, or the question particle.
_TR_QUESTION = re.compile(r"\b(?:kaç\w*|ne|neden|niye|nasıl|nerede\w*|nereden|nereye|kim\w*|hangi\w*|"
                          r"m[ıiuü](?:d[ıiuü]r)?)\b", re.IGNORECASE)
#: About the speaker or the listener: "ben", "bana", "yoruldum", "biliyorsun",
#: "sever misin", "kedimin".
_TR_PERSONAL = re.compile(r"\b(?:ben\w*|sen\w*|bana|sana|biz\w*|siz\w*)\b|"
                          r"\bm[ıiuü](?:s[ıiuü]n|y[ıiuü]m|y[ıiuü]z|s[ıiuü]n[ıiuü]z)\b|\w+[ıiuü]m[ıiuü]n\b|"
                          r"\w{3,}(?:[ıiuü]m|[dt][ıiuü]m|s[ıiuü]n|[ıiuü]z|s[ıiuü]n[ıiuü]z|"
                          r"yorum|yorsun|yoruz|abilir miyim)\b", re.IGNORECASE)
#: A named owner: "Japonya'nın başkenti", "kedinin adı".
_TR_OWNER = re.compile(r"\w+'?n[ıiuü]n\b", re.IGNORECASE)
_TURKISH_LETTERS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")


def _unnamed_turkish_subject(text: str) -> bool:
    """A Turkish question about something it doesn't name -- Turkish drops the
    pronoun, so "kaç dişlisi var?" is "how many gears does IT have?"."""
    if not (_TURKISH_LETTERS.search(text) or _TR_QUESTION.search(text)):
        return False
    if not ("?" in text or _TR_QUESTION.search(text)):
        return False
    without_question_words = _TR_QUESTION.sub(" ", text)
    return not _TR_PERSONAL.search(without_question_words) and not _TR_OWNER.search(text)


def subject_words(message: str) -> set[str]:
    """Words of three letters or more that could name something: "man", "hat"."""
    words = {"".join(ch for ch in raw if ch.isalpha()) for raw in (message or "").lower().split()}
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS and w not in _COMMON}


def own_words(message: str) -> set[str]:
    """The message's words that could name something."""
    return {word for word in _content_words(message or "") if word not in _COMMON}


def about_screen(message: str, screen_text: str = "", title: str = "",
                 after_remark: bool = False, screen_last_turn: bool = False) -> bool:
    """Whether this message needs what is on screen."""
    text = message or ""
    if after_remark:
        # A reply to a remark, and the remark was about the screen: "which one?"
        return True
    if _POINTING.search(text) or _SCREEN_THINGS.search(text) or _DOING.search(text) or _WORK_ON.search(text):
        return True
    if _unnamed_turkish_subject(text):
        return True
    words = own_words(text)
    if not words:
        # "why?", "go on", "more": about whatever the last answer was about.
        return screen_last_turn
    shown = _content_words(f"{title} {screen_text}")
    return bool(words & shown)
