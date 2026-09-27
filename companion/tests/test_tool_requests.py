"""What the user's own words ask for.

Notes are saved by the app, never by the model, and tools that change things --
timers, the stopwatch -- only run when the message asks for them.

Measured on qwen3.5:4b before this: over 24 ordinary messages it wrote 2 to 4
notes nobody asked for, and once started a note, a timer and a stopwatch after
"Interesting.". Stricter tool descriptions made it worse, 8 to 13 unasked
writes, so the decision moved out of the model and into code.
"""

import sys
import tempfile
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import Companion, build_tools
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.types import ScreenContext, ToolCall
from modules.tools.requests import asked_for, asks_for_timing, note_request, offered

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("recognising a note request, and what to save")

DICTATED = [
    ("Note that the gears were made of bronze.", "the gears were made of bronze"),
    ("Take a note that the Antikythera mechanism has thirty bronze gears.",
     "the Antikythera mechanism has thirty bronze gears"),
    ("Remember this for me: it dates from about 150 BC.", "it dates from about 150 BC"),
    ("Not al: dişliler bronzdan yapılmış.", "dişliler bronzdan yapılmış"),
    ("Dişlilerin bronz olduğunu not alır mısın?", "Dişlilerin bronz olduğunu"),
    ("the gears are bronze, note that", "the gears are bronze"),
]
for message, expected in DICTATED:
    request = note_request(message)
    check(f"dictated: {message!r}",
          request is not None and expected.lower() in request.text.lower(), repr(request))

for message in ("Can you note that down?", "Great, write that down.",
                "Save this as a note please", "Bunu not al.", "Jot that down."):
    request = note_request(message)
    check(f"points back at the last reply: {message!r}",
          request is not None and request.text == "", repr(request))

for message in ("What have I noted so far?", "Did I note that?", "I remember that from school.",
                "Do you remember this article?", "Interesting.", "Yes.", "What notes do I have?",
                "Not really.", "Notlarımda ne var?", "Not all of it made sense.",
                "Thanks, that helps.", "Summarize the key points in three bullets.",
                "Cool, I'll keep reading.", "Note taking apps are overrated."):
    check(f"not a note request: {message!r}", note_request(message) is None,
          repr(note_request(message)))

print("\ntools that change things need a message that asks for them")

for message in ("Set a timer for 20 minutes for the pasta.", "Start a stopwatch.",
                "Cancel the pasta timer.", "Remind me in ten minutes.",
                "20 dakikalık zamanlayıcı kur.", "Kronometreyi başlat.", "Stop the stopwatch."):
    check(f"asks for timing: {message!r}", asks_for_timing(message))
for message in ("Interesting.", "Yes.", "What is this article about?", "Thanks, that helps.",
                "Cool, I'll keep reading.", "Bu makale ne hakkında?", "That's wild."):
    check(f"doesn't: {message!r}", not asks_for_timing(message))
check("a state-changing tool nobody listed is never allowed",
      not asked_for("delete_everything", "please delete everything"))
check("a note is never a model tool, however it is asked for",
      not asked_for("take_note", "Note that the gears are bronze."))

# Each tool has its own check. With one shared "about timing" check,
# qwen3.5:4b answered "Set a one minute timer called tea." by setting the timer
# and starting the stopwatch too.
START, STOP, CHECK = {"action": "start"}, {"action": "stop"}, {"action": "check"}
GATE = [
    ("set_timer", "Set a one minute timer called tea.", {}, True),
    ("stopwatch", "Set a one minute timer called tea.", START, False),
    ("cancel_timer", "Set a one minute timer called tea.", {}, False),
    ("stopwatch", "Start a stopwatch.", START, True),
    ("set_timer", "Start a stopwatch.", {}, False),
    ("stopwatch", "Stop the stopwatch.", STOP, True),
    ("cancel_timer", "Stop the stopwatch.", {}, False),
    ("cancel_timer", "Cancel the pasta timer.", {}, True),
    ("cancel_timer", "Never mind the tea.", {}, True),
    ("stopwatch", "Remind me in ten minutes.", STOP, False),
    ("stopwatch", "How long has it been?", CHECK, True),
    ("stopwatch", "Kronometreyi başlat.", START, True),
    ("set_timer", "20 dakikalık zamanlayıcı kur.", {}, True),
    ("cancel_timer", "Zamanlayıcıyı iptal et.", {}, True),
    ("set_timer", "Interesting.", {}, False),
    ("stopwatch", "Yes.", START, False),
]
for tool, message, arguments, expected in GATE:
    check(f"{tool}{'' if expected else ' not'} allowed for {message!r} {arguments or ''}",
          asked_for(tool, message, arguments) == expected)

