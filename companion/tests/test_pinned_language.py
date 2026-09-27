"""A language pinned with the lang button: voice, answers and remarks.

Asked for: choosing a language by hand instead of auto should choose the
companion's spoken language too, and it should reply in that language. Before,
the button only told speech recognition what the user speaks. Now the one choice
reaches recognition, the voice (every utterance starts in it; a sentence clearly
in another language still gets its own voice), every answer (a reply-language
note, except for a translation request) and remarks. speech.language in
config.yaml starts it, and the button shows it.
"""

import os
import sys
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtWidgets import QApplication

import core.companion as companion_module
from core.attention import AttentionPolicy
from core.companion import Companion
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.observer import Activity
from core.orchestrator import MOVE_ORDER, Orchestrator
from core.types import ScreenContext
from modules.ui.app import CompanionApp
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker
from modules.voice.speaker import Speaker
from modules.voice.tts.base import TTSEngine

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


VOICES = {"en": "en_GB-alba-medium", "tr": "tr_TR-dfki-medium"}
EN, TR = VOICES["en"], VOICES["tr"]


class Recording(TTSEngine):
    sample_rate = 22050

    def __init__(self):
        self.voice_name = EN
        self.spoken = []
        self.done = threading.Event()

    def set_voice(self, voice):
        self.voice_name = voice
        return True

    def synthesize(self, text):
        self.spoken.append((self.voice_name, text))
        return np.ones(50, dtype=np.int16)


class Quiet:
    sample_rate = 22050
    is_playing = False
    progress = (None, None)

    def __init__(self, engine, count):
        self.engine, self.count = engine, count

    def enqueue(self, samples, tag=None, **kwargs):
        if len(self.engine.spoken) >= self.count:
            self.engine.done.set()

    def stop(self):
        pass

    def close(self):
        pass


def speak(utterances, pins=(None,), voices=VOICES):
    """Voices used for each sentence; each utterance begun as the worker begins an answer."""
    engine = Recording()
    speaker = Speaker(engine, Quiet(engine, sum(len(u) for u in utterances)),
                      min_sentence_chars=3, voices_by_language=voices)
    for pin in pins:
        speaker.pin_language(pin)
    for sentences in utterances:
        speaker.begin_utterance()
        for sentence in sentences:
            speaker.feed(sentence + " ")
        speaker.flush()
    engine.done.wait(3)
    speaker.close()
    return [voice for voice, _ in engine.spoken]


ENGLISH = "The gears were bronze, and that is the point."
UNCLEAR = "OK, sure."

print("the voice")

check("pinned Turkish, a reply too short to tell is spoken in Turkish",
      speak([[UNCLEAR]], pins=("tr",)) == [TR], str(speak([[UNCLEAR]], pins=("tr",))))
check("...where auto left the English voice on -- the gap asked about", speak([[UNCLEAR]]) == [EN])
voices = speak([[ENGLISH], [UNCLEAR]], pins=("tr",))
check("each answer starts in the pinned voice again, after one that ended in English",
      voices == [EN, TR], str(voices))
check("...and a sentence clearly in another language keeps its own voice", voices[0] == EN)
check("back on auto, the voice follows the text alone", speak([[UNCLEAR]], pins=("tr", None)) == [EN])
check("a pinned language with no voice installed leaves the voice to the text",
      speak([[UNCLEAR]], pins=("de",)) == [EN])
check("the code's case doesn't matter", speak([[UNCLEAR]], pins=("TR",)) == [TR])

print("\nthe answer's language")

english_screen = "The Antikythera mechanism is an ancient Greek device used to predict eclipses."
block = companion_module._reply_language_block
check("pinned Turkish, an English question about an English page is told Turkish",
      "reply in Turkish" in block("What is this article about?", [], english_screen, "tr"))
check("...where auto says nothing, so the reply was English",
      block("What is this article about?", [], english_screen) == "")
conflict = block("Bu makale ne hakkında?", [], english_screen, "en")
check("pinned English, a Turkish question is told English -- naming the conflict, which measurably mattered",
      "they wrote in Turkish, but" in conflict and "reply in English" in conflict, conflict)
check("a request for another language still wins over the pin",
      block("Say that in English please.", [], english_screen, "tr") == ""
      and block("Bunu İngilizceye çevir.", [], english_screen, "en") == "")

