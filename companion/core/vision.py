"""When the model gets to see the screen, not just read it.

Measured on qwen3.5:4b before any of this was built (14 September 2026), on a
text article, a photo gallery, a video page and a bar chart:

- Text only, the image pages were answered wrongly: the gallery was "an app
  called Replay", and the chart's busiest day was "Monday, about 400" when the
  bars said Tuesday, about 420. With a 768 px screenshot all three were right.
- The cost was small: +362 prompt tokens at 768 px (+642 at 1024, which bought
  nothing more), first words unchanged at 0.32 s, VRAM unchanged at 3.34 GB and
  100% on the GPU, 34 ms to downscale and encode a 2560x1440 screen.
- The text article was answered as well and as fast with or without an image.

So an image goes with a question when the question is about something visual,
or when the screen has too little text to answer from -- decided here, in code,
the way tools are offered. Unprompted remarks use one only when the page shows a
picture: a thumbnail's distinct colours were 108-123 for photo and video pages
and 8-12 for text, a chart, two settings screens and the companion's own window.
Nothing here writes an image to disk; the model runs on this machine.
"""

from __future__ import annotations

import io
import re

import numpy as np
from PIL import Image

#: Questions about what something looks like, not what the text says.
_VISUAL = re.compile(
    r"(?:^\W*(?:what(?:'s| is) (?:this|that)|bu ne|şu ne)\W*$)"
    r"|\b(?:what am i (?:looking at|seeing|watching)|what do you see|can you see|"
    r"what(?:'s| is) on (?:the |my )?screen|look(?:ing)? at (?:this|that)|"
    r"who is (?:this|that)|where is this|pictures?|photos?|photographs?|images?|"
    r"pics?|charts?|graphs?|diagrams?|plots?|videos?|clips?|screenshots?|maps?|"
    r"colou?rs?|drawings?|paintings?|memes?|logos?|icons?|"
    r"resim\w*|fotoğraf\w*|görsel\w*|grafi\w*|video\w*|harita\w*|renk\w*|"
    r"ne görüyorsun|neye bakıyorum)\b",
    re.IGNORECASE,
)


def asks_visual(question: str) -> bool:
    """Whether the question is about something seen rather than read."""
    return bool(_VISUAL.search(question or ""))


#: Asking it to look at the screen itself, often right after a wrong answer:
#: "look at my screen", "look again", "ekranıma bak". Measured
#: before: none of these got a screenshot unless worded visually, and with the
#: screen's text a moment stale 0 of 6 were answered from the screen.
_LOOK = re.compile(
    r"\b(?:(?:look|looking|check|see|watch|glance)\s+(?:at\s+)?(?:my|the)\s+screen|"
    r"(?:look|check)\s+again|take\s+(?:a|another)\s+look|have\s+a\s+look|"
    r"ekran\w*\s+(?:bak|gör|izle|kontrol)\w*|(?:tekrar|bir\s+daha)\s+bak\w*|bir\s+bak\b)",
    re.IGNORECASE,
)
#: Asking it to keep watching the screen, and to stop. Stop is checked first:
#: "ekranımı izleme" (don't watch) begins like "ekranımı izle" (watch). A bare
#: "keep watching" isn't a request -- "I'll keep watching this show" -- since
#: starting would put a screenshot behind every remark.
_STOP_WATCHING = re.compile(
    r"\b(?:stop\s+(?:watching|looking\s+at)|"
    r"(?:don'?t|do\s+not|no\s+need\s+to)\s+(?:watch|look\s+at)\s+(?:my|the)\s+screen|"
    r"izlemeyi\s+(?:bırak|kes|durdur)\w*|ekran\w*\s+izleme\b)",
    re.IGNORECASE,
)
_WATCH = re.compile(
    r"\b(?:(?:watch|keep\s+watching|keep\s+an\s+eye\s+on|monitor)\s+(?:my|the)\s+screen|"
    r"ekran\w*\s+(?:izle\b|izler\s+misin|izleyebilir\s+misin|izlemeye\s+devam))",
    re.IGNORECASE,
)


def asks_to_look(question: str) -> bool:
    """Whether the message asks it to look at the screen. "Stop looking at my
    screen" contains a look, and must not take a screenshot."""
    return bool(_LOOK.search(question or "")) and watch_request(question) != "stop"


def watch_request(message: str) -> str | None:
    """"start" or "stop" when the message asks it to keep watching the screen
    or to stop; None otherwise."""
    if _STOP_WATCHING.search(message or ""):
        return "stop"
    if _WATCH.search(message or ""):
        return "start"
    return None


def text_is_thin(text: str, min_chars: int) -> bool:
    """Too little prose on screen to answer from the text alone."""
    from core.orchestrator import MIN_PROSE_SHARE, prose_share

    text = (text or "").strip()
    return len(text) < min_chars or prose_share(text) < MIN_PROSE_SHARE


def wants_image(when: str, question: str, text: str, min_chars: int) -> bool:
    """Whether an answer should come with a screenshot.

    `when`: "never", "always", or "thin_text" -- being asked to look, a visual
    question, or a screen with too little text.
    """
    if when == "never":
        return False
    if when == "always":
        return True
    return asks_to_look(question) or asks_visual(question) or text_is_thin(text, min_chars)


def picture_colours(image: Image.Image) -> int:
    """Distinct colours in a small thumbnail, each channel in 8 levels."""
    thumb = image.convert("RGB")
    thumb.thumbnail((160, 160))
    levels = (np.asarray(thumb) // 32).reshape(-1, 3)
    return int(np.unique(levels, axis=0).shape[0])


def looks_like_picture(image: Image.Image, min_colours: int = 48) -> bool:
    """A photo or a video frame, as opposed to text or an interface."""
    return picture_colours(image) >= min_colours


def encode(image: Image.Image, max_edge: int = 768) -> bytes:
    """Downscaled to `max_edge` on its long side, as JPEG bytes in memory."""
    copy = image.convert("RGB")
    copy.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