print("\nonly the tools a message is about are offered at all")

# Offered every tool on every turn, qwen3.5:4b checked the clock before
# replying to "Yes." 10 times in 10 near midnight, and reached for timers after
# "go on" until the turn ran out of rounds.
TOOLS = ["get_time", "stopwatch", "set_timer", "list_timers", "cancel_timer",
         "read_notes", "search_notes"]


def offered_for(message):
    return {name for name in TOOLS if offered(name, message)}


for message in ("Yes.", "go on", "Interesting.", "What is this article about?",
                "Bu makale ne hakkında?"):
    check(f"no tools for {message!r}", offered_for(message) == set(),
          str(sorted(offered_for(message))))
check("'What time is it?' gets the clock and nothing else",
      offered_for("What time is it?") == {"get_time"}, str(sorted(offered_for("What time is it?"))))
check("'Saat kaç?' gets the clock", "get_time" in offered_for("Saat kaç?"))
check("a timer request gets the timer tools",
      {"set_timer", "list_timers", "cancel_timer", "stopwatch"}
      <= offered_for("Set a timer for 20 minutes."))
check("'What have I noted so far?' gets the note readers and no timers",
      {"read_notes", "search_notes"} <= offered_for("What have I noted so far?")
      and "set_timer" not in offered_for("What have I noted so far?"))
check("a tool with no rule is always offered", offered("some_new_lookup", "anything"))

print("\nthrough the companion")

tmp = Path(tempfile.mkdtemp(prefix="companion-requests-"))
cfg = AppConfig.load(CONFIG_PATH)
cfg.tools.notes_file = str(tmp / "notes.md")
cfg.tools.timers_file = str(tmp / "timers.json")
PAGE = ScreenContext(text="The Antikythera mechanism is an ancient Greek orrery.",
                     window_title="Antikythera mechanism - Wikipedia",
                     app_name="brave.exe", source="uia")


class ScriptedLLM:
    """Replies "Done.", after asking for the given tool calls on its first round."""

    def __init__(self, calls=()):
        self.pending = list(calls)
        self.rounds = []

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, **kwargs):
        self.rounds.append((messages, tools))
        if self.pending and collect_tool_calls is not None:
            collect_tool_calls.extend(self.pending)
            self.pending = []
            return
        yield "Done."


def companion(llm):
    # Built around __init__, so no screen, model or audio device is needed.
    comp = Companion.__new__(Companion)
    comp.config, comp.llm, comp.audio = cfg, llm, None
    comp.memory = ConversationMemory()
    comp.tools = build_tools(cfg)
    return comp


def last_turn(llm):
    return llm.rounds[-1][0][-1].content


llm = ScriptedLLM()
comp = companion(llm)
offered = {spec.name for spec in comp.tools.specs()}
check("the model is never offered a note-taking tool", "take_note" not in offered,
      str(sorted(offered)))
check("it can still read and search notes", {"read_notes", "search_notes"} <= offered)

notebook = comp.tools.notebook
comp.ask("Note that the gears were made of bronze.", context=PAGE).text()
entries = notebook.entries()
check("a dictated note is saved by the app",
      len(entries) == 1 and "gears were made of bronze" in entries[0], str(entries))
check("...with what they were reading", bool(entries) and "Antikythera" in entries[0])
check("the model is told exactly what was saved",
      "NOTE SAVED" in last_turn(llm) and "gears were made of bronze" in last_turn(llm))

comp.memory = ConversationMemory()
comp.memory.add_turn("How many gears did it have?", "At least thirty bronze gears survive.")
comp.ask("Can you note that down?", context=PAGE).text()
check("'note that down' saves the reply it points at",
      "thirty bronze gears survive" in notebook.entries()[-1], notebook.entries()[-1])

