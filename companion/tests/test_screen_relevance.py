"""The screen goes to the model only with a message that is about it.

Measured before, on an article and on a chat full of test output, with the
screen on every message: 35 of 48 everyday messages were pulled onto it --
"bugün çok yoruldum" got "Antikythera Mekanismi hakkında bilgi veriyorsun", "what
should I cook tonight?" got "the screen doesn't mention any recipes". After, 0 of
48, with questions about the screen answered as well as before (12 of 14).

Decided in code: pointing words, things on a screen, what they are doing, a
word from the page, "summarise"/"özetle", a Turkish question about something it
doesn't name ("kaç dişlisi var?"), a reply to a remark, a follow-up to an answer
that had the screen. When in doubt, it goes.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PIL import Image

from core.companion import Companion
from core.config import AppConfig, LLMConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.relevance import about_screen
from core.settings import SETTINGS
from core.types import ScreenContext

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


ARTICLE = ScreenContext(text=(
    "Antikythera mechanism\nFrom Wikipedia, the free encyclopedia\n"
    "The Antikythera mechanism is an Ancient Greek hand-wound orrery, described as the oldest known example of "
    "an analogue computer. It was used to predict astronomical positions and eclipses decades in advance, and to "
    "track the four-year cycle of athletic games similar to an Olympiad. The artefact was among wreckage retrieved "
    "from a shipwreck off the coast of the Greek island Antikythera in 1901. On 17 May 1902, it was identified as "
    "containing a gear by archaeologist Valerios Stais. The mechanism was a complex clockwork device composed of "
    "at least 30 meshing bronze gears."), window_title="Antikythera mechanism - Wikipedia - Brave",
    app_name="brave.exe", source="uia")
CHAT = ScreenContext(text=(
    "Assistant\nThe language pin now sets the spoken voice as well as the written answer.\n"
    "Pinned Turkish, English question | English 5/5 | Turkish 5/5\n"
    "All quick suites pass. Next: reading the screen only when asked about.\n"
    "~/projects/app> pytest -q\n  128 passed in 42s"),
    window_title="Assistant", app_name="assistant.exe", source="uia")

EVERYDAY = ["hi", "how are you?", "good morning!", "tell me a joke", "I'm tired today",
            "what should I cook tonight?", "what's the capital of Japan?", "do you like music?",
            "set a timer for 5 minutes", "what do you know about me?", "who are you?", "thanks!",
            "can you help me with my homework?", "merhaba", "nasılsın?", "bugün çok yoruldum",
            "Japonya'nın başkenti neresi?", "benim hakkımda ne biliyorsun?", "sen kimsin?", "bana bir şaka anlat",
            "akşam ne pişireyim?", "canım sıkılıyor", "ne düşünüyorsun?", "müzik sever misin?", "teşekkürler",
            "iyi geceler", "kedimin adı ne olsun?"]
ABOUT_ARTICLE = ["What is this article about?", "When was it found?", "Who identified the gear?", "how many gears?",
                 "What am I reading?", "summarize", "who is Valerios Stais?", "what's an Olympiad?",
                 "why was it important?", "what does orrery mean?", "explain the second paragraph",
                 "translate the first sentence", "what's on my screen?", "can you see my screen?",
                 "what's happening here", "bronze gears huh", "Bu makale ne anlatıyor?", "kaç dişlisi var?",
                 "ne zaman bulunmuş?", "orrery ne demek?", "kim bulmuş?", "nerede bulundu?", "neden önemli?",
                 "Stais kim?", "özetler misin?", "ekranda ne var?"]

print("which messages are about the screen")

for name, page in (("article", ARTICLE), ("chat", CHAT)):
    sent = [m for m in EVERYDAY if about_screen(m, page.text, page.window_title)]
    check(f"everyday messages, English and Turkish, go without the {name} ({len(EVERYDAY) - len(sent)}/{len(EVERYDAY)})",
          not sent, str(sent))
kept = [m for m in ABOUT_ARTICLE if not about_screen(m, ARTICLE.text, ARTICLE.window_title)]
check(f"questions about the article keep it ({len(ABOUT_ARTICLE) - len(kept)}/{len(ABOUT_ARTICLE)})", not kept, str(kept))
check("a word from the page is enough; a named other thing isn't",
      about_screen("tell me about Stais", ARTICLE.text, ARTICLE.window_title)
      and not about_screen("tell me about Tokyo", ARTICLE.text, ARTICLE.window_title))
check("Turkish: an unnamed subject goes with it, a named owner or a personal question doesn't",
      about_screen("kaç parça var?", ARTICLE.text) and not about_screen("Japonya'nın nüfusu kaç?", ARTICLE.text)
      and not about_screen("kaç yaşındasın?", ARTICLE.text))
check("a reply to a remark keeps the screen the remark was about",
      about_screen("which one?", ARTICLE.text, after_remark=True) and about_screen("hayır", ARTICLE.text, after_remark=True))
check("'why?' and 'go on' follow the last answer: with the screen if it had it, without if not",
      about_screen("why?", ARTICLE.text, screen_last_turn=True) and about_screen("go on", CHAT.text, screen_last_turn=True)
      and not about_screen("why?", ARTICLE.text, screen_last_turn=False))


class Capturing:
    def __init__(self):
        self.seen = []

    def chat(self, messages, images=None, tools=None, stream=False, collect_tool_calls=None, **kwargs):
        self.seen.append(messages)
        yield "OK."


class Screen:
    is_live = False

    def grab(self):
        return Image.new("RGB", (80, 50), (20, 90, 160))


def companion(relevant=True):
    comp = Companion.__new__(Companion)
    comp.config = AppConfig.load(CONFIG_PATH)
    comp.config.llm.screen_only_when_relevant = relevant
    comp.config.vision.enabled = True
    comp.config.ratings.remember_moments = False
    comp.tools, comp.memory, comp.audio = None, ConversationMemory(), None
    comp.screen, comp.llm = Screen(), Capturing()
    return comp


def sent(comp, message, page):
    comp.ask(message, context=page).text()
    last = comp.llm.seen[-1][-1]
    return last.content, last.images


print("\nin the app")

comp = companion()
content, _ = sent(comp, "how are you?", ARTICLE)
check("'how are you?' goes without the screen's text, told it isn't about the screen, with the window's title",
      "orrery" not in content and "[SCREEN TEXT not sent" in content and "Window title: Antikythera" in content,
      content[:200])
check("...and the next 'why?' follows it: still without", "orrery" not in sent(comp, "why?", ARTICLE)[0])
content, _ = sent(comp, "When was it found?", ARTICLE)
check("'When was it found?' goes with it", "[SCREEN TEXT]" in content and "orrery" in content)
check("...and the next 'why?' follows it: with", "orrery" in sent(comp, "why?", ARTICLE)[0])

TURKISH_PAGE = ScreenContext(text="Antikythera mekanizması, antik Yunanlıların gökyüzündeki hareketleri hesaplamak "
                                  "için yaptığı bronz bir düzenektir. " * 10, window_title="Vikipedi", source="uia")
content, _ = sent(companion(), "tell me a joke", TURKISH_PAGE)
check("a screen not sent can't ask for a reply-language note: its language is beside the point",
      "[REPLY LANGUAGE" not in content, content[:300])

THIN = ScreenContext(text="Photos", window_title="Photos", app_name="photos.exe", source="uia",
                     image=Image.new("RGB", (80, 50), (200, 30, 30)))
_, images = sent(companion(), "hi", THIN)
check("a greeting on a picture sends no screenshot", not images)
_, images = sent(companion(), "who is the man with the hat?", THIN)
check("a question on a picture does (nothing to match its words against)", bool(images))
_, images = sent(companion(relevant=False), "hi", THIN)
check("switched off, as before: a screenshot with every message on a picture", bool(images))
content, _ = sent(companion(relevant=False), "how are you?", ARTICLE)
check("switched off, every message gets the screen's text", "[SCREEN TEXT]" in content and "orrery" in content)
comp = companion()
content, images = sent(comp, "look at my screen", CHAT)
check("asked to look, a screenshot and no text, as before", bool(images) and "[SCREEN TEXT left out" in content)

print("\nthe setting")
check("off in code, on in config.yaml, on the settings page",
      LLMConfig().screen_only_when_relevant is False
      and AppConfig.load(CONFIG_PATH).llm.screen_only_when_relevant is True
      and any(s.key == "llm.screen_only_when_relevant" and s.kind == "bool" for s in SETTINGS))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