comp = Companion.__new__(Companion)
comp.config = AppConfig.load(CONFIG_PATH)
comp.tools, comp.memory, comp.audio = None, ConversationMemory(), None
PAGE = ScreenContext(text=english_screen, window_title="Antikythera mechanism - Wikipedia",
                     app_name="brave.exe", source="uia")
content = comp.build_messages("What is this article about?", PAGE)[-1].content
check("an answer on auto carries no language note", "REPLY LANGUAGE" not in content)
comp.reply_language = "tr"
content = comp.build_messages("What is this article about?", PAGE)[-1].content
check("pinned, every answer's message carries it", "reply in Turkish" in content, content[-300:])
check("...and says it again after the question, the last thing read -- the note alone failed in use",
      content.endswith("Question: What is this article about?\n\n(Write your reply in Turkish.)"), content[-120:])
content = comp.build_messages("Say that in English please.", PAGE)[-1].content
check("...but not when the message asks for a language", "Write your reply in" not in content
      and "REPLY LANGUAGE" not in content)
comp.reply_language = None
content = comp.build_messages("What is this article about?", PAGE)[-1].content
check("...and never on auto", content.endswith("Question: What is this article about?"), content[-80:])
comp.reply_language = "tr"

print("\nremarks")

orch = Orchestrator(SimpleNamespace(), AttentionPolicy())
activity = Activity(summary="reading about Greek astronomy")
prompt = orch.build_prompt(activity, english_screen, dwell=30, revisit=False, move=MOVE_ORDER[0])
check("on auto, a remark's prompt names no language", "in Turkish" not in prompt[-1].content)
orch.reply_language = "tr"
prompt = orch.build_prompt(activity, english_screen, dwell=30, revisit=False, move=MOVE_ORDER[0])
check("pinned, a remark is asked for in that language", "words, in Turkish." in prompt[-1].content,
      prompt[-1].content[-200:])

print("\none choice reaches everything")

cfg = AppConfig.load(CONFIG_PATH)
check("auto by default", CompanionWorker(cfg)._language is None)
cfg.speech.language = "TR"
check("speech.language in config.yaml starts it pinned", CompanionWorker(cfg)._language == "tr")
cfg.speech.language = None
worker = CompanionWorker(cfg)
worker.set_speech_language("tr")
check("chosen before the worker has started, it is kept for when it has", worker._language == "tr")
heard = []
worker._stt = SimpleNamespace(set_language=lambda language: heard.append(("stt", language)))
worker._speaker = SimpleNamespace(pin_language=lambda language: heard.append(("voice", language)))
worker._companion = SimpleNamespace(reply_language=None)
worker._orchestrator = SimpleNamespace(reply_language=None)
worker.set_speech_language("TR")
check("the button's choice reaches recognition, the voice, answers and remarks",
      heard == [("stt", "tr"), ("voice", "tr")] and worker._companion.reply_language == "tr"
      and worker._orchestrator.reply_language == "tr", str(heard))
heard.clear()
worker.set_speech_language(None)
check("...and auto takes it back from all of them",
      heard == [("stt", None), ("voice", None)] and worker._companion.reply_language is None
      and worker._orchestrator.reply_language is None)

qt = QApplication.instance() or QApplication(sys.argv)
cfg = AppConfig.load(CONFIG_PATH)
cfg.speech.enabled, cfg.speech.languages = True, ["en", "tr"]
check("the button shows auto by default", ChatWindow(cfg).language_button.accessibleName() == "lang: auto")
cfg.speech.language = "tr"
window = ChatWindow(cfg)
emitted = []
window.language_changed.connect(emitted.append)
check("...and a language pinned in config.yaml", window.language_button.accessibleName() == "lang: tr"
      and window.language_button.text() == "tr")
window._cycle_language()
check("clicking on from it goes back to auto", emitted == [""] and window.language_button.accessibleName() == "lang: auto",
      str(emitted))

notices = []
fake = SimpleNamespace(worker=SimpleNamespace(set_speech_language=lambda language: None),
                       window=SimpleNamespace(add_notice=lambda text, colour="": notices.append(text)))
CompanionApp._on_language_changed(fake, "tr")
check("the window says what a pinned language now does",
      notices and "replies" in notices[-1] and "voice" in notices[-1], str(notices))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