before = len(notebook.entries())
comp.memory = ConversationMemory()
comp.ask("Note that down.", context=PAGE).text()
check("with nothing to point at, nothing is saved", len(notebook.entries()) == before)
check("...and the model is told so, so it can ask what to note",
      "NOTE NOT SAVED" in last_turn(llm))

comp.ask("What is this article about?", context=PAGE).text()
check("the note line rides on that one turn only", "NOTE" not in last_turn(llm))
check("an ordinary question is sent with no tools at all", llm.rounds[-1][1] is None,
      str(llm.rounds[-1][1]))

timer = ToolCall(name="set_timer", arguments={"minutes": 20, "label": "pasta"})
stopwatch = ToolCall(name="stopwatch", arguments={"action": "start"})

eager = ScriptedLLM([timer, stopwatch])
comp = companion(eager)
comp.ask("Interesting.", context=PAGE).text()
check("after 'Interesting.', a timer the model tried to set is not created",
      comp.tools.timers.active() == [], str([t.label for t in comp.tools.timers.active()]))
results = [m.content for m in eager.rounds[-1][0] if m.role == "tool"]
check("...and the model is told nothing was changed",
      len(results) == 2 and all("Not done" in r for r in results), str(results))

asked = ScriptedLLM([timer])
comp = companion(asked)
comp.ask("Set a timer for 20 minutes for the pasta.", context=PAGE).text()
check("when they ask for a timer, it is created",
      [t.label for t in comp.tools.timers.active()] == ["pasta"],
      str([t.label for t in comp.tools.timers.active()]))


class Repeating(ScriptedLLM):
    """Asks for the same timer on each of its first rounds, as qwen3.5:4b did."""

    def __init__(self, per_round, rounds):
        super().__init__()
        self.per_round, self.left = per_round, rounds

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, **kwargs):
        self.rounds.append((messages, tools))
        if self.left and tools and collect_tool_calls is not None:
            self.left -= 1
            collect_tool_calls.extend(self.per_round)
            return
        yield "Done."


def without_timers(llm):
    # Every companion here shares one timers file; start these checks empty.
    comp = companion(llm)
    for running in comp.tools.timers.active():
        comp.tools.timers.cancel(running.id)
    return comp


tea = ToolCall(name="set_timer", arguments={"minutes": 1, "label": "tea"})
for label, llm in (("round after round", Repeating([tea], 3)),
                   ("twice in one round", Repeating([tea, tea], 1))):
    comp = without_timers(llm)
    comp.ask("Set a one minute timer called tea.", context=PAGE).text()
    labels = [t.label for t in comp.tools.timers.active()]
    check(f"the same timer asked for {label} is set once", labels == ["tea"], str(labels))
results = [m.content for m in llm.rounds[-1][0] if m.role == "tool"]
check("...and the repeat is told it was already done",
      len(results) == 2 and "Already done" in results[1], str(results))

other = Repeating([tea, ToolCall(name="set_timer", arguments={"minutes": 20, "label": "pasta"})], 1)
comp = without_timers(other)
comp.ask("Set a one minute timer for tea and twenty minutes for the pasta.", context=PAGE).text()
labels = sorted(t.label for t in comp.tools.timers.active())
check("two different timers in one turn are both set", labels == ["pasta", "tea"], str(labels))

looking = ScriptedLLM([ToolCall(name="get_time", arguments={})])
comp = companion(looking)
comp.ask("Interesting.", context=PAGE).text()
results = [m.content for m in looking.rounds[-1][0] if m.role == "tool"]
check("tools that only look always run", bool(results) and "Not done" not in results[0],
      str(results))

class AlwaysCalling(ScriptedLLM):
    """A model that asks for the stopwatch every round it is allowed to."""

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, **kwargs):
        self.rounds.append((messages, tools))
        if collect_tool_calls is not None:
            collect_tool_calls.append(stopwatch)
            return
        yield "Here's my answer."


looper = AlwaysCalling()
comp = companion(looper)
reply = comp.ask("Interesting.", context=PAGE).text().strip()
check("a model that keeps asking for a refused tool still gets to answer",
      reply == "Here's my answer.", repr(reply))
check("...through a last pass with no tools", looper.rounds[-1][1] is None)
check("...and not the old apology for a loop", "stuck" not in reply)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
