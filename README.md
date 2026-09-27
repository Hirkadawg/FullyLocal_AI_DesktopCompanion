# Local AI Desktop Companion

A desktop companion that reads what is on your screen and talks with you about it — typed or
spoken, in English or Turkish — and now and then says something of its own accord. It has a voice,
a Live2D avatar, timers and notes, and it can hear what your speakers are playing.

**Everything runs on the machine it is installed on.** The model is a local one through
[Ollama](https://ollama.com); no screen contents, audio or text leave the computer, and the app
makes no network call of its own — there is no account, no telemetry and no cloud fallback.

## What it does

- **Answers about the screen.** Text comes from the application's own accessibility tree where
  possible (exact, ~40 ms) and from OCR where not. A screenshot goes to the model only when the
  question is visual, the screen is short on text, or you ask it to look.
- **Speaks and listens.** Answers are spoken as they generate (Piper, on the CPU); push-to-talk
  and an always-on microphone mode both transcribe locally (faster-whisper).
- **Says things unprompted.** Code decides when a page is worth a remark and which kind of remark
  it is; the model only writes it, and every remark can show why it was made.
- **Remembers.** The conversation, a few lasting facts it keeps about you in a file you can edit,
  and the replies you gave a thumbs up — brought back when they bear on what you are doing.
- **Does small things.** Countdown timers, a stopwatch, the clock, notes, and an estimate of the
  key, tempo and chords of whatever is playing.
- **Has a face.** A Live2D avatar on the desktop whose mouth follows the speech sounds and whose
  eyes follow the pointer. You supply the model files.
- **Is configurable.** A settings page covers the common options; `config.yaml` holds the rest,
  with the reasoning in comments.

## Requirements

| | |
|---|---|
| Operating system | Windows 10 or 11 |
| Python | 3.13 — the version it is developed and tested on |
| GPU | 8 GB of VRAM is enough — the default model sits in 3.3 GB |
| [Ollama](https://ollama.com) | 0.17.1 or newer, running locally |
| Disk | ~4 GB of models, ~800 MB of Python packages |

Windows-only as it stands: screen text uses UI Automation and hearing the speakers uses WASAPI
loopback. Everything else (Qt, Ollama, Piper, faster-whisper) is cross-platform, so a port is
mostly a matter of replacing those two.

## Install

```bash
git clone https://github.com/Hirkadawg/FullyLocal_AI_DesktopCompanion.git
cd FullyLocal_AI_DesktopCompanion/companion

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

ollama pull qwen3.5:4b        # chat and vision, 3.3 GB
ollama pull embeddinggemma    # matches remembered replies by meaning, 621 MB

.venv\Scripts\python.exe -m piper.download_voices en_GB-alba-medium --data-dir data/voices
```

That last command fetches the speaking voice (~60 MB); add `tr_TR-dfki-medium` the same way for
Turkish, or any other voice from [the Piper samples](https://rhasspy.github.io/piper-samples) —
set `voice.voice` in `config.yaml` to whichever you downloaded. The speech-recognition model
downloads itself the first time you talk to it. A missing voice is not fatal: the app says so once
and carries on in text. Avatar files are optional and not included; see
[the guide](docs/GUIDE.md).

## Run

```bash
.venv\Scripts\python.exe main.py --gui     # the window, tray icon and hotkeys
.venv\Scripts\python.exe main.py           # a terminal loop
```

Or double-click `run_gui.bat` (windowed) or `run.bat` (terminal) in `companion\`.

The window docks to the corner of the monitor named by `capture.monitor_index`. `Ctrl+Shift+Space`
shows and hides it from anywhere, `Ctrl+Shift+A` is push-to-talk, and `Esc` stops an answer
mid-sentence. Closing the window hides it to the tray; quit from the tray icon.

Worth running once after installing, to confirm it is reading the screen you think it is:

```bash
.venv\Scripts\python.exe main.py --list-monitors   # which monitor is which
.venv\Scripts\python.exe main.py --probe           # window choice and each perception source
```

## Configuration

`companion/config.yaml` is the single configuration file; every option has a comment saying what
it is for and, where it was a close call, why it is set the way it is. The settings page in the
app (gear icon) edits the common ones and writes your changes to `companion/data/settings.yaml`,
which overrides the defaults and survives updates.

Everything the app records about its own use — ratings, the activity log, the facts file, notes,
timers, saved prompts, exports — stays in `companion/data/`. Nothing there is ever sent anywhere,
and the "Your data" tab of the settings page resets any of it (resets move files to
`data/archive/` rather than deleting them).

## Privacy

- No network call is made by the app itself. Ollama is reached at `localhost`; downloads of models
  and voices are explicit commands you run.
- The screen is captured only to answer a question or to take an ambient reading, and screenshots
  are never written to disk unless you ask for one with `--shot`.
- A capture is refused outright while a window matching the privacy ignore-list is visible
  anywhere on the target monitor — password managers, `seed phrase`, `incognito` and so on. The
  check runs before the capture, so blocked content is never read in the first place.
- The microphone is open only while push-to-talk is held, or while hands-free listening is
  switched on. Hearing what the speakers play is off by default.

## Tests

Plain scripts, no test framework: each one exits 0 when it passes.

```bash
.venv\Scripts\python.exe tests\run_all.py --fast   # 48 suites, ~1 min, nothing external needed
.venv\Scripts\python.exe tests\run_all.py          # all 85; needs Ollama, audio, and the app closed
```

Suites ending in `_e2e` run against the real model and judge behaviour over a batch of answers,
because a single reply proves nothing at temperature. See
[companion/tests/README.md](companion/tests/README.md).

## Layout

```
companion/
  main.py           the CLI, the diagnostics, and the entry point for the GUI
  config.yaml       every option, with its reasoning in comments
  core/             what the app is: perception, prompts, remarks, memory, tools, settings
  modules/          what it talks to: capture, perception, llm, voice, audio, ui, tools
  prompts/          the system prompt, and the personality file you can rewrite
  assets/           icons
  tests/            85 suites and their fixtures
  data/             everything it records about its use (created at run time, never published)
docs/
  GUIDE.md          using it: commands, settings, voices, languages, diagnostics
  ARCHITECTURE.md   how it is put together, and why it is put together that way
```

## Design notes

Three decisions shape most of the behaviour, and each replaced an earlier approach that measured
worse:

- **Code decides, the model writes.** Whether to remark, what kind of remark, how long an answer
  should be, which tools are offered — all of it is decided in Python. The model is asked only to
  put it into words.
- **Instructions go to the turn that needs them.** The system prompt is split at its headings and
  only the relevant sections are sent; per-turn notes carry the rest. Standing rules for one kind
  of message reliably broke unrelated replies.
- **The screen goes with the message only when the message is about the screen.** Attaching it to
  everything pulled ordinary conversation onto whatever happened to be open.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the longer version.

## Scope and limits

- Windows only, as described under Requirements.
- The avatar needs Live2D model files you supply; without them the app runs with the window alone.
- `companion/prompts/remarks.md` holds the personality: a small placeholder persona meant to be
  rewritten for whoever is using it.
- Answers are as good as a 4B model on your own hardware — fluent about what is on screen, fuzzy
  on specific dates, names and attributions.
- Speech recognition handles English and Turkish well, European languages acceptably, and
  Japanese, Chinese and Korean only to the gist.

## Licences

This project is under the MIT licence — see [LICENSE](LICENSE). Third-party work keeps its own
terms, including:

- icons from [Lucide](https://lucide.dev) (ISC, `companion/assets/icons/lucide/LICENSE`);
- `live2d-py` (MIT), which bundles the Live2D Cubism Core — free for private use, with its own
  terms for anything beyond that;
- the models pulled through Ollama: `qwen3.5:4b` (Apache 2.0) and `embeddinggemma` (Gemma terms);
- Piper voices and faster-whisper models, each under the licence of the voice or model you pull.
