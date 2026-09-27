# Using the companion

Ask a local model about whatever is on your screen. Everything runs on your own
machine: read the screen → `qwen3.5:4b` through Ollama → answer.

Every command below is run from the `companion\` folder, where `main.py` lives.
See [../README.md](../README.md) for what the app is and how to install it, and
[ARCHITECTURE.md](ARCHITECTURE.md) for how it works inside.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
ollama pull qwen3.5:4b
```

`qwen3.5:4b` needs Ollama 0.17.1 or newer.

## Run

**Windowed** — double-click `run_gui.bat`, or:

```bash
.venv\Scripts\python.exe main.py --gui
```

An always-on-top panel docks to the corner of monitor 1. `Ctrl+Shift+Space`
shows and hides it from anywhere; it also sits in the system tray. `Esc` stops an
answer mid-stream, or hides the window when idle. Drag it by its body to move it.
Follow-ups work — "explain that last part again" resolves against what was just
said.

Closing the window only hides it. Quit from the tray icon or the window's `quit`
button, which shuts the model worker down cleanly.

**Terminal** — double-click `run.bat`, or:

```bash
.venv\Scripts\python.exe main.py
```

Then type a question. `:help` lists the REPL commands.

## Commands

| Command | Purpose |
|---|---|
| `main.py --gui` | always-on-top window with tray icon and global hotkey |
| `main.py` | interactive terminal loop |
| `main.py --ask "..."` | one question, print, exit |
| `main.py --list-monitors` | show the monitor table and which one config uses |
| `main.py --shot check.png` | save a capture — confirms you're reading the right screen |
| `main.py --image page.png` | read a saved image instead of the live screen |
| `main.py --say "..."` | speak text and exit — tests the voice on its own |
| `main.py --listen 5` | record 5s from the mic, transcribe, print what it heard |
| `main.py --list-audio` | list audio output devices |
| `main.py --watch 60` | run the ambient loop for 60s and report what it costs |
| `main.py --probe` | show window selection and run each perception source separately |
| `main.py --window brave` | read a specific window instead of choosing automatically |
| `main.py --debug` | verbose logs and per-stage timings |

In the REPL: `:screen` (raw OCR text), `:timings`, `:windows` (what the privacy
guard sees), `:help`, `exit`.

`--image` is the one to reach for when tuning prompts or OCR settings: it makes
the whole pipeline reproducible instead of depending on what happens to be on
screen.

## Tests

```bash
.venv\Scripts\python.exe tests\run_all.py --fast
```

48 suites, about a minute, nothing external needed. Drop the `--fast` for all
85 (several minutes), which needs Ollama running, audio devices, and the
companion itself closed so the hotkeys are free. See
[../companion/tests/README.md](../companion/tests/README.md).

## Which window does it read?

UI Automation reads one window, so something has to choose it. The rule is
simply **whichever window covers the largest share of the target monitor**, with
ties (two maximised windows) going to the front-most.

Focus is deliberately ignored. You type questions into a terminal on the other
monitor, so the focused window is routinely not the one you're asking about —
selecting by focus meant having to click the article first, every time.

Three kinds of window are excluded, because each of them otherwise wins wrongly:

- **Slivers** — a window mostly on another display that overlaps this one by a
  few pixels. A maximised window's frame extends a little past the screen edge,
  so neighbouring monitors genuinely do bleed into each other; without
  `min_window_on_monitor` these count as being "on" this monitor.
- **Shell surfaces** — the desktop spans every monitor and would look like the
  largest window available. Excluded by window class.
- **Overlays** — the NVIDIA/Steam/Discord overlays cover the whole screen above
  everything else and contain nothing readable. See `capture.ignore_processes`.

`--probe` prints the ranked list with a reason beside everything it skipped, so
a wrong choice is diagnosable rather than mysterious.

If the automatic choice is ever wrong, you can pin one window without changing
the default behaviour:

```bash
.venv\Scripts\python.exe main.py --window brave
```

or permanently via `capture.window_match` in `config.yaml`. If nothing matches,
it warns and falls back to automatic selection rather than failing.

## Which monitor?

