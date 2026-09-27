"""The settings page.

What the user changes is saved to data/settings.yaml -- only what differs from
config.yaml -- and applied on top of config.yaml when loading. Every entry in
SETTINGS must name a real config value with a sensible range; settings that can
change while running take effect without a restart; tools switched off are
never offered or run; and the test suites never see the user's own settings.
"""

import os
import shutil
import sys
import tempfile
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Real fonts, so widths measured offscreen match the screen.
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import yaml
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit

from core.attention import AttentionPolicy
from core.companion import Companion, build_tools
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.orchestrator import Orchestrator
from core.settings import SECTIONS, SETTINGS, SettingsStore, apply, current_values, overlay, read
from core.types import ScreenContext, ToolCall
from modules.ui.app import CompanionApp
from modules.ui.settings_dialog import SettingsDialog
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)

print("every setting is real, labelled and in range")

ids = [s.id for s in SETTINGS]
check("each setting has its own id", len(ids) == len(set(ids)))
check("each is in a known section, with a label and a one-line help",
      all(s.section in SECTIONS and s.label and s.help for s in SETTINGS))
tool_names = set(build_tools(cfg).names) | {"save_notes"}
for s in SETTINGS:
    if s.kind == "tool":
        check(f"{s.label}: switches real tools", set(s.tools) <= tool_names, str(s.tools))
        continue
    target = cfg
    try:
        for part in s.key.split(".")[:-1]:
            target = getattr(target, part)
        value = getattr(target, s.key.split(".")[-1])
    except AttributeError:
        check(f"{s.key} exists in the config", False)
        continue
    kinds = {"bool": bool, "int": int, "float": (int, float), "monitor": int, "folder": str,
             "dir": str, "choice": str, "microphone": (str, int, type(None)), "hotkey": str}
    ok = isinstance(value, kinds[s.kind]) and not (s.kind != "bool" and isinstance(value, bool))
    if s.kind in ("int", "float"):
        ok = ok and s.minimum <= value <= s.maximum
    if s.kind == "choice":
        ok = ok and value in dict(s.choices)
    check(f"{s.key}: a {s.kind}{', within its range' if s.kind in ('int', 'float') else ''}",
          ok, repr(value))
covered = {n for s in SETTINGS if s.kind == "tool" for n in s.tools}
# Tools that belong to a feature go off with the feature's switch.
by_feature = {"search_activity": "activity.enabled", "analyse_music": "music.enabled"}
check("every tool can be switched off, by its own switch or its feature's",
      set(build_tools(cfg).names) <= covered | set(by_feature)
      and all(any(s.key == key for s in SETTINGS) for key in by_feature.values()),
      str(set(build_tools(cfg).names) - covered - set(by_feature)))

print("\nsaved choices apply over config.yaml")

folder = Path(tempfile.mkdtemp(prefix="companion-settings-"))
shutil.copy(CONFIG_PATH, folder / "config.yaml")
(folder / "data").mkdir()
(folder / "data" / "settings.yaml").write_text(yaml.safe_dump({
    "monitor_index": 2,
    "proactive": {"cooldown_s": 33.0, "a_setting_from_another_version": 1},
    "tools": {"disabled": ["get_time"]},
    "something_unknown": {"x": 1},
}), encoding="utf-8")

real_load = AppConfig.load.__func__.__globals__["_load"]  # helpers' original loader
loaded = real_load(AppConfig, folder / "config.yaml")
check("a saved choice overrides config.yaml", loaded.proactive.cooldown_s == 33.0
      and loaded.monitor_index == 2 and loaded.tools.disabled == ["get_time"],
      f"{loaded.proactive.cooldown_s} {loaded.monitor_index} {loaded.tools.disabled}")
check("...untouched values still come from config.yaml",
      loaded.proactive.max_per_hour == cfg.proactive.max_per_hour)
check("unknown keys are skipped, not fatal", not hasattr(loaded.proactive, "a_setting_from_another_version"))
check("settings=False reads config.yaml alone",
      real_load(AppConfig, folder / "config.yaml", settings=False).proactive.cooldown_s
      == cfg.proactive.cooldown_s)
check("the test suites never see saved settings",
      AppConfig.load(folder / "config.yaml").proactive.cooldown_s == cfg.proactive.cooldown_s)
