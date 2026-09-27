"""Verify the privacy guard's matching logic against synthetic windows,
and confirm live window enumeration works (reporting counts only, not titles)."""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.config import AppConfig
from core.privacy import PrivacyGuard
from core.types import WindowInfo
from core.winapi import visible_windows
from modules.capture.screen import MSSCapture

config = AppConfig.load(CONFIG_PATH)
guard = PrivacyGuard(
    enabled=config.privacy.enabled,
    blocked_processes=config.privacy.blocked_processes,
    blocked_title_patterns=config.privacy.blocked_title_patterns,
)


def w(title: str, process: str) -> WindowInfo:
    return WindowInfo(title=title, process=process, rect=(0, 0, 100, 100))


cases = [
    ("blocked process", [w("Vault", "keepassxc.exe")], True),
    ("blocked process, mixed case", [w("Vault", "KeePassXC.exe".lower())], True),
    ("title: password manager", [w("My Passwords - Vault", "chrome.exe")], True),
    ("title: incognito", [w("New Tab - Incognito", "chrome.exe")], True),
    ("title: seed phrase", [w("Wallet seed phrase backup", "notepad.exe")], True),
    ("innocuous window", [w("Antikythera mechanism - Wikipedia", "chrome.exe")], False),
    ("innocuous editor", [w("main.py - Visual Studio Code", "code.exe")], False),
    ("blocked among many", [w("Docs", "chrome.exe"), w("Vault", "1password.exe")], True),
    ("empty list", [], False),
]

failures = 0
for label, windows, should_block in cases:
    block = guard.check(windows)
    ok = (block is not None) == should_block
    failures += not ok
    verdict = "PASS" if ok else "FAIL"
    detail = f" -> {block.reason}" if block else ""
    print(f"  [{verdict}] {label}{detail}")

disabled = PrivacyGuard(enabled=False, blocked_processes=["keepassxc.exe"])
ok = disabled.check([w("Vault", "keepassxc.exe")]) is None
failures += not ok
print(f"  [{'PASS' if ok else 'FAIL'}] guard honours enabled: false")

bad = PrivacyGuard(enabled=True, blocked_title_patterns=["valid", "[unclosed"])
ok = bad.check([w("a valid title", "x.exe")]) is not None
failures += not ok
print(f"  [{'PASS' if ok else 'FAIL'}] invalid regex is skipped, valid one still works")

screen = MSSCapture(monitor_index=config.monitor_index)
live = visible_windows(within=screen.bounds())
screen.close()
processes = sorted({win.process for win in live if win.process})
print(f"\n  live enumeration: {len(live)} window(s) on monitor {config.monitor_index}")
print(f"  processes seen: {', '.join(processes) or '(none)'}")
print(f"  guard verdict on live screen: "
      f"{'BLOCKED' if guard.check(live) else 'clear'}")

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