`monitor_index: 1` in `config.yaml` means the first physical monitor as *mss*
enumerates them. That ordering comes from Windows internals and doesn't
necessarily match the numbers in Display Settings. Verify rather than assume:

```bash
.venv\Scripts\python.exe main.py --shot check.png
```

## Privacy

The companion refuses to capture at all if a window matching the ignore-list is
visible **anywhere on the target monitor** — not just in the foreground, since a
full-monitor screenshot picks up background windows too. The check runs before
the screenshot, so blocked content is never captured rather than captured and
discarded.

Defaults cover common password managers plus title patterns like `password`,
`seed phrase`, and `incognito`. Edit the `privacy` section of `config.yaml`;
patterns are Python regexes matched case-insensitively against window titles.

Blocks are loud — you're told which rule matched. A rule with a broken regex is
logged as an error rather than silently dropped.

## How it reads the screen

Two perception sources, tried in the order set by `perception.sources`:

1. **UI Automation** — asks the application for its text through the same
   accessibility API screen readers use. Exact text, no misreads, and it
   includes content scrolled *out of view*, so a whole article is available
   rather than the screenful you can see.
2. **OCR** — reads the pixels. Slower and approximate, but works on anything:
   video, games, remote desktop, canvas-drawn apps.

The first source returning at least `perception.min_chars` wins, and later
sources are never run — so OCR isn't paid for when UIA answers.

Measured on the reference machine, same 2560×1440 screen:

| | UI Automation | OCR |
|---|---|---|
| Time | **38 ms** | 5,400 ms |
| Text | 20,000 chars (capped) | 5,635 chars |

**Chromium quirk:** Chrome, Edge, Brave and Electron apps keep their
accessibility tree switched off until something asks for it, and switching on is
asynchronous. The UIA source detects an empty read, signals the renderer, waits
`uia.retry_delay_s`, and tries once more. A first question on a freshly opened
browser can therefore be a beat slower than the ones after it.

## Ambient awareness

The companion watches the screen continuously without paying to read it. Every
`ambient.sample_interval_s` it grabs a 160×90 greyscale thumbnail and compares it
to the last one — microseconds of work. Expensive perception runs only when the
screen has **changed** and then **held still** for `stable_delay_s`.

That settling delay is the point: without it, scrolling an article would fire a
read on every frame, each capturing a half-scrolled page. Waiting for stillness
means it reads pages, not motion.

Questions then answer from that cached read when it's fresher than
`max_cache_age_s`, so there's no capture delay before the model starts —
**1 ms instead of 42 ms**, and instead of seconds when OCR is the source.

Measured idle cost on the reference machine: **1.9% of one core**, 60 seconds on
a static 1440p screen. The `mss` full-screen grab is ~21 ms and dominates
completely — everything after it is ~0.02 ms — so `sample_interval_s` sets the
idle cost almost by itself. Halve it for faster reactions at double the CPU.

See it live, without involving the model:

```bash
.venv\Scripts\python.exe main.py --watch 60
```

Switch windows or scroll while it runs. It reports samples, changes, full
re-reads and CPU used.

Privacy still applies: while a blocked window is visible the loop stays silent,
the cache is dropped, and questions are refused rather than answered from a
reading taken before that window appeared.

## Performance

| Stage | Cost |
|---|---|
| capture | ~1 ms |
| UI Automation | ~40 ms warm, ~800 ms on the first call |
| OCR (CPU, downscaled to 1920px) | 1–5 s depending on how much text is on screen |
| model | depends on answer length; stays in VRAM for `keep_alive` |

Nothing runs between questions — perception happens only when you ask.

## Known limits

- OCR misreads small or low-contrast UI text. `:screen` shows exactly what the
  model received, which is the first thing to check when an answer looks wrong.
- A 4B model gets fuzzy on specific details — dates, names, attributions —
  even when it summarises the screen correctly. There is no cloud fallback, by
  design: the answer to a question you cannot trust is to check it, not to send
  the question somewhere else.
- Images, diagrams and video frames are understood only when a screenshot is
  sent (`vision.enabled`): ask it to look, or let it decide the question is a
  visual one.
- Conversation memory holds the last turns (`memory.max_turns`), question and
  answer text only, and is not kept across restarts. What is kept on purpose —
  liked replies, the facts file, the activity log — is under "Your data" below.