(folder / "data" / "settings.yaml").write_text("proactive: [this is: not valid", encoding="utf-8")
check("a damaged settings file doesn't stop loading",
      real_load(AppConfig, folder / "config.yaml").proactive.cooldown_s == cfg.proactive.cooldown_s)

print("\nwhat is saved")

base = real_load(AppConfig, folder / "config.yaml", settings=False)
live = real_load(AppConfig, folder / "config.yaml", settings=False)
values = current_values(live)
values["proactive.cooldown_s"] = 120.0
values["tool:get_time"] = False
values["tools.notes_file"] = str(folder / "vault")
changed = apply(live, values)
check("applying reports what changed",
      {s.id for s in changed} == {"proactive.cooldown_s", "tool:get_time", "tools.notes_file"},
      str([s.id for s in changed]))
check("a tool switched off lands in tools.disabled", live.tools.disabled == ["get_time"])
check("the notes folder keeps the notes file's name",
      Path(live.tools.notes_file) == folder / "vault" / Path(base.tools.notes_file).name,
      live.tools.notes_file)
saved = overlay(live, base)
check("only what differs from config.yaml is saved",
      saved == {"proactive": {"cooldown_s": 120.0}, "tools": {"disabled": ["get_time"],
                "notes_file": str(folder / "vault" / Path(base.tools.notes_file).name)}},
      str(saved))
store = SettingsStore(folder / "data" / "settings.yaml")
store.save(saved)
check("saved and loaded back, it is the same config",
      real_load(AppConfig, folder / "config.yaml").proactive.cooldown_s == 120.0
      and store.load() == saved)
values["proactive.cooldown_s"] = base.proactive.cooldown_s
apply(live, values)
check("setting a value back to config.yaml's drops it from what is saved",
      "proactive" not in overlay(live, base), str(overlay(live, base)))

print("\nswitched-off tools")


class ScriptedLLM:
    def __init__(self, calls=()):
        self.pending, self.rounds = list(calls), []

    def chat(self, messages, images=None, tools=None, stream=False,
             collect_tool_calls=None, **kwargs):
        self.rounds.append(tools)
        if self.pending and collect_tool_calls is not None:
            collect_tool_calls.extend(self.pending)
            self.pending = []
            return
        yield "Done."


def companion(llm, disabled):
    comp = Companion.__new__(Companion)
    comp.config = AppConfig.load(CONFIG_PATH)
    comp.config.tools.disabled = list(disabled)
    comp.llm, comp.audio, comp.memory = llm, None, ConversationMemory()
    comp.tools = build_tools(comp.config)
    return comp


PAGE = ScreenContext(text="An article.", window_title="Article", app_name="brave.exe", source="uia")
llm = ScriptedLLM()
companion(llm, []).ask("What time is it?", context=PAGE).text()
check("switched on, the clock is offered for 'what time is it?'",
      llm.rounds[0] and "get_time" in {t.name for t in llm.rounds[0]})
llm = ScriptedLLM()
companion(llm, ["get_time"]).ask("What time is it?", context=PAGE).text()
check("switched off, it isn't", not llm.rounds[0] or "get_time" not in {t.name for t in llm.rounds[0]},
      str(llm.rounds[0]))
llm = ScriptedLLM([ToolCall(name="get_time", arguments={})])
comp = companion(llm, ["get_time"])
comp.ask("What time is it?", context=PAGE).text()
check("...and a call to it anyway is not run", "switched off" in comp._run_tool(
      ToolCall(name="get_time", arguments={}), "What time is it?"))
comp = companion(ScriptedLLM(), ["read_notes", "search_notes", "save_notes"])
before = len(comp.tools.notebook.entries())
comp.ask("Note that the gears were bronze.", context=PAGE).text()
check("notes switched off: 'note that…' saves nothing", len(comp.tools.notebook.entries()) == before)

print("\ntaken up while running")

worker = CompanionWorker(cfg)
worker._attention = AttentionPolicy()
worker._orchestrator = Orchestrator(SimpleNamespace(), worker._attention)
worker._companion = SimpleNamespace(memory=ConversationMemory())
cfg.proactive.cooldown_s, cfg.proactive.max_per_hour = 200.0, 7
cfg.proactive.min_time_on_page_s, cfg.proactive.max_remarks_per_page = 42.0, 4
cfg.memory.enabled = False
worker.apply_settings()
check("remark timing reaches the running policy and orchestrator",
      worker._attention.cooldown_s == 200.0 and worker._attention.max_per_hour == 7
      and worker._orchestrator.min_time_on_page_s == 42.0
      and worker._orchestrator.max_remarks_per_page == 4)
