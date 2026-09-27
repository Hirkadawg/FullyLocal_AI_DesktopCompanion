# Architecture

How the companion is put together, and why it is put together that way. For using it, see
[GUIDE.md](GUIDE.md); for the options themselves, `companion/config.yaml`.

## The shape of it

```
   your question ─┐
                  ▼
        CompanionWorker (one background thread)
                  │
                  ├─ perception ──► ScreenContext   (UI Automation, else OCR)
                  ├─ Companion.ask() ──► messages ──► Ollama ──► answer, streamed
                  ├─ voice ──────► Piper ──► speakers, sentence by sentence
                  └─ idle tick ──► orchestrator ──► a remark, or nothing at all
```

Two rules decide where everything lives.

**`Companion` is the application's API.** The CLI, the GUI and the voice loop all call the same
two methods rather than each assembling their own prompts. Prompt assembly sits in `core/`, not in
the provider, so replacing the model provider changes nothing about how questions are framed.

**Everything expensive happens on one worker thread.** Qt owns the main thread and does nothing
but draw; `modules/ui/worker.py` owns a queue and services it. Its `queue.get(timeout=…)` doubles
as the ambient clock — the timeout branch is where watching the screen happens, which is also why
nothing is watched while an answer is being written.

## Reading the screen

Perception is two sources behind one interface (`modules/perception/base.py`), tried in the order
`perception.sources` names, both producing a `ScreenContext`:

1. **UI Automation** asks the application for its text through the accessibility API. Exact, ~40 ms,
   and it includes text scrolled out of view — a whole article rather than a screenful.
2. **OCR** reads pixels. Slower and approximate, but it works on video, games and canvas-drawn
   apps where there is no tree to ask.

The first source that returns at least `perception.min_chars` wins and the rest are never run, so
OCR costs nothing when UI Automation answers.

Which window gets read is decided by coverage: **the window occupying the largest share of the
target monitor**, ties going to the front-most. Focus is deliberately ignored — you type into one
window and ask about another. Slivers, shell surfaces and full-screen overlays are excluded
because each otherwise wins wrongly. `--probe` prints the ranked list with a reason beside
everything skipped.

**Watching is separated from reading.** Every `ambient.sample_interval_s` the ambient loop grabs a
160×90 greyscale thumbnail and compares it with the last one — microseconds of work. A full read
happens only when the screen has changed *and then held still* for `stable_delay_s`, which is what
makes it read pages rather than scrolling. Questions answer from that cached read while it is
fresher than `max_cache_age_s`, so there is no capture delay in front of the model.

**The privacy guard runs before the capture, not after.** It inspects every visible window
overlapping the target monitor, not just the foreground one, because a monitor-wide capture picks
up whatever else is on that monitor. A match blocks the capture outright and says so; the ambient
cache is dropped at the same time, so a later question cannot be answered from a reading taken
before the sensitive window appeared.

## Asking the model

The interesting part of this project is not the call to Ollama — it is what is, and is not, put in
front of the model. Four mechanisms, all of them decided in code:

**The system prompt is sent by section.** `prompts/system.md` is cut at its `## ` headings
(`core/prompt.py`). The part before the first heading — who it is, the language, how to answer, and
how to treat screen text — goes with every message. "When you spoke first", "Grounding", "Tools"
and "Audio" go only when the conversation involves them. Instruction-following falls as
instructions pile up, fastest in small models, and a rule aimed at one kind of message has
repeatedly damaged unrelated replies here.

**The screen goes only with messages about the screen.** `core/relevance.py` decides, from the
message itself (and the turn before it), whether the screen text is wanted, in both English and
Turkish. Attaching it to everything pulled ordinary conversation onto whatever was open — in a
48-message batch, 35 replies were derailed that way; deciding in code took that to none.

**Per-turn notes, not standing rules.** Answer length (`core/length.py`), the language to reply in,
what was actually heard of an interrupted reply, what a tool returned — each is appended to the
turn it applies to and disappears afterwards. The same instruction placed in the system prompt
changes turns it was never meant for.

**Memory is retrieved, not accumulated.** Three kinds, each fetched only when it bears on the
message:

- `core/memory.py` replays the last few turns, questions and answers only — never the screen text
  they were based on, which is stale by the next question and would crowd out the current screen.
