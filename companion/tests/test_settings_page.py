"""The settings page, laid out for reading.

Reviewed before changing it, rendered at the dialog's size with real fonts: the
Features tab held 26 unrelated switches, 2.9 screens of scrolling with no
headings; descriptions averaged up to 103 characters and ran to 213; "(after
restart)" sat inside labels; whole seconds showed as "10,0 s"; labels ran to 54
characters; long folder paths were cut off; the window had no icon.

Now each tab is one topic with small headings. These are measured here so they
can't drift back as settings are added.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Real fonts, so heights measured offscreen match the screen.
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from core.reset import RESETS
from core.settings import SECTIONS, SETTINGS, current_values
from modules.ui.settings_dialog import SettingsDialog

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


qt = QApplication.instance() or QApplication(sys.argv)
cfg = AppConfig.load(CONFIG_PATH)
dialog = SettingsDialog(cfg, defaults=current_values(cfg), monitors=[(1, "Monitor 1: 2560×1440 (primary)")],
                        microphones=[("", "System default"), ("Microphone (USB Audio)", "Microphone (USB Audio)")])
dialog.show()
qt.processEvents()

print("a tab per topic")

check("tabs: General, Remarks, Screen, Voice, Avatar, Your data",
      list(SECTIONS) == ["General", "Remarks", "Screen", "Voice", "Avatar", "AI model", "Your data"]
      and [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == list(SECTIONS))
check("every setting is on the page once, on a known tab",
      len({s.id for s in SETTINGS}) == len(SETTINGS) and all(s.section in SECTIONS for s in SETTINGS)
      and all(dialog.widget(s.id) is not None for s in SETTINGS))
longest = 0.0
for i, section in enumerate(SECTIONS):
    dialog.tabs.setCurrentIndex(i)
    qt.processEvents()
    scroll = dialog.tabs.widget(i)
    screens = scroll.widget().sizeHint().height() / scroll.viewport().height()
    rows = [s for s in SETTINGS if s.section == section]
    groups = list(dict.fromkeys(getattr(s, "group", "") for s in rows))
    longest = max(longest, screens)
    print(f"    {section:<10} {len(rows):>2} rows under {len(groups)} heading(s): {', '.join(groups) or '-'}; "
          f"{screens:.1f} screens")
    if section != "Your data":
        check(f"{section}: at most 16 rows, every one under a heading",
              len(rows) <= 16 and all(getattr(s, "group", "") for s in rows))
check(f"no tab is more than 2 screens long (longest {longest:.1f}; Features was 2.9)", longest <= 2.0)

print("\nshort words")

help_lengths = sorted(((len(s.help), s.label) for s in SETTINGS), reverse=True)
check(f"descriptions fit a line or two: at most 130 characters (longest {help_lengths[0][0]}; was 213)",
      help_lengths[0][0] <= 130, str([label for length, label in help_lengths if length > 130]))
label_lengths = sorted(((len(s.label), s.label) for s in SETTINGS), reverse=True)
check(f"labels at most 40 characters (longest {label_lengths[0][0]}; was 54)", label_lengths[0][0] <= 40,
      str([label for length, label in label_lengths if length > 40]))
check("no '--' standing in for a dash, in settings or the reset rows",
      not [s.label for s in SETTINGS if "--" in s.help or "--" in s.label]
      and not [r.label for r in RESETS if "--" in r.help or "--" in r.label])

print("\nthe details")

check("'after restart' is a tag beside exactly the restart-only settings, never part of a label",
      set(getattr(dialog, "_restart_tags", {})) == {s.id for s in SETTINGS if not s.live}
      and not any("restart" in s.label.lower() for s in SETTINGS))
whole, fine = dialog.widget("proactive.cooldown_s"), dialog.widget("speech.pause_s")
check("whole seconds show no decimal; tenths keep theirs",
      whole.decimals() == 0 and not any(c in whole.text() for c in ",.") and fine.decimals() == 1,
      f"{whole.text()!r} / {fine.text()!r}")
folder = dialog.widget("activity.folder")
check("a folder's whole path shows on hover", folder.toolTip() == folder.text() and folder.text())
check("the archive folder is with the resets, on Your data",
      next(s for s in SETTINGS if s.key == "archive_folder").section == "Your data")
check("the window has the companion's icon", not dialog.windowIcon().isNull())

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
