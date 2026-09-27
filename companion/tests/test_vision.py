"""Showing the model the screen, with fakes: no model, no screen.

When an answer gets a screenshot (a visual question, or too little text), how a
picture is told from an interface, that the image travels on the current turn
only, that the privacy list stops a screenshot before any pixel is read, and
that remarks use a picture on a text-poor page while an interface is still
refused without a model call.
"""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, TESTS_DIR, VOICES_DIR  # noqa: F401

import numpy as np
from PIL import Image

sys.path.insert(0, str(TESTS_DIR / "fixtures"))
import make_visual_pages  # noqa: E402

from core.attention import AttentionPolicy  # noqa: E402
from core.companion import Companion  # noqa: E402
from core.config import AppConfig  # noqa: E402
from core.errors import PrivacyBlocked  # noqa: E402
from core.logging import setup_logging  # noqa: E402
from core.memory import ConversationMemory  # noqa: E402
from core.observer import DESCRIBE  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402
from core.types import Message, ScreenContext  # noqa: E402
from core.vision import asks_visual, encode, looks_like_picture, picture_colours, wants_image  # noqa: E402
from modules.llm.ollama_client import OllamaLLM, _to_payload  # noqa: E402

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("which questions are about something seen")

for question in ("What's this?", "What am I looking at?", "Describe this picture.",
                 "Which day is highest on the chart?", "What is happening in this video?",
                 "What colour is the car?", "Bu resimde ne var?", "Videoda ne oluyor?", "Bu ne?"):
    check(f"visual: {question!r}", asks_visual(question))
for question in ("What is this article about?", "Summarize the key points.", "Yes.",
                 "Note that the gears were bronze.", "Bu makale ne hakkında?", "What time is it?"):
    check(f"not visual: {question!r}", not asks_visual(question))

ARTICLE_TEXT = ("The Antikythera mechanism is an Ancient Greek hand-powered orrery, "
                "described as the oldest known example of an analogue computer.\n") * 8
THIN = "Home Explore Upload\nIMG_2041.jpg · 4.2 MB · Added yesterday"
check("a visual question gets an image, however much text",
      wants_image("thin_text", "What's in this photo?", ARTICLE_TEXT, 400))
check("so does a question about a screen with little text",
      wants_image("thin_text", "What is this about?", THIN, 400))
check("an ordinary question about an article doesn't",
      not wants_image("thin_text", "What is this article about?", ARTICLE_TEXT, 400))
check("'never' never, 'always' always",
      not wants_image("never", "What's in this photo?", THIN, 400)
      and wants_image("always", "Summarize it.", ARTICLE_TEXT, 400))

print("\ntelling a picture from an interface")

pages = make_visual_pages.make_all(Path(tempfile.mkdtemp(prefix="companion-vision-")))
article = Image.open(FIXTURE_IMAGE).convert("RGB")
chart = Image.open(pages["chart"]).convert("RGB")
rng = np.random.default_rng(3)
gradient = np.linspace(0, 255, 400)
photo_like = np.stack([
    np.clip(np.add.outer(gradient, gradient[::-1]) / 2 + rng.normal(0, 25, (400, 400)), 0, 255),
    np.clip(np.add.outer(gradient[::-1], gradient) / 2 + rng.normal(0, 25, (400, 400)), 0, 255),
    np.clip(np.add.outer(gradient, gradient) / 2 + rng.normal(0, 25, (400, 400)), 0, 255),
], axis=2).astype(np.uint8)
photo = Image.fromarray(photo_like)
ui = Image.new("RGB", (1200, 800), "#f3f3f3")
for y in range(80, 760, 60):
    ui.paste(Image.new("RGB", (1000, 44), "#ffffff"), (100, y))
    ui.paste(Image.new("RGB", (40, 20), "#0067c0"), (1020, y + 12))

check("the text article is not a picture", not looks_like_picture(article),
      f"{picture_colours(article)} colours")
check("a chart is not a picture", not looks_like_picture(chart), f"{picture_colours(chart)} colours")
check("a settings-like interface is not a picture", not looks_like_picture(ui),
      f"{picture_colours(ui)} colours")
check("a photo-like image is", looks_like_picture(photo), f"{picture_colours(photo)} colours")
if "gallery" in pages:
    gallery = Image.open(pages["gallery"]).convert("RGB")
    check("the photo gallery page is", looks_like_picture(gallery), f"{picture_colours(gallery)} colours")
else:
    print("  (no local photo for the gallery page; skipped)")

data = encode(Image.new("RGB", (2560, 1440), "white"), 768)
decoded = Image.open(__import__("io").BytesIO(data))
check("encoded as JPEG, long edge 768", data[:2] == b"\xff\xd8" and max(decoded.size) == 768,
      str(decoded.size))

print("\nsending it")

check("a message's images reach the request", _to_payload(
      Message(role="user", content="hi", images=(b"jpeg",))).get("images") == [b"jpeg"])
check("...and a message without any sends none", "images" not in _to_payload(Message(role="user", content="hi")))
sent = []
llm = OllamaLLM()
llm._call = lambda kwargs: sent.append(kwargs) or {"message": {"content": "ok"}}
"".join(llm.chat([Message(role="system", content="s"), Message(role="user", content="q")],
                 images=[photo]))
check("chat(images=...) attaches them to the user message, encoded",
      sent[-1]["messages"][-1].get("images", [b""])[0][:2] == b"\xff\xd8"
      and "images" not in sent[-1]["messages"][0])

print("\nanswers")


class RecordingLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, **kwargs):
        self.calls.append(list(messages))
        yield "An answer."