- `core/reflection.py` writes a few lasting facts to `data/about_you.md`, a plain file you can
  edit or delete; `Companion` sends only the lines that bear on the question.
- `core/moments.py` brings back replies you gave a thumbs up, matched by meaning through
  EmbeddingGemma (`modules/llm/embeddings.py`) with shared-word matching as the fallback when no
  embedding model is available. Document vectors are cached, so matching costs one short embed per
  message.

Replies are capped by `ollama.max_reply_tokens` (`num_predict`) so a runaway generation cannot
fill the context, and `llm.log_prompts` writes exactly what was sent — settings, token counts,
every message, the reply — to `data/prompts/`, which is the only honest way to debug a prompt.

## Speaking unprompted

The remark path is the part most likely to be annoying, so it is the part most decided in code.
The design intent, stated in the module docstrings: **seeing is separated from speaking.** Nothing
observed turns into speech directly; it becomes an event, and one loop decides what, if anything,
to do about it.

| Piece | Job |
|---|---|
| `core/observer.py` | a silent one-line impression of what you are doing |
| `core/orchestrator.py` | event → is this worth a remark, and which kind → prompt → remark |
| `core/attention.py` | the manners: cooldowns, budgets, quiet periods, waiting for audio to stop |
| `core/learning.py` | where remarks are welcome, learned per site, kind and moment |
| `core/moments.py` | natural stopping points — a video ending, a scroll coming to rest |

The kind of remark (a question, an observation, something remembered, something about what is
playing) is chosen in code from what is on screen and what has happened recently. The model is
handed that decision and asked only to write one sentence. What comes back is then checked in
code and dropped if it advises, narrates what you are obviously doing, repeats something said
recently, or reads the page back at you — a remark that copies six or more words in a row from the
page is refused outright.

Waiting is not refusing: a remark blocked by a cooldown stays a candidate and is asked about again
on the next tick, rather than being thrown away. Every remark can show why it was made, and the
reason is shown on screen, never spoken.

## Voice and audio

**Out.** Answers are spoken as they generate: tokens accumulate to a sentence boundary, that
sentence is synthesised while the model writes the next one, so the first audio arrives with the
first sentence (~1.2 s) rather than after the last. Each sentence is spoken by a voice chosen for
the language it is written in (`modules/voice/language.py`), and Piper runs
on the CPU, so voice costs no VRAM. `Esc` clears the audio queue within a buffer.

**In.** Push-to-talk holds the microphone open only while the key is down; hands-free mode keeps it
open and sends each utterance as it completes. Transcription is faster-whisper on the CPU (~450 ms
an utterance with `base.en`). Silence is rejected by level, plus a short list of the phrases
Whisper invents over quiet audio, because given silence it does not return nothing.

**What the speakers are playing** is a separate, off-by-default path: WASAPI loopback taps the
output device, so it hears what you hear and nothing from the microphone. Any chunk recorded while
the companion was speaking is discarded whole, or it would answer from its own voice.

**The avatar** is driven by the same speech: Piper's phoneme timings become mouth shapes
(`modules/voice/visemes.py`), and `core/mood.py` picks an expression in code from the message and
the reply.

## Tools

`modules/tools/` holds timers, a stopwatch, notes and the clock. The model asks; the app acts.
Nothing is written because the model felt like it — a note is saved only when the message asked
for one, and a tool's result is handed back as a per-turn note rather than mixed into the screen
text. Timers live in `data/timers.json` and survive a restart; notes are plain Markdown in
`data/notes.md`, readable in any editor long after this project is abandoned.

## Configuration and data

`config.yaml` holds the defaults with their reasoning in comments. The settings page writes only
the values you changed to `data/settings.yaml`, which is layered on top at load time
(`core/config.py`), so the documented defaults stay readable and your changes survive an update.
Resets move files to `data/archive/` instead of deleting them.

## Testing

Suites are plain scripts with no framework, because a framework would be the only dependency here
that is not already needed to run the app. Three habits matter more than the count:

- **Anything judged on model output is judged over a batch.** One reply at temperature proves
  nothing; suites ask the same thing six to twenty-four times and score the replies.
- **A new check has to be shown failing** against the code without the change — a `git worktree` at
  the previous commit does that without disturbing the working tree.
- **No suite touches real data.** Every config a suite loads points at a temporary folder, because
  a model given real tools will use them.

See [companion/tests/README.md](../companion/tests/README.md).
