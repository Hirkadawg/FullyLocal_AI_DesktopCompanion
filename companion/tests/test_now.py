"""What is true right now: timed audio, the reply's language, and the song playing.

Reported 15 Sep: a minute after Turkish audio, an English question got a Turkish
reply; and "what song is playing?" named the song from before. Recent audio is
now newest first with each line's age and language; a reply-language note is
added only when the audio or screen is in another language (and never for a
translation request); a question about now is told that fresh observations
outrank what was said; and a question about music gets the exact track from
Windows' media controls.
"""

import json
import sys
import time
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

import core.companion as companion_module
from core.companion import Companion
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.types import ScreenContext
from modules.audio.transcriber import AudioTranscriber, TranscriptLine
from modules.perception import media

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("recent audio, timed")

now = 10_000.0
lines = [TranscriptLine("Hold on to the blue morning.", now - 5, "en"),
         TranscriptLine("Akşam olunca sahil boyunca yürürüm.", now - 200, "tr")]
block = companion_module._audio_block(lines, now=now)
newest, oldest = block.find("(just now, en)"), block.find("(3 min ago, tr)")
check("newest first, each line with its age and language",
      0 <= newest < oldest and "NEWEST FIRST" in block, block)
prompt = AppConfig.load(CONFIG_PATH).system_prompt
check("...and the system prompt describes it that way, not as oldest first",
      "newest first" in prompt and "oldest first" not in prompt)
check("ages read naturally", [companion_module._age(s) for s in (5, 45, 61, 200)]
      == ["just now", "45 s ago", "1 min ago", "3 min ago"])


class FakeCapture:
    def __init__(self):
        self.audio = np.full(16000 * 2, 0.1, dtype=np.float32)

    def drain(self, max_seconds):
        return self.audio


class FakeSTT:
    sample_rate = 16000

    def __init__(self, text, language):
        self.text, self.last_language = text, language

    def transcribe(self, audio):
        return self.text


transcriber = AudioTranscriber(FakeCapture(), FakeSTT("Merhaba, bugün fotosentezi konuşacağız.", "tr"))
transcriber._process_chunk()
check("each transcript line keeps the language it was heard in",
      transcriber._lines[-1].language == "tr", str(transcriber._lines[-1]))
clock = time.time()
transcriber._lines.clear()
for age, text in ((400, "old"), (90, "a minute and a half"), (10, "newest")):
    transcriber._lines.append(TranscriptLine(text, clock - age, "en"))
recent = transcriber.recent(minutes=2)
check("recent() gives the last minutes, newest first", [l.text for l in recent] == ["newest", "a minute and a half"],
      str([l.text for l in recent]))
check("...within a character budget that keeps the newest", [l.text for l in transcriber.recent(minutes=10, max_chars=8)]
      == ["newest"])
check("audio sent with a question is 2 minutes by default", AppConfig.load(CONFIG_PATH).audio.context_minutes == 2.0)

print("\nthe reply's language")

english_screen = "The Antikythera mechanism is an ancient Greek device used to predict eclipses."
turkish_lines = [TranscriptLine("Bu mekanizma yıldızların hareketlerini hesaplıyordu.", now - 60, "tr")]
note = companion_module._reply_language_block("What do you think about this article?", turkish_lines, english_screen)
check("an English question with Turkish audio is told to reply in English -- the reported case",
      "reply in English" in note, note)
check("no note when everything is in the question's language",
      companion_module._reply_language_block("What do you think about this article?",
                                             [TranscriptLine("the gears were bronze", now, "en")], english_screen) == "")
check("a Turkish question about an English page is told Turkish",
      "reply in Turkish" in companion_module._reply_language_block("Bu makale ne hakkında?", [], english_screen))
check("a translation request gets no note: the language is theirs to name",
      companion_module._reply_language_block("Translate what they said into English please", turkish_lines, english_screen) == ""
      and companion_module._reply_language_block("Now say that in German.", turkish_lines, english_screen) == "")
check("a message too short to tell gets no note", companion_module._reply_language_block("ok", turkish_lines, english_screen) == "")

print("\nquestions about now")

check("a question about now is told fresh observations outrank what was said",
      "outranks anything said earlier" in companion_module._now_block("what song is playing now?"))
check("...other questions aren't", companion_module._now_block("Why is the sky blue?") == "")

print("\nthe song playing, from Windows")

raw = json.dumps([{"app": "brave.exe", "status": "Paused", "title": "Slow Tide", "artist": "Harbour Row", "album": "Blue Morning"},
                  {"app": "musicbox.exe", "status": "Playing", "title": "Night Bus", "artist": "The Lanterns", "album": ""},
                  {"app": "x.exe", "status": "Playing", "title": "  ", "artist": ""}])
sessions = media.parse_sessions(raw)
check("sessions are read, with untitled ones skipped", [s.title for s in sessions] == ["Slow Tide", "Night Bus"])
check("one session (PowerShell unwraps it) and garbage both read safely",
      len(media.parse_sessions(json.dumps({"app": "a.exe", "status": "Playing", "title": "T"}))) == 1
      and media.parse_sessions("not json") == [])
check("app names read plainly", [media.app_name(a) for a in ("brave.exe", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "")]
      == ["Brave", "Spotify", "an app"])
described = media.describe(sessions)
check("the playing track comes first, with artist and app; the paused one after",
      described.index('Playing: "Night Bus" by The Lanterns (Musicbox)') < described.index('Paused: "Slow Tide" by Harbour Row, from Blue Morning (Brave)'),
      described)
check("nothing reported is said plainly", "no app reports a track" in media.describe([]))
started = time.perf_counter()
live = media.now_playing()
elapsed = time.perf_counter() - started
print(f"    Windows reports now ({elapsed * 1000:.0f} ms): {[(s.status, s.title, s.artist, s.app) for s in live]}")
check("asking Windows itself works and returns in time, whatever is playing",
      isinstance(live, list) and elapsed < 3.5)

print("\nin a turn's message")

comp = Companion.__new__(Companion)
comp.config = AppConfig.load(CONFIG_PATH)
comp.config.perception.now_playing = True
comp.tools, comp.memory = None, ConversationMemory()
comp.audio = SimpleNamespace(recent=lambda minutes, max_chars: [
    TranscriptLine("Hold on to the blue morning.", time.time() - 5, "en"),
    TranscriptLine("Akşam olunca sahil boyunca yürürüm.", time.time() - 100, "tr")])
asked = []
comp.now_playing = lambda: asked.append(1) or [media.MediaSession("MusicBox", "Playing", "Blue Morning", "The Lanterns")]
PLAYER = ScreenContext(text="YouTube Music\nHome Explore Library", window_title="YouTube Music - Brave",
                       app_name="brave.exe", source="uia")
content = comp.build_messages("what song is playing now?", PLAYER)[-1].content
check("a music question carries the exact track, the timed audio and the 'now' note",
      '"Blue Morning" by The Lanterns' in content and "(just now, en)" in content
      and "outranks anything said earlier" in content and asked == [1])
content = comp.build_messages("Why is the sky blue?", PLAYER)[-1].content
check("an ordinary question doesn't ask Windows or carry the track", asked == [1] and "NOW PLAYING" not in content)
comp.config.perception.now_playing = False
content = comp.build_messages("what song is playing now?", PLAYER)[-1].content
check("switched off, it isn't asked", asked == [1] and "NOW PLAYING" not in content)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
