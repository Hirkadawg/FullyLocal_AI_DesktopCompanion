"""A pinned language, with the real model.

Before, pinning a language changed only speech recognition: an English question
got an English answer and remarks stayed in the page's language. With the pin
reaching the prompts, answers and remarks must come in the pinned language -- and
a request for another language must still get that one.

Reported in use after the first version: pinned to English, "benim hakkımda ne
biliyorsun?" and "ekranımı görebiliyor musun" were answered in Turkish. The
article page had measured 5 of 5 English; a screen showing a chat with some
Turkish in it measured 6 of 10 with the note alone. That screen is below, ten
questions, because it is where the difference shows.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from core.orchestrator import MOVE_ORDER, Orchestrator, load_persona
from core.types import ScreenContext
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
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
article = comp.refresh()
PERSONA = load_persona(cfg.root / cfg.proactive.persona_file)


#: The screen in the report: a chat window, mostly English, with some Turkish in it.
CHAT = ScreenContext(text=(
    "Assistant\nThe language pin now sets the spoken voice as well as the written answer.\n"
    "Pinned Turkish, English question | English 5/5 | Turkish 5/5\n"
    "Pinned English, Turkish question | Turkish 5/5 | English 5/5\n"
    "Type an English question, then switch the pin to tr and ask one in Turkish "
    "(Bu makale ne hakkında?).\n"
    "~/projects/app> pytest -q\n  128 passed in 42s"),
    window_title="Assistant", app_name="assistant.exe", source="uia")
TURKISH_PAGE = ScreenContext(text=(
    "Antikythera mekanizması, antik Yunanlıların gökyüzündeki hareketleri hesaplamak için yaptığı bronz bir "
    "düzenektir. 1901 yılında Antikythera adası açıklarındaki bir batıkta süngercilerin bulduğu parçalar, uzun "
    "yıllar boyunca kimsenin anlamlandıramadığı paslı bir yığın olarak müzede bekledi. Röntgen ve bilgisayarlı "
    "tomografi taramaları, içinde en az otuz dişli bulunduğunu ortaya çıkardı. Mekanizma Güneş'in ve Ay'ın "
    "konumlarını, Ay'ın evrelerini ve güneş tutulmalarını önceden gösterebiliyordu. Ayrıca dört yılda bir yapılan "
    "Olimpiyat oyunlarının takvimini de izliyordu. Bilim insanları cihazın MÖ 2. yüzyılın sonu ile MÖ 1. yüzyılın "
    "başı arasında yapıldığını düşünüyor. Benzer karmaşıklıkta bir dişli düzeneği Avrupa'da ancak on dördüncü "
    "yüzyılda, astronomik saatlerle yeniden ortaya çıktı."),
    window_title="Antikythera mekanizması - Vikipedi", app_name="brave.exe", source="uia")


def answers(questions, pinned, runs=RUNS, page=None):
    comp.reply_language = pinned
    found = []
    for i in range(runs):
        comp.memory.clear()
        reply = comp.ask(questions[i % len(questions)] if isinstance(questions, list) else questions,
                         context=page or article).text()
        found.append(guess_language(reply) or "?")
        print(f"    [{found[-1]}] {reply[:90]!r}")
    return found


def remarks(pinned, count=6, page=None):
    found = []
    for kind in range(count):
        orch = Orchestrator(
            comp.llm,
            AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=cfg.proactive.min_chars),
            max_words=cfg.proactive.max_words, temperature=cfg.proactive.temperature,
            min_time_on_page_s=0, persona=PERSONA,
        )
        orch.reply_language = pinned
        orch._last_tried = MOVE_ORDER[(kind - 1) % len(MOVE_ORDER)]
        orch.observe(page or article)
        remark = orch.poll()
        if remark:
            found.append((guess_language(remark.text) or "?", remark.text))
            print(f"    [{found[-1][0]}] {remark.text}")
    return found


print("an English question, on auto (what pinning Turkish used to give)")
auto = answers("What is this article about?", None)
check(f"English ({auto.count('en')}/{RUNS})", auto.count("en") >= RUNS - 1, str(auto))

print("\nthe same question, pinned to Turkish")
pinned = answers("What is this article about?", "tr")
check(f"Turkish ({pinned.count('tr')}/{RUNS})", pinned.count("tr") >= RUNS - 1, str(pinned))

print("\npinned to English, a Turkish question")
english = answers("Bu makale ne hakkında?", "en")
check(f"English ({english.count('en')}/{RUNS})", english.count("en") >= RUNS - 1, str(english))

print("\npinned to English, the reported questions, on a chat screen with some Turkish in it")
reported = answers(["benim hakkımda ne biliyorsun?", "ekranımı görebiliyor musun"], "en", runs=10, page=CHAT)
check(f"English ({reported.count('en')}/10; the note alone measured 6/10)", reported.count("en") >= 9,
      str(reported))

print("\npinned to Turkish, asking for English")
asked = answers("What is this article about? Answer in English.", "tr")
check(f"the language asked for wins ({asked.count('en')}/{RUNS})", asked.count("en") >= RUNS - 1, str(asked))

print("\nremarks on auto")
before = remarks(None)
print("\nremarks pinned to Turkish")
after = remarks("tr")
turkish = sum(language == "tr" for language, _ in after)
check(f"remarks come in Turkish ({turkish}/{len(after)}; on auto "
      f"{sum(language == 'tr' for language, _ in before)}/{len(before)})",
      len(after) >= 4 and turkish >= len(after) - 1)
check("...and stay short", all(len(text.split()) <= cfg.proactive.max_words + 15 for _, text in after),
      str([len(text.split()) for _, text in after]))

print("\nremarks about a Turkish page, pinned to English")
english_remarks = remarks("en", page=TURKISH_PAGE)
in_english = sum(language == "en" for language, _ in english_remarks)
check(f"remarks come in English ({in_english}/{len(english_remarks)})",
      len(english_remarks) >= 4 and in_english >= len(english_remarks) - 1)

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
