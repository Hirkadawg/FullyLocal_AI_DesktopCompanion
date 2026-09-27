"""Showing qwen3.5:4b the screen, with the real model.

On pages rendered around a local photo and on a chart: visual questions get the
screenshot and are answered from it; the text article's ordinary question gets
none; and a photo page, refused for remarks without vision, gets remarks about
the photo over a batch of eight.
"""

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
from core.orchestrator import Orchestrator, load_persona  # noqa: E402

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


pages = make_visual_pages.make_all(Path(tempfile.mkdtemp(prefix="companion-vision-e2e-")))
cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False
cfg.vision.enabled = True
PHOTO_WORDS = re.compile(r"beach|run|jog|rock|ocean|sea\b|shore|wave|coast|cliff|sand|tide|reflect", re.I)


def comp_for(path):
    comp = build_companion(cfg, image_path=str(path))
    comp.llm.health_check()
    comp.refresh()
    return comp


print("questions")

comp = comp_for(pages["chart"])
answer = comp.ask("Which day had the most visitors, and about how many?", context=comp.last_context).text()
print(f"    chart: {answer[:140]!r}")
check("the chart is read from the picture: Tuesday, about 420",
      "tuesday" in answer.lower() and re.search(r"\b4[0-4]\d\b", answer), answer[:80])

comp = comp_for(FIXTURE_IMAGE)
check("the text article's ordinary question gets no screenshot",
      comp._images_for("What is this article about?", comp.last_context) == ())

if "gallery" not in pages:
    print("  (no local photo; photo checks skipped)")
    print(f"\n  {failures} failure(s)")
    sys.exit(1 if failures else 0)

for name, question in (("gallery", "What am I looking at?"), ("video", "What is this video showing?")):
    comp = comp_for(pages[name])
    answer = comp.ask(question, context=comp.last_context).text()
    print(f"    {name}: {answer[:140]!r}")
    check(f"{name}: answered from the photo", bool(PHOTO_WORDS.search(answer)), answer[:80])

print("\nremarks on the photo page, a batch of eight")

comp = comp_for(pages["gallery"])
persona = load_persona(cfg.root / cfg.proactive.persona_file)


def orchestrator(screenshot):
    return Orchestrator(
        comp.llm, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=cfg.proactive.min_chars),
        max_words=cfg.proactive.max_words, memory=ConversationMemory(),
        temperature=cfg.proactive.temperature, min_time_on_page_s=0, persona=persona,
        screenshot=screenshot, vision_edge=cfg.vision.max_image_edge,
        min_picture_colours=cfg.vision.min_picture_colours,
    )


blind = orchestrator(None)
blind.observe(comp.last_context)
check("without vision, the photo page is refused", blind.poll() is None)

made, about_photo = 0, 0
for i in range(8):
    orch = orchestrator(comp.screenshot)
    orch.observe(comp.last_context)
    remark = orch.poll()
    if remark is None:
        print(f"    {i + 1}: (nothing)")
        continue
    made += 1
    about_photo += bool(PHOTO_WORDS.search(remark.text + " " + remark.activity))
    print(f"    {i + 1}: {remark.text}  [now: {remark.activity}]")
check("with vision, remarks are made (at least 6 of 8)", made >= 6, f"{made}/8")
check("...about what the photo shows (at least 6 of 8)", about_photo >= 6, f"{about_photo}/8")

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
