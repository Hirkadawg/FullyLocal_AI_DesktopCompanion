"""Run the test suites and summarise.

Some suites need things that aren't always available -- Ollama running, an
audio device, a live desktop, free hotkeys. Rather than fail confusingly when
one is missing, each suite declares what it needs, and `--fast` runs only the
ones that need nothing.

    python tests/run_all.py --fast     pure logic, seconds, no Ollama needed
    python tests/run_all.py            everything
    python tests/run_all.py -k voice   only suites matching "voice"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

#: What each suite needs beyond Python.
#:   model    -- Ollama running with the configured model pulled
#:   sttmodel -- the Whisper model (downloads on first use)
#:   voice    -- a Piper voice in data/voices (the suites synthesise with
#:               en_US-lessac-medium, which is not one the app ships with)
#:   audio    -- a working output device, and a microphone for some checks
#:   display  -- Qt; fine on any desktop, not on a headless machine
#:   hotkey   -- the configured hotkeys must be free, so quit the app first
#:   screen   -- reads the live desktop, so results depend on what's on it
SUITES: list[tuple[str, set[str]]] = [
    ("test_privacy.py", set()),
    ("test_selection.py", set()),
    ("test_fallback.py", set()),
    ("test_ambient.py", set()),
    ("test_memory_hotkeys.py", set()),
    ("test_voice.py", set()),
    ("test_tools.py", set()),
    ("test_tool_requests.py", set()),
    ("test_tools_e2e.py", {"model"}),
    ("test_tool_grounding.py", {"model"}),
    ("test_transcript.py", {"display"}),
    ("test_cache.py", {"screen"}),
    ("test_screen_sources.py", {"screen"}),
    ("test_escape.py", {"display", "hotkey"}),
    ("test_speech.py", {"audio", "sttmodel"}),
    ("test_audio.py", {"audio", "sttmodel"}),
    ("test_audio_e2e.py", {"audio", "sttmodel", "model"}),
    ("test_translation.py", {"sttmodel", "model"}),
    ("test_multilingual.py", {"sttmodel"}),
    ("test_language_lock.py", {"sttmodel"}),
    ("test_echo.py", set()),
    ("test_attention.py", set()),
    ("test_orchestrator.py", set()),
    ("test_remark_memory.py", set()),
    ("test_user_activity.py", set()),
    ("test_audio_hold.py", set()),
    ("test_interruption.py", set()),
    ("test_remark_reason.py", set()),
    ("test_remark_now.py", set()),
    ("test_ratings.py", set()),
    ("test_mic_toggle.py", set()),
    ("test_hands_free.py", set()),
    ("test_settings.py", set()),
    ("test_settings_page.py", set()),
    ("test_button_icons.py", set()),
    ("test_relationship.py", set()),
    ("test_shared_moments.py", set()),
    ("test_reply_cap.py", set()),
    ("test_screen_relevance.py", set()),
    ("test_modular_prompt.py", set()),
    ("test_remark_copy.py", set()),
    ("test_moments_meaning.py", set()),
    ("test_vision.py", set()),
    ("test_vision_e2e.py", {"model"}),
    ("test_activity.py", set()),
    ("test_moments.py", set()),
    ("test_reflection.py", set()),
    ("test_learning.py", set()),
    ("test_export.py", set()),
    ("test_reset.py", set()),
    ("test_music.py", {"voice"}),
    ("test_avatar.py", set()),
    ("test_visemes.py", {"voice"}),
    ("test_voice_language.py", set()),
    ("test_mood.py", set()),
    ("test_answer_length.py", set()),
    ("test_now.py", set()),
    ("test_microphone_choice.py", set()),
    ("test_pinned_language.py", set()),
    ("test_look_and_watch.py", set()),
    ("test_avatar_hidden.py", set()),
    ("test_avatar_window_place.py", set()),
    ("test_avatar_move.py", set()),
    ("test_avatar_live.py", {"display"}),
    ("test_microphone_live.py", {"audio"}),
    ("test_reflection_e2e.py", {"model"}),
    ("test_activity_e2e.py", {"model"}),
    ("test_music_e2e.py", {"model"}),
    ("test_answer_length_e2e.py", {"model"}),
    ("test_now_e2e.py", {"model"}),
    ("test_pinned_language_e2e.py", {"model"}),
    ("test_look_and_watch_e2e.py", {"model"}),
    ("test_notes_stacked_e2e.py", {"model"}),
    ("test_shared_moments_e2e.py", {"model"}),
    ("test_reply_cap_e2e.py", {"model"}),
    ("test_screen_relevance_e2e.py", {"model"}),
    ("test_modular_prompt_e2e.py", {"model"}),
    ("test_moments_meaning_e2e.py", {"model"}),
    ("test_hands_free_e2e.py", {"sttmodel"}),
    ("test_proactive_e2e.py", {"model"}),
    ("test_followup.py", {"model"}),
    ("test_interruption_e2e.py", {"model"}),
    ("test_voice_e2e.py", {"model", "audio"}),
    ("test_cancel_leak.py", {"model", "display"}),
    ("test_gui.py", {"model", "display", "hotkey"}),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the companion test suites")
    parser.add_argument(
        "--fast", action="store_true",
        help="only suites needing no model, audio, display or live screen",
    )
    parser.add_argument("-k", metavar="TEXT", help="only suites whose name contains TEXT")
    args = parser.parse_args()

    selected = [
        (name, needs)
        for name, needs in SUITES
        if not (args.fast and needs) and (not args.k or args.k in name)
    ]
    if not selected:
        print("no suites matched")
        return 1

    print(f"\n  running {len(selected)} suite(s) with {sys.executable}\n")
    results: list[tuple[str, bool, float, str]] = []

    for name, needs in selected:
        path = TESTS_DIR / name
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(path)],
            # Suites write UTF-8 (helpers.py); decode it as such, or the
            # locale's cp1252 mangles Turkish and can fail outright.
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(TESTS_DIR),
        )
        elapsed = time.perf_counter() - started
        ok = proc.returncode == 0

        summary = ""
        for line in reversed((proc.stdout or "").splitlines()):
            if "failure(s)" in line:
                summary = line.strip()
                break
        if not ok and not summary:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            summary = tail[-1][:70] if tail else f"exit {proc.returncode}"

        needs_note = f"[{','.join(sorted(needs))}]" if needs else ""
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {elapsed:6.1f}s  "
              f"{summary} {needs_note}")
        results.append((name, ok, elapsed, proc.stdout + proc.stderr))

    failed = [r for r in results if not r[1]]
    total = sum(r[2] for r in results)
    print(f"\n  {len(results) - len(failed)}/{len(results)} suites passed "
          f"in {total:.0f}s")

    for name, _ok, _elapsed, output in failed:
        print(f"\n--- {name} ---")
        for line in output.splitlines():
            if "FAIL" in line or "Error" in line or "error" in line:
                print(f"    {line.strip()[:120]}")

    if failed:
        print("\n  If a failure mentions a hotkey already registered, quit the")
        print("  companion first -- the running app owns those combinations.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

