"""What the companion says unprompted has to be in memory too.

The reported failure, verbatim:

    companion: That AI speech stuff looks intense -- did you find anything
               interesting?
    user:      Yes.
    companion: The current time is Sunday, 13 September 2026, at 12:45:44.

"Yes." was a reply to a question the model had no record of asking, so it
arrived as an opening line with no referent and the model reached for a tool.
The cause was structural rather than a missed call: `add_turn(question, answer)`
stores a PAIR, and a remark has no question in front of it, so there was no way
to store one at all.
"""

import json
import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.orchestrator import Orchestrator
from core.types import Message, ScreenContext

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, temperature=None, json_schema=None):
        # Plain text when describing the activity; JSON, as asked, for a remark.
        self.prompts.append(messages)
        if json_schema is None:
            yield "reading about AI speech synthesis"
        else:
            yield json.dumps({"say": self.reply, "why": "it follows what they are reading"})

    def health_check(self):
        pass


print("memory can hold something said without being asked")

m = ConversationMemory()
m.add_remark("Did you find anything interesting?")
check("a remark is stored", len(m) == 1, f"{len(m)} message(s)")
check("...as an assistant message", m.history()[0].role == "assistant")
check("...with no invented question in front of it",
      all(msg.role == "assistant" for msg in m.history()))
check("it counts as one exchange", m.turns == 1, f"turns={m.turns}")

m.add_turn("Yes.", "Glad to hear it.")
history = m.history()
check("the user's reply follows the remark",
      [msg.role for msg in history] == ["assistant", "user", "assistant"],
      str([msg.role for msg in history]))
check("the reply can be seen to be a reply",
      history[0].content == "Did you find anything interesting?"
      and history[1].content == "Yes.")

print("\nempty and disabled memories behave as before")

m2 = ConversationMemory()
m2.add_remark("   ")
check("an empty remark is not stored", len(m2) == 0)
m3 = ConversationMemory(enabled=False)
m3.add_remark("anything")
check("a disabled memory stores no remark", m3.history() == [])

print("\ntrimming keeps pairs together and lone remarks whole")

m4 = ConversationMemory(max_turns=3, max_chars=10_000)
m4.add_remark("remark one")
m4.add_turn("q1", "a1")
m4.add_remark("remark two")
m4.add_turn("q2", "a2")
check("oldest exchange is dropped at the limit", m4.turns == 3,
      f"turns={m4.turns}")
kept = [msg.content for msg in m4.history()]
check("the lone remark went, and went alone", "remark one" not in kept)
check("...leaving the pair after it intact",
      kept == ["q1", "a1", "remark two", "q2", "a2"], str(kept))

m5 = ConversationMemory(max_turns=2, max_chars=10_000)
m5.add_turn("q1", "a1")
m5.add_remark("r1")
m5.add_remark("r2")
check("dropping a pair removes both halves, never just the question",
      [msg.content for msg in m5.history()] == ["r1", "r2"],
      str([msg.content for msg in m5.history()]))

print("\ntrimming by characters terminates on remark-only history")

m6 = ConversationMemory(max_turns=50, max_chars=100)
for i in range(10):
    m6.add_remark("x" * 40 + str(i))
check("a long run of remarks is trimmed, not looped over", m6.turns <= 3,
      f"turns={m6.turns}")
check("...and something survives", m6.turns >= 1)

print("\nthe orchestrator records the remark it just made")

screen = ScreenContext(text="x " * 500, window_title="AI speech synthesis",
                       app_name="brave.exe")


def remark_with(reply, memory=None):
    """One remark from a page that is due, through the real orchestrator."""
    orch = Orchestrator(
        FakeLLM(reply),
        AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100),
        max_words=25, memory=memory, min_time_on_page_s=0,
    )
    orch.observe(screen)
    return orch.poll()


memory = ConversationMemory()
remark = remark_with(
    "That AI speech stuff looks intense -- did you find anything interesting?", memory
)
check("a remark was produced", remark is not None)
check("it reached memory", len(memory) == 1, f"{len(memory)} message(s)")
check("memory holds exactly what was said",
      memory.history()[0].content == remark.text if remark else False)

print("\na declined remark is not recorded")

memory2 = ConversationMemory()
remark_with("NOTHING", memory2)
check("NOTHING leaves memory untouched", len(memory2) == 0)

print("\nan empty memory is still wired up (the __len__ falsiness trap)")

memory3 = ConversationMemory()
check("an empty memory is falsy -- which is why `or` defaults are banned here",
      not memory3)
remark_with("Hm, neat.", memory3)
check("the FIRST remark of a session is still recorded", len(memory3) == 1,
      f"{len(memory3)} message(s)")

print("\nno memory passed is not an error")

check("an orchestrator without memory still speaks", remark_with("Hm.") is not None)

print("\nthe next question carries the remark into the prompt")

# The whole point: build_messages splices history in, so the remark is what the
# model sees above the user's "Yes.".
memory4 = ConversationMemory()
memory4.add_remark("Did you find anything interesting?")
spliced = [
    Message(role="system", content="sys"),
    *memory4.history(),
    Message(role="user", content="Question: Yes."),
]
check("history lands between the system prompt and the question",
      [msg.role for msg in spliced] == ["system", "assistant", "user"],
      str([msg.role for msg in spliced]))
check("so 'Yes.' has something to refer to",
      "anything interesting" in spliced[1].content)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
