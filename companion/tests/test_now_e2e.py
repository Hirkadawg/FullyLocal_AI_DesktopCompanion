"""What is true right now, with the real model -- the two cases reported 15 Sep.

1. Turkish heard a minute ago, then an English question about the English article
   on screen. Measured with the old audio block: Turkish replies 3 times in 5;
   with timed lines and the reply-language note, English 5 of 5.
2. "what song is playing now?" after it named another song, the title not in the
   screen text: the old song 5 of 5 without Windows' now-playing, even with the
   new lyrics marked "just now". With NOW PLAYING it must name the new song --
   and its artist, not the one from the earlier answer.
"""

import sys
import time
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from core.types import ScreenContext
from modules.audio.transcriber import TranscriptLine
from modules.perception.media import MediaSession
from modules.voice.language import guess_language

setup_logging("ERROR")
failures = 0
RUNS = 5


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False
cfg.vision.enabled = False
cfg.perception.now_playing = True
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
article = comp.refresh()


def audio(lines):
    return SimpleNamespace(recent=lambda minutes, max_chars: [
        TranscriptLine(text, time.time() - age, language) for age, language, text in lines])


print("an English question after Turkish audio")
comp.audio = audio([(60, "tr", "Bu mekanizma yıldızların ve gezegenlerin hareketlerini hesaplıyordu."),
                    (75, "tr", "Bugün sizlere Antik Yunan'ın en ilginç buluşlarından birini anlatacağım.")])
languages = []
for _ in range(RUNS):
    comp.memory.clear()
    languages.append(guess_language(comp.ask("What do you think about this article?", context=article).text()) or "?")
check(f"it replies in English ({languages.count('en')}/{RUNS})", languages.count("en") >= RUNS - 1, str(languages))

print("\nwhat song is playing, after naming another")
comp.audio = audio([(5, "en", "Hold on to the blue morning, don't let it slip away."),
                    (20, "en", "Blue morning light is falling on the quiet street."),
                    (200, "tr", "Sevdim seni bir kere, unutamam hiç bir zaman."),
                    (230, "tr", "Akşam olunca sahil boyunca yürümeyi severim.")])
comp.now_playing = lambda: [MediaSession("MusicBox", "Playing", "Blue Morning", "The Lanterns", "Harbour Lights"),
                            MediaSession("Brave", "Paused", "Gece Rüzgarı", "Mavi Ada")]
player = ScreenContext(text="YouTube Music\nHome Explore Library", window_title="YouTube Music - Brave",
                       app_name="brave.exe", source="uia")
right = artist = 0
for _ in range(RUNS):
    comp.memory.clear()
    comp.memory.add_turn("what song is playing?", "It's Gece Rüzgarı by Mavi Ada — a slow Turkish ballad.")
    reply = comp.ask("what song is playing now?", context=player).text()
    head = reply.split(".")[0]
    right += "blue morning" in head.lower() and "Gece Rüzgarı" not in head
    artist += "Lanterns" in reply and "by Mavi Ada" not in head
    print(f"    {reply[:110]!r}")
check(f"it names the song playing now ({right}/{RUNS})", right >= RUNS - 1)
check(f"...with its own artist, not the earlier answer's ({artist}/{RUNS})", artist >= RUNS - 1)

print("\nan ordinary question")
comp.audio = None
asked = []
comp.now_playing = lambda: asked.append(1) or []
reply = comp.ask("What is this article about?", context=article).text()
check("is answered in English without asking Windows what plays",
      guess_language(reply) == "en" and not asked, reply[:80])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