## Talking to it

Hold **`Ctrl+Shift+A`**, speak, release. The question is transcribed and asked.

Hold-to-talk rather than always-listening, deliberately. The microphone is open
only while your finger is on the key — no wake word, no continuous recording,
and Windows' own microphone indicator will confirm it. Between utterances the
companion holds no microphone handle at all.

Pressing the talk key also **stops any answer in progress**, so interrupting and
asking something new are the same gesture — and the companion is never speaking
while the mic is open, so it can't transcribe itself.

Measured on this machine, CPU only:

| model | per utterance |
|---|---|
| `tiny.en` | 244 ms |
| **`base.en`** (default) | **453 ms** |
| `distil-small.en` | 1460 ms |
| `small.en` | 1442 ms |

Those are per *utterance*, not per second — Whisper pads to a 30-second window,
so a two-word question costs the same as a long one. Model size is the only real
lever. Speech recognition uses **no VRAM**; the GPU path was measured needing
cuBLAS/cuDNN libraries that aren't installed, for a saving that doesn't justify
~1 GB of NVIDIA runtime packages.

### Speaking to it in another language

Just speak it — there's no switch to flip. `speech.model` is multilingual and
`speech.language: null` auto-detects. Measured on short Turkish commands:
detected correctly **4/4**, with accurate transcription, and English still
detected correctly alongside it.

The reply comes back in the language you asked in, spoken by a voice that can
pronounce it — the voice is chosen from `voice.voices_by_language` using the
language recognition already detected, so no guessing is involved. A voice
that isn't installed is simply ignored and the default is kept.

Add a language by downloading its voice and adding a line:

```bash
.venv\Scripts\python.exe -m piper.download_voices de_DE-thorsten-medium --data-dir data/voices
```

If auto-detect ever picks wrong, set `speech.language` explicitly (`"en"`,
`"tr"`) — telling it costs a little less accuracy than guessing.

Test the microphone on its own:

```bash
.venv\Scripts\python.exe main.py --listen 5
```

It reports what it captured, the peak level, and what it heard — so a dead mic
or a wrong input device is obvious rather than mysterious.

**Given silence, Whisper does not return nothing.** It returns "You", or "Thank
you", or whatever it heard most over quiet audio in training. Audio quieter than
`speech.silence_rms` therefore never reaches the model, and a short list of
known filler phrases is discarded as a backstop. If a noisy room produces
phantom questions, raise `silence_rms`; if quiet speech gets ignored, lower it.

## Voice

Answers are spoken as they generate, not after they finish. Tokens accumulate
only until a sentence boundary; that sentence is synthesised and queued while
the model is still writing the next one. So the first audio arrives with the
first sentence rather than after the last.

Measured on the reference machine, with the model already resident:

| | |
|---|---|
| first token | 198 ms |
| **first audio** | **1.24 s** |
| Piper synthesis speed | ~15× real time, on CPU |
| VRAM cost | **none** — Piper never touches the GPU |

`Esc` interrupts: the audio queue is cleared within one buffer (a few
milliseconds), so talking over it feels immediate rather than "finishes the
sentence first". Anything already said is still remembered.

Test the voice on its own, without the model:

```bash
.venv\Scripts\python.exe main.py --say "Testing, one two three."
```

**A missing voice or audio device is not fatal.** The window says so once and
carries on silently — voice is an enhancement, not a dependency.

### Changing the voice

Voices are ~60 MB each. Browse samples at
[rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples), then:

```bash
.venv\Scripts\python.exe -m piper.download_voices en_US-amy-medium --data-dir data/voices
```

Set `voice.voice` in `config.yaml` to the name you downloaded. `voice.speed`
adjusts pace, and `voice.device` picks an output (see `--list-audio`).

## Hearing what you're watching

**Off by default.** Reading the screen is something you asked for; continuously
transcribing everything audible — calls, music, whatever is playing in another
tab — is a bigger step, so it's opt-in. Turn it on with `audio.enabled: true`.

Once on, it taps the **output** device, so it hears exactly what you hear and
nothing from your microphone. That means it understands a video rather than
only reading its subtitles — which are often absent, delayed, or abbreviated.

