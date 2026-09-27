"""Deterministic tests for the perception fallback chain, using fake sources."""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.logging import setup_logging
from core.types import ScreenContext
from modules.perception.base import PerceptionSource
from modules.perception.composite import FallbackPerception

setup_logging("ERROR")

calls: list[str] = []


class Fake(PerceptionSource):
    def __init__(self, name: str, text: str | None):
        self.name = name
        self.text = text  # None means "raise"

    def read(self) -> ScreenContext:
        calls.append(self.name)
        if self.text is None:
            raise RuntimeError("source exploded")
        return ScreenContext(text=self.text, source=self.name)


GOOD = "x" * 500
THIN = "tiny"
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


# 1. First source succeeds -> second must never run (the whole efficiency point)
calls.clear()
chain = FallbackPerception([Fake("uia", GOOD), Fake("ocr", GOOD)], min_chars=40)
ctx = chain.read()
check("good first source wins", ctx.source == "uia")
check("expensive source is skipped entirely", calls == ["uia"], f"calls={calls}")

# 2. First source thin -> falls through to second
calls.clear()
chain = FallbackPerception([Fake("uia", THIN), Fake("ocr", GOOD)], min_chars=40)
ctx = chain.read()
check("thin first source falls through", ctx.source == "ocr")
check("both sources were tried", calls == ["uia", "ocr"], f"calls={calls}")

# 3. First source raises -> chain survives and uses the second
calls.clear()
chain = FallbackPerception([Fake("uia", None), Fake("ocr", GOOD)], min_chars=40)
ctx = chain.read()
check("crashing source does not sink the chain", ctx.source == "ocr")

# 4. All thin -> best available is returned, not nothing
chain = FallbackPerception([Fake("uia", "ab"), Fake("ocr", "abcdefgh")], min_chars=40)
ctx = chain.read()
check("all-thin returns the longest partial", ctx.text == "abcdefgh", f"got {ctx.text!r}")

# 5. All sources fail -> empty context, no exception
chain = FallbackPerception([Fake("uia", None), Fake("ocr", None)], min_chars=40)
ctx = chain.read()
check("total failure yields empty context", ctx.text == "" and ctx.is_empty)

# 6. Timing is recorded so --probe and :timings have something to show
chain = FallbackPerception([Fake("uia", GOOD)], min_chars=40)
ctx = chain.read()
check("chain timing recorded", "chain" in ctx.timings_ms)

# 7. Empty source list is rejected loudly rather than silently doing nothing
try:
    FallbackPerception([], min_chars=40)
    check("empty source list rejected", False)
except ValueError:
    check("empty source list rejected", True)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
