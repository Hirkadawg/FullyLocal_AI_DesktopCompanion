"""Tests for window selection: largest share of the monitor wins.

Uses synthetic windows so results don't depend on the current desktop layout.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import core.winapi as winapi
from core.logging import setup_logging
from core.types import WindowInfo

setup_logging("ERROR")

MONITOR = (0, 0, 2560, 1440)
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def w(title, process, rect, fg=False, hwnd=1):
    return WindowInfo(title=title, process=process, rect=rect,
                      is_foreground=fg, hwnd=hwnd)


def with_windows(windows, classes=None, console=0):
    """Patch winapi's environment lookups so selection can be tested offline."""
    classes = classes or {}
    winapi.visible_windows = lambda within=None: list(windows)
    winapi.window_class = lambda hwnd: classes.get(hwnd, "AppWindow")
    winapi.own_console_window = lambda: console


def pick(windows, classes=None, console=0, ignore=()):
    with_windows(windows, classes, console)
    return winapi.pick_target_window(MONITOR, ignore_processes=ignore)


# 1. The window dominating the monitor wins even though another has focus.
#    This is the exact bug reported: focus sat on a sliver from monitor 2.
sliver = w("Notes", "notes.exe", (2552, 177, 4488, 1233), fg=True, hwnd=1)
full = w("Wikipedia - Brave", "brave.exe", (-8, -8, 2568, 1408), hwnd=2)
got = pick([sliver, full])
check("focused sliver loses to the dominant window", got is not None and got.hwnd == 2,
      f"got {got.process if got else None}")

# 2. Bigger coverage beats smaller, regardless of Z-order.
small = w("Notes", "notepad.exe", (0, 0, 600, 400), hwnd=3)
big = w("Wikipedia", "brave.exe", (0, 0, 2560, 1440), hwnd=4)
got = pick([small, big])  # small is topmost
check("larger coverage beats topmost-but-smaller", got.hwnd == 4, f"got {got.title!r}")

# 3. Exact tie (two maximised windows) breaks to the front-most.
top = w("Front", "brave.exe", (0, 0, 2560, 1440), hwnd=5)
back = w("Behind", "notes.exe", (0, 0, 2560, 1440), hwnd=6)
got = pick([top, back])
check("tie breaks to the front-most window", got.hwnd == 5, f"got {got.title!r}")

# 4. The desktop spans everything but must never be chosen.
desktop = w("Program Manager", "explorer.exe", (0, 0, 4480, 1440), hwnd=7)
got = pick([desktop, full], classes={7: "Progman"})
check("desktop is excluded", got.hwnd == 2, f"got {got.process}")

# 5. A full-screen overlay above everything must never be chosen.
overlay = w("NVIDIA GeForce Overlay", "nvidia overlay.exe", (0, 0, 2559, 1440), hwnd=8)
got = pick([overlay, full], ignore=["nvidia overlay.exe"])
check("overlay is excluded", got.hwnd == 2, f"got {got.process}")

# 6. The companion's own console is never read.
console = w("cmd", "cmd.exe", (0, 0, 2560, 1440), hwnd=9)
got = pick([console, full], console=9)
check("own console is excluded", got.hwnd == 2, f"got {got.process}")

# 7. Nothing viable -> None rather than a wrong guess.
got = pick([desktop], classes={7: "Progman"})
check("no viable window returns None", got is None)

# 8. Sliver threshold: a window 40% on this monitor is rejected at min 0.5.
with_windows([w("Half out", "x.exe", (-1600, 0, 960, 1440), hwnd=10)])
got = winapi.pick_target_window(MONITOR, min_on_monitor=0.5)
check("window mostly off-monitor is rejected", got is None)
got = winapi.pick_target_window(MONITOR, min_on_monitor=0.3)
check("...but accepted when the threshold is lowered", got is not None)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
