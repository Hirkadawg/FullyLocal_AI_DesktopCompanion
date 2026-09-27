"""The avatar's mood, decided in code from the message and the reply.

The rules are held to both measured sets: the one they were written on, and the
held-out set they got 11 of 12 on before the choice-question rule. Both are
questions to qwen3.5:4b, with its real replies' openings where they decide it.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.logging import setup_logging
from core.mood import MOODS, mood_of

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


# (message, the opening of the model's real reply, mood)
WRITTEN_ON = [
    ("I just got the job I interviewed for!", "That's incredible news! Congratulations on landing that role", "happy"),
    ("My cat died yesterday.", "I am so incredibly sorry for your loss.", "sad"),
    ("Did you know octopuses have three hearts?", "That's a fascinating fact! Octopuses do indeed have three hearts", "surprised"),
    ("What is 17 times 23?", "17 multiplied by 23 equals 391.", "neutral"),
    ("Can you explain how vaccines work?", "Vaccines work by training your immune system", "neutral"),
    ("I'm so tired of this bug, I've been at it all day.", "That sounds really frustrating.", "sad"),
    ("I finished my first marathon today!", "That's incredible news! Completing a marathon is an absolutely massive achievement.", "happy"),
    ("Why is the sky blue?", "The sky appears blue because of a phenomenon called Rayleigh scattering.", "neutral"),
    ("Guess what, it's snowing here in July!", "That sounds like a very unusual weather event for July!", "surprised"),
    ("Should I learn Rust or Go first?", "The answer often depends on your specific goals!", "thinking"),
    ("Sınavı geçtim!", "Harika bir haber! Sınavı geçmek gerçekten büyük bir başarı.", "happy"),
    ("Bugün çok yorgunum.", "Bunu duyduğuma üzüldüm.", "sad"),
]
HELD_OUT = [
    ("We're having a baby!", "Congratulations! That's wonderful news.", "happy"),
    ("I failed my driving test again.", "I'm sorry, that's frustrating.", "sad"),
    ("Believe it or not, I found a four-leaf clover.", "That's lucky! They're rare.", "surprised"),
    ("How do I boil an egg?", "Place the egg in a pot of cold water.", "neutral"),
    ("Is it better to rent or buy a house?", "It depends on how long you plan to stay.", "thinking"),
    ("My grandmother passed away last week.", "I'm so sorry for your loss.", "sad"),
    ("What's the capital of Australia?", "The capital of Australia is Canberra.", "neutral"),
    ("I got promoted today!", "Congratulations on your promotion!", "happy"),
    ("Which laptop should I buy for programming?", "A good choice depends on your budget.", "thinking"),
    ("Biliyor muydun, balinalar şarkı söyler.", "Evet, erkek balinalar uzun şarkılar söyler.", "surprised"),
    ("Kediyi mi yoksa köpeği mi sahiplenmeliyim?", "İkisi de güzel seçenekler.", "thinking"),
    ("Bu makale ne hakkında?", "Makale Antikythera mekanizmasını anlatıyor.", "neutral"),
]

for label, cases in (("the set the rules were written on", WRITTEN_ON), ("the held-out set", HELD_OUT)):
    wrong = [(m, mood_of(m, r), want) for m, r, want in cases if mood_of(m, r) != want]
    check(f"{label}: {len(cases) - len(wrong)}/{len(cases)}", not wrong, str(wrong))

check("every answer is one of the moods", all(mood_of(m, r) in MOODS for m, r, _ in WRITTEN_ON + HELD_OUT))
check("nothing said: neutral", mood_of("", "") == "neutral")
check("an ordinary question with 'or' in the middle isn't a choice",
      mood_of("Tell me about the war of 1812 or whatever it was called.", "") == "neutral")

print("\nthe worker hands the mood to the avatar")

import inspect  # noqa: E402

from core.config import AppConfig  # noqa: E402
from modules.ui.worker import CompanionWorker  # noqa: E402

worker = CompanionWorker(AppConfig.load(CONFIG_PATH))
emitted = []
worker.mood.connect(emitted.append)
worker._emit_mood("My cat died yesterday.", "I'm so sorry for your loss.")
worker._emit_mood("", "Wow, octopuses really do have three hearts.")
check("the worker decides the mood and emits it", emitted == ["sad", "surprised"], str(emitted))
source = inspect.getsource(CompanionWorker._answer)
check("answers give their mood once enough of the reply has arrived, or at its end",
      source.count("_emit_mood(question") == 2 and "MOOD_AFTER_CHARS" in source)
check("delivered remarks give theirs", "_emit_mood(" in inspect.getsource(CompanionWorker._deliver_remark))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
