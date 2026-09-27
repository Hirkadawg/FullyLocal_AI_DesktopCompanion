"""Looking at the screen when asked, and watching it for remarks, with the real
model.

The screen's text is the article, read a moment ago; the screen itself now shows
a bar chart -- the "it got the screen wrong" case. Measured before:
- "look at my screen" / "ekranımı kontrol et" / "look again": 0 of 6 answers
  from the screen, even the two that had a screenshot attached;
- seeing switched off, "can you see my screen?": "yes, I can see it" 5 of 6;
- remarks read only the text, so none could be about the chart.

Measured while building: with the screen's text in the message, a screenshot
attached with any note -- after the question, or the text marked out of date --
was ignored, 0 of 6; with the text left out, 6 of 6 from the screenshot. Remarks
while watching: 2 of 5 with the text, 4 of 5 without. So a request to look, and
a remark while watching, go from the screenshot alone -- and a page of text must
still be understood that way.
"""

import dataclasses
import re
import sys
import tempfile
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, TESTS_DIR, VOICES_DIR  # noqa: F401

sys.path.insert(0, str(TESTS_DIR / "fixtures"))
import make_visual_pages  # noqa: E402

from core.attention import AttentionPolicy  # noqa: E402
from core.companion import build_companion  # noqa: E402
from core.config import AppConfig  # noqa: E402
from core.logging import setup_logging  # noqa: E402
from core.memory import ConversationMemory  # noqa: E402
from core.orchestrator import MOVE_ORDER, Orchestrator, load_persona  # noqa: E402

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


pages = make_visual_pages.make_all(Path(tempfile.mkdtemp(prefix="companion-look-e2e-")))
cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False
cfg.vision.enabled = cfg.vision.watch_remarks = True
reader = build_companion(cfg, image_path=FIXTURE_IMAGE)
ARTICLE_READ = reader.refresh()
STALE = dataclasses.replace(ARTICLE_READ, image=None)
comp = build_companion(cfg, image_path=str(pages["chart"]))
comp.llm.health_check()
CHART = re.compile(r"visitor|chart|\bbars?\b|tuesday|dashboard|graph|ziyaret|grafik|çubuk|salı", re.I)

print("asked to look: the text is the article, the screen now a chart")
looked = 0
for question in ["Look at my screen. What's on it?", "Ekranımı kontrol et, ne var?",
                 "You're wrong, look again. What's on my screen?"] * 2:
    comp.memory.clear()
    reply = comp.ask(question, context=STALE).text()
    looked += bool(CHART.search(reply))
    print(f"    [{'chart' if CHART.search(reply) else 'not chart'}] {question!r}: {reply[:90]!r}")
check(f"answered from the screen ({looked}/6; before 0/6)", looked >= 5)

print("\nasked to look at a page of text: understood from the screenshot alone")
understood = 0
for question in ["Look at my screen. What is it about?", "Ekranıma bak, bu ne hakkında?"] * 2:
    reader.memory.clear()
    reply = reader.ask(question, context=ARTICLE_READ).text()
    understood += bool(re.search(r"antikythera", reply, re.I))
    print(f"    {question!r}: {reply[:100]!r}")
check(f"the article is still named from the screenshot ({understood}/4)", understood >= 3)

print("\nseeing switched off")
cfg.vision.enabled = False
CLAIM = re.compile(r"\bI can see (?:your|the) screen|\bI(?:'m| am) (?:looking at|seeing) your screen|"
                   r"\bgörebiliyorum\b|\bgörüyorum\b", re.I)
claims = 0
for question in ["Can you see my screen?", "ekranımı görebiliyor musun"] * 3:
    comp.memory.clear()
    reply = comp.ask(question, context=STALE).text()
    claims += bool(CLAIM.search(reply))
    print(f"    [{'claims' if CLAIM.search(reply) else 'honest'}] {question!r}: {reply[:100]!r}")
check(f"it doesn't claim to see ({claims}/6 claimed; before 5/6)", claims <= 1)
cfg.vision.enabled = True

print("\nwatch my screen")
# Five times, not once: a single try passed while a prompt edit had broken
# this, which really scored 4-5 in 10 against 9-10. The rate also moves with
# the Ollama version (9-10, then 5-7 of 10 after an update), so the bar is a
# deliberately low 3 of 5.
said = 0
for _ in range(5):
    comp.memory.clear()
    comp.watching = False
    reply = comp.ask("watch my screen", context=STALE).text()
    said += bool(comp.watching and re.search(r"watch", reply, re.I))
    print(f"    {reply[:120]!r}")
check(f"it starts watching, and says so ({said}/5)", said >= 3)
comp.watching = True

persona = load_persona(cfg.root / cfg.proactive.persona_file)


def remarks(watching):
    found = []
    for kind in range(6):
        orch = Orchestrator(
            comp.llm, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=cfg.proactive.min_chars),
            max_words=cfg.proactive.max_words, temperature=cfg.proactive.temperature, min_time_on_page_s=0,
            persona=persona, memory=ConversationMemory(), screenshot=comp.screenshot,
            vision_edge=cfg.vision.max_image_edge, min_picture_colours=cfg.vision.min_picture_colours,
            watching=lambda: watching,
        )
        orch._last_tried = MOVE_ORDER[(kind - 1) % len(MOVE_ORDER)]
        orch.observe(STALE)
        remark = orch.poll()
        if remark is not None:
            found.append(remark.text)
            print(f"    [{'chart' if CHART.search(remark.text) else 'text'}] {remark.text}")
    return found


print("\nremarks, not watching")
blind = remarks(False)
print("\nremarks, watching")
seen = remarks(True)
about_chart = sum(bool(CHART.search(t)) for t in seen)
check(f"watching, remarks come from the screen ({about_chart}/{len(seen)}; not watching "
      f"{sum(bool(CHART.search(t)) for t in blind)}/{len(blind)})", len(seen) >= 4 and about_chart >= len(seen) // 2)

comp.memory.clear()
reply = comp.ask("stop watching", context=STALE).text()
print(f"\n    {reply[:120]!r}")
check("'stop watching' ends it", not comp.watching, reply[:80])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
