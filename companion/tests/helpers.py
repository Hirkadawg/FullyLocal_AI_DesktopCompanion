"""Shared setup for the test suites.

Importing this puts the companion package on sys.path and exposes the paths the
suites need, so no test contains an absolute path to anyone's machine.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Suites print model output, which can be Turkish or Japanese. When run_all.py
# captures a suite's output, Windows gives the pipe a cp1252 encoding and the
# first "ı" crashes the suite -- test_translation did exactly that, whichever
# model was under test.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TESTS_DIR = Path(__file__).resolve().parent
COMPANION_DIR = TESTS_DIR.parent

if str(COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(COMPANION_DIR))

CONFIG_PATH = str(COMPANION_DIR / "config.yaml")
VOICES_DIR = str(COMPANION_DIR / "data" / "voices")
FIXTURE_IMAGE = str(TESTS_DIR / "fixtures" / "article.png")

#: Where every suite's notes and timers go instead of the user's own.
#:
#: A suite that builds the real app gets real tools, and the model is free to
#: use them. Asked to "remember the word ZEPHYRINE", models took a note -- into
#: the user's notebook, 19 times over a month of test runs, until a later test
#: read one back and a reply mentioned it. Two suites had their own temp
#: folders; the rest wrote to companion/data.
TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="companion-tests-"))

# Run the model-backed suites against another model without touching
# config.yaml -- how a candidate replacement gets judged on the same tests:
#     COMPANION_TEST_MODEL=qwen3-vl:8b python tests/run_all.py -k e2e
# Suites run as subprocesses of run_all.py, so the variable reaches them all.
TEST_MODEL = os.environ.get("COMPANION_TEST_MODEL") or None

from core.config import AppConfig  # noqa: E402 -- needs sys.path set above

_load = AppConfig.load.__func__


def _load_for_tests(cls, path, settings=False):
    # Never the user's settings page choices: a suite must behave the same
    # whatever they have switched on or off. `settings` is accepted and ignored.
    config = _load(cls, path, settings=False)
    config.tools.notes_file = str(TEST_DATA_DIR / "notes.md")
    config.tools.timers_file = str(TEST_DATA_DIR / "timers.json")
    config.ratings.file = str(TEST_DATA_DIR / "ratings.jsonl")
    config.activity.folder = str(TEST_DATA_DIR / "activity")
    config.reflection.file = str(TEST_DATA_DIR / "about_you.md")
    config.learning.file = str(TEST_DATA_DIR / "learning.json")
    config.archive_folder = str(TEST_DATA_DIR / "archive")
    config.llm.prompt_log_folder = str(TEST_DATA_DIR / "prompts")
    config.avatar.state_file = str(TEST_DATA_DIR / "avatar.json")
    if TEST_MODEL:
        config.ollama.model = TEST_MODEL
    return config


AppConfig.load = classmethod(_load_for_tests)


def check_factory():
    """Return (check, failures) where `failures` is a one-element list.

    Suites predate this and mostly keep their own counter; provided for new
    ones so the reporting stays consistent.
    """
    failures = [0]

    def check(label: str, condition: bool, detail: str = "") -> None:
        failures[0] += not condition
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {label}{'  ' + detail if detail else ''}")

    return check, failures
