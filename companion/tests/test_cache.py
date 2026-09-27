"""Verify the ambient cache: a settled screen is pre-read, so a question about
it answers with no perception delay. Also that privacy still gates the cache."""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.ambient import Verdict
from core.companion import build_companion
from core.config import AppConfig
from core.errors import PrivacyBlocked
from core.logging import setup_logging
from core.privacy import PrivacyGuard

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


comp = build_companion(cfg)
check("ambient observer built", comp.ambient is not None)

# Drive the watch loop until it performs a real read, as if the screen had just
# settled after you opened a page.
context = None
for _ in range(30):
    context = comp.ambient_tick()
    if context is not None:
        break
    time.sleep(cfg.ambient.sample_interval_s / 2)

check("ambient loop performed a read", context is not None,
      f"source={context.source if context else None}")
check("cache populated", comp.last_context is not None)
if context:
    print(f"      read {len(context.text)} chars via {context.source}")

# A question now should reuse that read rather than perceiving again.
start = time.perf_counter()
cached = comp.observe(max_age_s=cfg.ambient.max_cache_age_s)
cached_ms = (time.perf_counter() - start) * 1000

start = time.perf_counter()
fresh = comp.refresh()
fresh_ms = (time.perf_counter() - start) * 1000

print(f"      cached observe: {cached_ms:.1f} ms   full refresh: {fresh_ms:.1f} ms")
check("cached answer returns the same context object", cached is context)
check("cached path is much faster than perceiving",
      cached_ms < fresh_ms / 4, f"{cached_ms:.1f} vs {fresh_ms:.1f} ms")
check("cached path is effectively instant", cached_ms < 30, f"{cached_ms:.1f} ms")

# max_age_s=0 must always perceive afresh.
start = time.perf_counter()
comp.observe(max_age_s=0)
zero_ms = (time.perf_counter() - start) * 1000
check("max_age_s=0 forces a fresh read", zero_ms > cached_ms * 3,
      f"{zero_ms:.1f} ms")

# An expired cache must not be served.
comp.last_context.captured_at = time.time() - 999
start = time.perf_counter()
comp.observe(max_age_s=cfg.ambient.max_cache_age_s)
stale_ms = (time.perf_counter() - start) * 1000
check("stale cache is refused", stale_ms > cached_ms * 3, f"{stale_ms:.1f} ms")

# Privacy must gate cached answers too, and drop what was cached.
comp.privacy = PrivacyGuard(enabled=True, blocked_title_patterns=[".*"])
blocked = False
try:
    comp.observe(max_age_s=cfg.ambient.max_cache_age_s)
except PrivacyBlocked:
    blocked = True
check("blocked window refuses even a cached answer", blocked)
check("cache is dropped when blocked", comp.last_context is None)
check("ambient tick stays quiet while blocked", comp.ambient_tick() is None)

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
