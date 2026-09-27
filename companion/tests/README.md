# Tests

Regression checks for the whole app. Most of the bugs here were found by hand, once; these suites
are how they stay found.

## Running them

```bash
.venv\Scripts\python.exe tests\run_all.py --fast
```

48 suites, about a minute, no Ollama, no audio device and no downloaded model needed. This is the
one to run while changing things.

```bash
.venv\Scripts\python.exe tests\run_all.py
```

All 85, several minutes. Needs Ollama running, speakers, a microphone, a Piper voice
(`.venv\Scripts\python.exe -m piper.download_voices en_US-lessac-medium --data-dir data/voices`),
and **the companion itself closed** — the running app owns the global hotkeys, and two suites cannot register them while it
does. A few suites play speech through the speakers.

`-k voice` runs only the suites whose name matches. `COMPANION_TEST_MODEL=<model>` runs them
against another model without editing `config.yaml`.

## What each one needs

`run_all.py` is the authoritative list: every suite is registered there with what it requires
beyond Python, and `--fast` runs those that require nothing.

| requirement | meaning |
|---|---|
| — | pure logic and fakes; always runnable |
| `model` | Ollama running with the configured model pulled |
| `sttmodel` | the Whisper model (downloads on first use) |
| `voice` | a Piper voice in `data/voices`; two suites synthesise their own audio with `en_US-lessac-medium` |
| `audio` | a working output device, and a microphone for some checks |
| `display` | Qt; fine on any desktop, not on a headless machine |
| `hotkey` | the configured hotkeys must be free, so quit the app first |
| `screen` | reads the live desktop, so the result depends on what is on it |

## What they cover

**Reading the screen** — `test_privacy`, `test_selection`, `test_fallback`, `test_ambient`,
`test_cache`, `test_screen_sources`, `test_vision`, `test_vision_e2e`.

**Talking to the model** — `test_memory_hotkeys`, `test_screen_relevance`, `test_modular_prompt`,
`test_reply_cap`, `test_answer_length`, `test_now`, `test_followup`, and the `_e2e` counterparts
that run the same thing against the real model.

**Remarks** — `test_orchestrator`, `test_attention`, `test_learning`, `test_moments`,
`test_remark_reason`, `test_remark_now`, `test_remark_copy`, `test_remark_memory`,
`test_user_activity`, `test_audio_hold`, `test_proactive_e2e`.

**Memory and data** — `test_ratings`, `test_relationship`, `test_shared_moments`,
`test_moments_meaning`, `test_activity`, `test_reflection`, `test_export`, `test_reset`.

**Voice, speech and audio** — `test_voice`, `test_voice_language`, `test_speech`, `test_echo`,
`test_mic_toggle`, `test_hands_free`, `test_microphone_choice`, `test_multilingual`,
`test_language_lock`, `test_pinned_language`, `test_audio`, `test_translation`, `test_interruption`.

**The window and the avatar** — `test_transcript`, `test_escape`, `test_settings`,
`test_settings_page`, `test_button_icons`, `test_avatar`, `test_avatar_hidden`, `test_avatar_move`,
`test_avatar_window_place`, `test_visemes`, `test_mood`, `test_gui`.

**Tools and music** — `test_tools`, `test_tool_requests`, `test_tools_e2e`, `test_tool_grounding`,
`test_music`, `test_music_e2e`.

Several suites are regressions for bugs found in use rather than in testing: Esc interrupting
speech instead of hiding the window (`test_escape`), the companion transcribing its own voice off
the loopback (`test_echo`), a tool's list padded with text from the screen (`test_tool_grounding`),
questions going unanswered after an interruption (`test_cancel_leak`), and a blocked remark being
thrown away instead of waiting (`test_orchestrator`).

## Conventions

- Each suite is a plain script: run it directly, exit code 0 means pass. No test framework, because
  adding one would be the only dependency here that is not already needed to run the app.
- `helpers.py` puts the companion package on `sys.path` and provides `CONFIG_PATH`, `VOICES_DIR`
  and `FIXTURE_IMAGE`. No suite contains an absolute path.
- `fixtures/article.png` is a rendered encyclopedia-style page. Suites that need fixed screen
  content replay it through `--image` rather than depending on what happens to be on the monitor.
  Regenerate with `python tests/fixtures/make_article.py out.png`.
- Tests that would otherwise need a human speaking use Piper to generate the audio, so the expected
  transcript is known exactly.
- Timing logic takes an injectable clock, so suites advance time rather than sleeping.
- **No suite touches real notes or timers.** `helpers.py` points every config a suite loads at a
  temporary folder, because a model given real tools will use them: "remember the word ZEPHYRINE"
  became a note in a real notebook, again and again, until a later test read one back.
- A new check has to be able to fail. Run it against the unchanged code — a `git worktree` at the
  previous commit does that without touching the working tree — before trusting a PASS.
- Checks on model output are judged over several samples, and a check that passes only most of the
  time is treated as broken rather than flaky.