check("...and memory can be switched off", worker._companion.memory.enabled is False)
cfg = AppConfig.load(CONFIG_PATH)

print("\nthe page and the app")

qt = QApplication.instance() or QApplication(sys.argv)
dialog = SettingsDialog(cfg, defaults=current_values(cfg), monitors=[(1, "Monitor 1"), (2, "Monitor 2")])
check("the page has a control for every setting",
      all(dialog.widget(s.id) is not None for s in SETTINGS))
check("...showing the current values", dialog.values() == current_values(cfg),
      str({k: v for k, v in dialog.values().items() if current_values(cfg)[k] != v}))
dialog.widget("proactive.cooldown_s").setValue(300)
dialog.widget("tool:stopwatch").setChecked(False)
dialog.widget("monitor_index").setCurrentIndex(1)
got = dialog.values()
check("changed controls give typed values",
      got["proactive.cooldown_s"] == 300.0 and got["tool:stopwatch"] is False
      and got["monitor_index"] == 2, str({k: got[k] for k in ("proactive.cooldown_s", "tool:stopwatch", "monitor_index")}))
dialog.set_values(current_values(cfg))
check("'Back to config.yaml' puts the controls back", dialog.values() == current_values(cfg))
check("restart-only settings say so, with a tag beside the label",
      "voice.enabled" in dialog._restart_tags and "proactive.enabled" not in dialog._restart_tags)

settings_dir = Path(tempfile.mkdtemp(prefix="companion-settings-app-"))
app_cfg = AppConfig.load(CONFIG_PATH)
app_cfg.root = settings_dir
app_cfg.tools.notes_file = str(settings_dir / "data" / "notes.md")
app_base = AppConfig.load(CONFIG_PATH)
app_base.root = settings_dir
app_base.tools.notes_file = app_cfg.tools.notes_file
calls, notices = [], []
window = ChatWindow(app_cfg)
fake = SimpleNamespace(
    config=app_cfg, listener=None, ratings=None, _quiet_action=None,
    worker=SimpleNamespace(apply_settings=lambda: calls.append("worker")),
    window=SimpleNamespace(add_notice=lambda text, colour="": notices.append(text),
                           apply_settings=lambda redock=False: calls.append(("window", redock))),
)
values = current_values(app_cfg)
values["proactive.enabled"] = not app_cfg.proactive.enabled
values["voice.enabled"] = not app_cfg.voice.enabled
values["monitor_index"] = 2
changed = CompanionApp._apply_settings(fake, values, app_base)
written = settings_dir / app_cfg.settings_file
check("saving writes data/settings.yaml", written.is_file())
check("...holding the changes", yaml.safe_load(written.read_text(encoding="utf-8")).get("monitor_index") == 2)
check("the running app takes them up, and the window re-docks for a monitor change",
      "worker" in calls and ("window", True) in calls, str(calls))
check("the notice names what needs a restart",
      notices and "Speak answers aloud" in notices[-1] and "Monitor to watch" in notices[-1]
      and "Comment on what you're reading" not in notices[-1], str(notices))

opened = []
window.settings_requested.connect(lambda: opened.append(1))
actions = {a.text().split("  ")[0]: a for a in window.menu.actions()}
check("the header's menu holds settings, clear, hide and quit",
      set(actions) == {"Settings…", "Clear conversation", "Hide", "Quit"}, str(list(actions)))
actions["Settings…"].trigger()
check("...and Settings… opens the page", opened == [1])
full = AppConfig.load(CONFIG_PATH)
full.speech.enabled = full.proactive.enabled = True
full.speech.languages = ["en", "tr"]
crowded = ChatWindow(full)
check(f"with every header button showing, the window still fits ui.width ({full.ui.width}px)",
      crowded.minimumSizeHint().width() <= full.ui.width,
      f"needs {crowded.minimumSizeHint().width()}px")

app_cfg.proactive.enabled = False
window.apply_settings()
check("switching remarks off hides the quiet button", window.quiet_button.isHidden())
app_cfg.proactive.enabled = True
window.apply_settings()
check("...and on shows it again, without a restart", not window.quiet_button.isHidden())

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