class FakeScreen:
    def __init__(self, image, live=False):
        self.image, self.is_live, self.grabs = image, live, 0

    def grab(self):
        self.grabs += 1
        return self.image

    def bounds(self):
        return (0, 0, 1600, 1000)


def companion(screen, enabled=True, when="thin_text", blocked=False):
    comp = Companion.__new__(Companion)
    comp.config = AppConfig.load(CONFIG_PATH)
    comp.config.vision.enabled, comp.config.vision.when = enabled, when
    comp.llm, comp.audio, comp.tools = RecordingLLM(), None, None
    comp.memory = ConversationMemory()
    comp.screen = screen
    block = SimpleNamespace(reason="blocked process", window="KeePass")
    comp.privacy = SimpleNamespace(check=lambda windows: block if blocked else None)
    comp.last_context = None
    return comp


def last_images(comp):
    return comp.llm.calls[-1][-1].images


PHOTO_PAGE = ScreenContext(text=THIN, window_title="Photos", app_name="brave.exe", source="uia")
ARTICLE_PAGE = ScreenContext(text=ARTICLE_TEXT, window_title="Antikythera", app_name="brave.exe", source="uia")

comp = companion(FakeScreen(photo))
comp.ask("What am I looking at?", context=PHOTO_PAGE).text()
check("a question about a photo page is sent with the screenshot",
      len(last_images(comp)) == 1 and last_images(comp)[0][:2] == b"\xff\xd8")
check("...taken once", comp.screen.grabs == 1, str(comp.screen.grabs))
check("...and memory keeps the words, not the image",
      all(not m.images for m in comp.memory.history()) and len(comp.memory.history()) == 2)

comp = companion(FakeScreen(photo))
comp.ask("What is this article about?", context=ARTICLE_PAGE).text()
check("an ordinary question about an article gets no screenshot, and none is taken",
      last_images(comp) == () and comp.screen.grabs == 0)

comp = companion(FakeScreen(photo), enabled=False)
comp.ask("What's in this photo?", context=PHOTO_PAGE).text()
check("vision switched off: no screenshot", last_images(comp) == () and comp.screen.grabs == 0)
comp = companion(FakeScreen(photo), when="never")
comp.ask("What's in this photo?", context=PHOTO_PAGE).text()
check("when: never -- none either", last_images(comp) == () and comp.screen.grabs == 0)

comp = companion(FakeScreen(photo, live=True), blocked=True)
comp.ask("What's in this photo?", context=PHOTO_PAGE).text()
check("a window on the privacy list: no screenshot, and not a pixel read",
      last_images(comp) == () and comp.screen.grabs == 0, str(comp.screen.grabs))
try:
    comp.screenshot()
    check("...screenshot() itself refuses", False)
except PrivacyBlocked:
    check("...screenshot() itself refuses", comp.screen.grabs == 0)

print("\nremarks on a page short on text")

SAY = "That runner has the whole beach to themselves this morning, lucky them."
WHY = "the photo is the whole point of the page"


class RemarkLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, temperature=None, json_schema=None):
        kind = "describe" if messages[0].content == DESCRIBE else "compose"
        self.calls.append((kind, messages[-1].images))
        yield "looking at a photo of a beach" if kind == "describe" else json.dumps({"say": SAY, "why": WHY})


def orchestrator(shot, calls):
    def screenshot():
        calls.append(1)
        if isinstance(shot, Exception):
            raise shot
        return shot
    llm = RemarkLLM()
    orch = Orchestrator(llm, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=400),
                        min_time_on_page_s=0, screenshot=screenshot if shot is not None else None)
    orch.observe(PHOTO_PAGE)
    return orch, llm


grabs = []
orch, llm = orchestrator(None, grabs)
check("without vision, a text-poor page is refused as before", orch.poll() is None and llm.calls == [])

grabs = []
orch, llm = orchestrator(ui, grabs)
results = [orch.poll() for _ in range(5)]
check("an interface is still refused, without a model call",
      all(r is None for r in results) and llm.calls == [], str(llm.calls))
check("...having looked once, not every tick", len(grabs) == 1, str(len(grabs)))

grabs = []
orch, llm = orchestrator(photo, grabs)
remark = orch.poll()
check("a photo page gets a remark, though it has under min_chars of text",
      remark is not None and remark.text == SAY, repr(remark))
check("...describing and composing both saw the screenshot",
      [kind for kind, _ in llm.calls] == ["describe", "compose"]
      and all(images and images[0][:2] == b"\xff\xd8" for _, images in llm.calls), str(llm.calls))
check("...from a single screenshot", len(grabs) == 1)

CAPTION = ScreenContext(text="A long caption line under the photo that easily counts as prose",
                        window_title="Photos", app_name="brave.exe", source="uia")
grabs = []
orch, llm = orchestrator(photo, grabs)
orch.observe(CAPTION)
remark = orch.poll()
check("a photo with a one-line caption -- prose, but short -- gets its picture looked at",
      remark is not None and len(grabs) == 1, f"{remark!r}, {len(grabs)} screenshot(s)")
orch, llm = orchestrator(ui, [])
orch.observe(CAPTION)
check("...while short prose with no picture is still refused, without a model call",
      orch.poll() is None and llm.calls == [])

grabs = []
orch, llm = orchestrator(photo, grabs)
remark, why_not = orch.remark_now()
check("asked for, a photo page gets a remark too", remark is not None, why_not)
orch, llm = orchestrator(ui, [])
remark, why_not = orch.remark_now()
check("...and an interface is declined without a model call",
      remark is None and "interface" in why_not and llm.calls == [], why_not)

orch, llm = orchestrator(PrivacyBlocked(SimpleNamespace(reason="blocked", window="KeePass")), [])
check("a blocked screenshot is no picture, not a crash", orch.poll() is None and llm.calls == [])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