```bash
.venv\Scripts\python.exe main.py --hear 30
```

Play a video while that runs; it prints the transcript as it accumulates, plus
what it cost.

Measured, while sound is playing:

| | |
|---|---|
| capture | ~2.6% of a core |
| transcription (`base.en`, 15 s chunks) | ~5.4% of a core |
| silence | capture only — quiet chunks never reach the model |

Nothing is written to disk. The transcript lives in memory and dies with the
process.

**It ignores its own voice.** Loopback taps the output device, so it would
otherwise hear Piper reading an answer and transcribe that as if it were the
video — its own words then becoming "what was heard" for the next question. Any
chunk recorded while the companion was speaking is discarded whole.

**Known limit:** audio is transcribed in chunks, so a word landing exactly on a
boundary can be clipped ("photosynthesis" → "phot- to synthesis"). At the
default 15-second chunks that's roughly one word a minute, which is harmless for
understanding but not a verbatim record. Shorter `chunk_seconds` reduces lag and
increases clipping.

### Video in another language

It works without being told anything: `audio.model` is multilingual and
`audio.language: null` auto-detects. Play a Spanish or Japanese video and ask
what was said — the answer comes back in your language, and you can ask for any
other ("say that in German").

`audio.task: "translate"` makes Whisper output English directly instead. It's
one step, but **much worse**, and the default is `transcribe` because of a
measurement rather than a preference. On the same Turkish sentence:

| | |
|---|---|
| Whisper `base` translate | returned the Turkish untranslated |
| Whisper `small` translate | "an ancient Greek **knowledge**… **guess the subjects** of the stars" |
| transcribe → the chat model | "an ancient Greek computer… predict the positions of stars" ✓ |

Transcribing is an easier job than translating, and a chat model is a far
better translator than a 74M speech model. Going through the model already loaded on
your GPU also keeps the original wording, so "what did they say *exactly*" still
works.

**Asking for a third language: ask conversationally.** Translating *into your
language* is reliable. Going Turkish → German in one shot is not — measured at
1 attempt in 3, the rest echoing the source untranslated. Asking in two steps
fixes it completely:

| | |
|---|---|
| "translate this Turkish into German" | 1/3 |
| "what does this mean?" → "now say that in German" | **3/3** |

The English answer gives the model a pivot to work from. That's also how you'd
naturally ask, so it isn't much of a workaround.

**Japanese, Chinese and Korean: expect the gist, not the words.** These are
where local Whisper struggles most, and it's a hardware ceiling rather than a
setting. Measured on a Chinese sentence:

| model | characters recovered | time for 5.7 s of audio |
|---|---|---|
| `base` | 22/27 | 2.5 s |
| `small` | 24/27 | 4.5 s |
| `medium` | 24/27 | **154 s** — unusable on CPU |

Even `small` gets words wrong — 古希**拉** for 古希**腊**, 日**时** for 日**食**.
Summarising survives that; asking for a word-by-word translation does not,
because the words genuinely aren't in the transcript. `audio.model: "small"`
helps a little for ~3× the CPU. Doing better needs Whisper on the GPU, which
currently competes with the language model for VRAM.

## Things it can do

Beyond answering, it can act. The model decides when a tool fits; you just ask.

| | |
|---|---|
| **Timers** | "set a timer for 20 minutes for the pasta", "what timers are running", "cancel the pasta timer" |
| **Stopwatch** | "start a stopwatch", "how long has it been" |
| **Notes** | "note that the mechanism has 30 gears", "what have I noted", "search my notes for orrery" |
| **Clock** | "what time is it" |

**Everything here is local.** Timers live in `data/timers.json` and survive a
restart; notes are plain Markdown in `data/notes.md` that you can open in any
editor, including long after this project is abandoned. **No tool touches the
network.**

### Notes and Obsidian

`data/notes.md` is UTF-8 Markdown, one timestamped bullet per note — already
what Obsidian expects. To write straight into a vault, point `tools.notes_file`
at an absolute path; missing folders are created:

```yaml
tools:
  notes_file: "D:/Obsidian/MyVault/Companion Notes.md"
```

