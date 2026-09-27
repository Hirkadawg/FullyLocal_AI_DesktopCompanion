"""Speaking each sentence with a voice for its language.

Reported: a Turkish reply to a TYPED Turkish question was spoken with the
English voice, because only speech recognition ever chose the voice. Now each
sentence's own text chooses: Turkish is told apart from English (and English
naming Turkish places stays English); a sentence that doesn't say keeps the
voice already speaking; recognition still sets the language for a spoken
question; and a language without a voice leaves the voice alone.
"""

import sys
import threading

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.logging import setup_logging
from modules.voice.language import guess_language
from modules.voice.speaker import Speaker
from modules.voice.tts.base import TTSEngine

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("the language of a sentence")

cases = [
    ("Dişliler bronzdan yapılmış.", "tr"),
    ("Bu makale ne hakkında?", "tr"),
    ("Tamam, anladım.", "tr"),
    ("Python kullanarak bir API yazdım.", "tr"),
    # measured: the one real answer sentence a 30% threshold missed
    ("Adam kitabı açtığında, sayfalar kendi kendine hareket etti; sanki okuyucuyu bekliyorlardı.", "tr"),
    ("Göbekli Tepe is in southeastern Türkiye.", "en"),
    ("The mosque in İstanbul was built by Mimar Sinan.", "en"),
    ("I love çay and simit in the morning.", "en"),
    ("The article covers the Perseverance rover.", "en"),
    ("Merhaba!", None),
    ("OK.", None),
    ("这是一个测试。", "zh"),
]
wrong = [(text, guess_language(text), want) for text, want in cases if guess_language(text) != want]
check("Turkish, English naming Turkish places, and too little to tell", not wrong, str(wrong))


class Recording(TTSEngine):
    sample_rate = 22050

    def __init__(self):
        self.voice_name = "en_GB-alba-medium"
        self.spoken = []
        self.done = threading.Event()

    def set_voice(self, voice):
        if voice == "missing-voice":
            return False
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


VOICES = {"en": "en_GB-alba-medium", "tr": "tr_TR-dfki-medium"}


def speak(sentences, voices=VOICES, before=None):
    engine = Recording()
    speaker = Speaker(engine, Quiet(engine, len(sentences)), min_sentence_chars=3, voices_by_language=voices)
    if before:
        speaker.set_language(before)
    for sentence in sentences:
        speaker.feed(sentence + " ")
    speaker.flush()
    engine.done.wait(3)
    speaker.close()
    return [voice for voice, _ in engine.spoken]


print("\nthe voice each sentence is spoken with")

voices = speak(["Antikythera mekanizması eski bir Yunan bilgisayarıdır.",
                "Yıldızların konumlarını tahmin etmek için kullanılmıştır."])
check("a Turkish reply to a typed question is spoken with the Turkish voice -- the reported bug",
      voices == ["tr_TR-dfki-medium"] * 2, str(voices))
voices = speak(["Evet, çok ilginç bir makale.", "Merhaba!", "The gears were bronze, and that is the point."])
check("a sentence that doesn't say keeps the voice speaking; a clear one switches",
      voices == ["tr_TR-dfki-medium", "tr_TR-dfki-medium", "en_GB-alba-medium"], str(voices))
voices = speak(["Göbekli Tepe is older than Stonehenge by thousands of years."])
check("English naming a Turkish place stays English", voices == ["en_GB-alba-medium"], str(voices))
voices = speak(["Merhaba!"], before="tr")
check("a spoken question's language still counts when the text doesn't say", voices == ["tr_TR-dfki-medium"],
      str(voices))
voices = speak(["Dişliler bronzdan yapılmış."], voices={"en": "en_GB-alba-medium"})
check("a language without a voice leaves the voice alone", voices == ["en_GB-alba-medium"], str(voices))
voices = speak(["Dişliler bronzdan yapılmış."], voices={"en": "en_GB-alba-medium", "tr": "missing-voice"})
check("a voice that isn't installed leaves the voice alone", voices == ["en_GB-alba-medium"], str(voices))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
