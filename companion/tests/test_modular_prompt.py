"""Only the system prompt's sections a message needs.

prompts/system.md went whole with every message, ~1,300 tokens of rules. Measured
with the real model on the same battery, whole against by need:
- prompt tokens: a greeting 1,433 -> 403, a screen question 1,675 -> 840, a tool
  question 1,996 -> 1,518, a reply to a remark 1,684 -> 1,107;
- tool questions calling a tool 6 and 6; replies to a remark 7 and 7 of 8;
  plain prose 4 and 4; everyday chat 6 and 6; what isn't on the screen said to
  be missing, read by hand, all right either way.
The paragraph on the screen's text stays in the core: moved into a section sent
only with the screen, "watch my screen" fell from 9-10 in 10 to 4-5 (I5).
"""

import sys
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import Companion, build_tools
from core.config import AppConfig, LLMConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.prompt import SECTIONS, asks_about_audio, compose, sections
from core.settings import SETTINGS
from core.types import ScreenContext

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("the sections")

cfg = AppConfig.load(CONFIG_PATH)
text = cfg.system_prompt
headings = [heading for heading, _ in sections(text)]
check("system.md has the core and every section code sends by need",
      headings[0] == "" and all(h in headings for h in SECTIONS), str(headings))
core_only = compose(text, set())
check("with no needs, only the core: who it is, the screen's text, the language, how to answer",
      "desktop companion" in core_only and "text extracted from their screen" in core_only
      and "Answer in the language" in core_only and "Lead with the answer" in core_only and "## " not in core_only)
check("with every need, the whole file", compose(text, set(SECTIONS.values())) == "\n\n".join(b for _, b in sections(text)))
check("a heading code doesn't know is always sent, so a new section can't be lost",
      "## Something new" in compose("Core.\n\n## Tools\nT.\n\n## Something new\nN.", set()))
check("audio questions, English and Turkish",
      all(asks_about_audio(m) for m in ("what did they just say?", "what song is this?", "ne dedi?", "şarkı sözleri"))
      and not any(asks_about_audio(m) for m in ("what is this article about?", "kaç dişlisi var?")))

print("\nwhat each message gets")


class Capturing:
    def __init__(self):
        self.seen = []

    def chat(self, messages, images=None, tools=None, stream=False, collect_tool_calls=None, **kwargs):
        self.seen.append(messages)
        yield "OK."


def companion(modular=True):
    comp = Companion.__new__(Companion)
    comp.config = AppConfig.load(CONFIG_PATH)
    comp.config.llm.modular_system_prompt = modular
    comp.config.ratings.remember_moments = False
    comp.config.vision.enabled = False
    comp.tools, comp.memory, comp.audio = build_tools(comp.config), ConversationMemory(), None
    comp.llm = Capturing()
    return comp


ARTICLE = ScreenContext(text="The Antikythera mechanism is an Ancient Greek hand-wound orrery. " * 20,
                        window_title="Antikythera mechanism - Wikipedia", app_name="brave.exe", source="uia")


def system(comp, message, lines=None):
    comp.audio = SimpleNamespace(recent=lambda minutes, max_chars: lines) if lines else None
    comp.ask(message, context=ARTICLE).text()
    comp.audio = None
    return comp.llm.seen[-1][0].content


def has(prompt, *names):
    return {name for name in SECTIONS if f"## {name}" in prompt} == set(names)


comp = companion()
check("'how are you?': the core alone", has(system(comp, "how are you?")), system(comp, "how are you?")[-200:])
check("a question about the screen: grounding", has(system(comp, "When was it found?"), "Grounding"))
check("'what time is it?': the tools (and grounding, since 'it' points at the screen)",
      has(system(comp, "what time is it?"), "Tools", "Grounding"))
check("'set a timer for 5 minutes': the tools alone", has(system(comp, "set a timer for 5 minutes"), "Tools"))
check("'note that tomorrow is Monday': the tools, which say how notes are saved",
      "## Tools" in system(comp, "note that tomorrow is Monday"))
line = SimpleNamespace(at=0, language="en", text="Welcome back to the channel.")
check("audio heard: the audio rules", "## Audio" in system(comp, "tell me a joke", lines=[line]))
check("asked about audio with nothing heard: the audio rules, which say to say so",
      "## Audio" in system(comp, "ne dedi?"))
comp = companion()
comp.memory.add_remark("Those gears are wild.", "the gears stand out")
check("after something it said unasked: the rules for replying", "## When you spoke first" in system(comp, "Yes."))
comp = companion()
comp.memory.add_turn("How old is it?", "About 2,100 years.")
check("after an ordinary answer only: not those", "## When you spoke first" not in system(comp, "thanks"))
check("switched off: the whole file, every time", system(companion(modular=False), "how are you?") == text)

print("\nthe setting")
check("off in code, on in config.yaml, on the settings page's AI model tab",
      LLMConfig().modular_system_prompt is False and AppConfig.load(CONFIG_PATH).llm.modular_system_prompt is True
      and any(s.key == "llm.modular_system_prompt" and s.section == "AI model" for s in SETTINGS))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