Notes are only ever written when you ask for one. Nothing is captured
automatically — not the screen, not the conversation.

A finished timer announces itself out loud, shows a tray notification, and
un-hides the window — but waits if the companion is mid-answer, since an alarm
talking over a reply is worse than one a few seconds late.

## Conversation memory

Follow-ups work because recent turns are replayed to the model. Only the
questions and answers are kept, never the screen text they were based on: that
text is stale by the next question, and re-sending it would crowd out the
*current* screen in an 8k context.

`clear` in the window (or the tray menu) forgets the conversation.

## If the hotkey doesn't work

`Ctrl+Shift+Space` is the default because `Ctrl+Alt+Space` is commonly taken
already. If another app owns the combination, the companion says so on
startup and stays fully usable from the tray icon — pick a different
`ui.hotkey` in `config.yaml` and restart.

## Remarks it makes on its own

Left alone, the companion occasionally says something about what you are doing.
Code decides whether a page is worth a remark and which kind it should be; the
model only writes the sentence, and what comes back is dropped if it advises,
narrates the obvious, repeats itself or reads the page back at you.

The `proactive` block in `config.yaml` sets how often at most, how long it stays
quiet after you interrupt it, and what it is allowed to remark on. The settings
page's **Remarks** tab has the same controls. Mute, in the window or the tray menu,
stops remarks without stopping anything else, and "Say something about this
page" — window button, tray item or hotkey — asks for one on demand.

Each remark carries the reason it was made. The reason is shown next to it and
never spoken — hover or expand to see what prompted it.

Where remarks are welcome is learned as you use it: a thumbs down, or stopping a
remark, counts against that site, kind and moment; a thumbs up counts for it.

## Rating what it says

The 👍 and 👎 beside an answer or a remark are stored in `data/ratings.jsonl` and
used two ways: they teach the learning above where to speak, and a liked reply
is remembered as a *shared moment* — brought back later when it bears on what
you are doing, matched by meaning rather than by shared words.

`export_liked.bat` writes every liked answer and remark to `data/exports/` as
review-ready Markdown. Nothing is trained and nothing is uploaded; it is there so
the material is yours to look at.

## The settings page

The gear icon opens a page with a tab per topic — General, Remarks, Screen,
Voice, Avatar, AI model, Your data. It edits the common options and writes only
what you changed to `data/settings.yaml`, layered over `config.yaml` at startup,
so the documented defaults stay readable and your choices survive an update.

Options not on the page live in `config.yaml`, with a comment on each saying what
it does and, where it was a close call, why it is set that way.

## The avatar

`avatar.enabled: true` puts a Live2D model on the desktop: its mouth follows the
speech sounds, its eyes follow the pointer, and its expression is chosen in code
from the message and the reply. Models live in `avatar.folder` (`data/avatars`
by default), each in its own folder with a `.model3.json` file, and `avatar.model`
picks one — **no model files are included**; use one you own or one licensed for
the purpose.

Drag it to move it, or use the configured shortcut. Hidden, it gives its place
back to the chat window. Without a model configured, the app runs normally with
the window alone.

## What is playing

With `music.enabled` on (the default) the companion estimates the key, tempo and chords of
whatever your speakers are playing, using numpy alone — no librosa, no extra
hundreds of megabytes. Ask "what key is this in" and the analysis is what it
answers from, rather than guessing from a song title.

## Your data

Everything the app records about its own use lives in `companion/data/`:

| file | what it holds |
|---|---|
| `settings.yaml` | the options you changed on the settings page |
| `ratings.jsonl` | 👍 / 👎 on answers and remarks |
| `about_you.md` | a few lasting facts, written by the daily reflection, editable by hand |
| `activity/` | a readable monthly CSV of the pages you spent time on |
| `notes.md`, `timers.json` | what you asked it to note and time |
| `prompts/` | exactly what was sent to the model, when `llm.log_prompts` is on |
| `exports/` | liked replies exported for review |
| `voices/`, `avatars/` | voices and avatar models you downloaded |

None of it is sent anywhere. The **Your data** tab of the settings page resets
any of it, and a reset moves the files to `data/archive/` rather than deleting
them, so a reset is undoable.
