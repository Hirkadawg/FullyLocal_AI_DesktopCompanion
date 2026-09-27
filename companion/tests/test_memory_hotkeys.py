"""Conversation memory trimming and hotkey parsing. Needs no window."""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.logging import setup_logging
from core.memory import ConversationMemory
from modules.ui.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    HotkeyError,
    parse,
)

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("conversation memory:")

m = ConversationMemory(max_turns=3, max_chars=10_000)
for i in range(5):
    m.add_turn(f"question {i}", f"answer {i}")
check("turn cap enforced", m.turns == 3, f"turns={m.turns}")
check("oldest turns dropped first", "question 2" in m.history()[0].content,
      f"first={m.history()[0].content!r}")
check("history alternates user/assistant",
      [msg.role for msg in m.history()] == ["user", "assistant"] * 3)

m = ConversationMemory(max_turns=50, max_chars=200)
for i in range(10):
    m.add_turn("q" * 40, "a" * 40)
total = sum(len(msg.content) for msg in m.history())
check("char budget enforced", total <= 200, f"chars={total}")
check("never trims below one turn", m.turns >= 1, f"turns={m.turns}")

m = ConversationMemory()
m.add_turn("question", "   ")
check("empty answer is not stored", m.turns == 0)
m.add_turn("question", "real answer")
check("real answer is stored", m.turns == 1)
m.clear()
check("clear empties history", m.history() == [])

m = ConversationMemory(enabled=False)
m.add_turn("question", "answer")
check("disabled memory stores nothing", m.history() == [])

print("\nhotkey parsing:")

mods, key = parse("ctrl+alt+space")
check("ctrl+alt+space", mods == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT) and key == 0x20,
      f"mods={mods:#x} key={key:#x}")

mods, key = parse("Ctrl+Shift+K")
check("case-insensitive, letter key",
      mods == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT) and key == ord("K"))

mods, key = parse("ctrl+f5")
check("function keys", key == 0x74, f"key={key:#x}")

for bad, why in [
    ("space", "no modifier"),
    ("ctrl", "no key"),
    ("ctrl+notakey", "unknown key"),
    ("", "empty"),
]:
    try:
        parse(bad)
        check(f"rejects {why}", False)
    except HotkeyError:
        check(f"rejects {why}", True)

print("\nprompt assembly with history:")

from core.companion import Companion
from core.config import AppConfig
from core.types import ScreenContext

cfg = AppConfig.load(CONFIG_PATH)
mem = ConversationMemory(max_turns=4, max_chars=4000)
mem.add_turn("what is this?", "an article about gears")
# Built bypassing __init__ so prompt assembly can be tested without a screen,
# a model or an audio device. Every attribute build_messages touches has to be
# set by hand, which is the cost of the shortcut.
comp = Companion.__new__(Companion)
comp.config, comp.memory, comp.audio = cfg, mem, None
comp.tools = None

ctx = ScreenContext(text="SCREEN CONTENT HERE", source="uia", app_name="brave.exe")
msgs = comp.build_messages("explain that again", ctx)

check("system prompt first", msgs[0].role == "system")
check("history sits between system and current turn",
      [m.role for m in msgs] == ["system", "user", "assistant", "user"],
      f"roles={[m.role for m in msgs]}")
check("screen text only on the current turn",
      "SCREEN CONTENT HERE" in msgs[-1].content
      and not any("SCREEN CONTENT" in m.content for m in msgs[1:-1]))
check("current question present", "explain that again" in msgs[-1].content)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
