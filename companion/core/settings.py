"""The settings page's choices, and where they are kept.

`config.yaml` stays the defaults, comments and all. What the user changes in the
settings page is saved to `data/settings.yaml` (`settings_file`) -- only the
values that differ from config.yaml -- and `AppConfig.load` applies it on top.
Deleting that file puts every setting back.

`SETTINGS` is the single list of what the page shows: each entry names the
config value, says what it does in the user's words, and whether a change takes
effect straight away or after a restart. The page is built from it, so a new
setting is one entry here, and the tests check every entry against the config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.logging import get_logger

log = get_logger(__name__)

#: The page's tabs, one topic each. "AI model" is how the model is asked;
#: "Your data" also holds the reset buttons.
SECTIONS = ("General", "Remarks", "Screen", "Voice", "Avatar", "AI model", "Your data")


@dataclass(frozen=True)
class Setting:
    #: Dotted path into AppConfig, e.g. "proactive.cooldown_s". For kind
    #: "tool", always "tools.disabled".
    key: str
    label: str
    help: str
    section: str
    #: "bool", "int", "float", "folder" (the folder of a file path), "dir" (a
    #: folder path itself), "monitor", "tool" (a group of tool names switched
    #: on and off together), "choice" (one of `choices`), or "microphone" (a
    #: microphone's name; "" on the page, None in the config, is the default), or
    #: "hotkey" (a key combination such as "ctrl+shift+2"; "" is none).
    kind: str
    #: Takes effect without restarting the companion.
    live: bool = True
    minimum: float = 0
    maximum: float = 0
    step: float = 1
    unit: str = ""
    tools: tuple[str, ...] = ()
    #: For "choice": (value, label) pairs, in the order shown.
    choices: tuple[tuple[str, str], ...] = ()
    #: The heading it is shown under on its tab.
    group: str = ""

    @property
    def id(self) -> str:
        return f"tool:{self.tools[0]}" if self.kind == "tool" else self.key


def _tool(label: str, help: str, *names: str) -> Setting:
    return Setting("tools.disabled", label, help, "General", "tool", tools=names, group="Tools")


#: In the page's order: a tab per topic, headings within it. A label
#: says what a row does in a few words; a description fits a line or two.
SETTINGS: tuple[Setting, ...] = (
    # -- General ----------------------------------------------------------------
    Setting("llm.answer_length", "Answer length",
            "Short: a sentence or two. Normal: two or three. Detailed: a paragraph or two. "
            "Asking for detail always gets a full answer.", "General", "choice",
            choices=(("short", "Short"), ("normal", "Normal"), ("detailed", "Detailed")),
            group="Answers"),
    Setting("memory.enabled", "Remember the conversation",
            "Needed for follow-ups like \"explain that again\".", "General", "bool", group="Answers"),
    Setting("ratings.enabled", "Thumbs up / down on replies",
            "Saved on this machine, in data/ratings.jsonl.", "General", "bool", group="Answers"),
    Setting("ratings.show_level", "Show your level with the companion",
            "A small level beside the window's title that grows with your votes. Click it to see "
            "how it works.", "General", "bool", group="Answers"),
    Setting("ratings.remember_moments", "Remember replies you liked",
            "A reply you gave 👍 comes back when it relates to what you're doing, a remark with why "
            "it was said. 👎 forgets it.", "General", "bool", group="Answers"),
    Setting("ratings.moments_embedding_model", "Match them by meaning",
            "With a small local model (embeddinggemma). Off: by shared words, which miss rewordings and Turkish.",
            "General", "choice", live=False, choices=(("embeddinggemma", "On"), ("", "Off")), group="Answers"),
    Setting("reflection.enabled", "Learn a few lasting facts about you",
            "Once a day while you're away, in about_you.md. Never health, religion, politics, "
            "sexuality, money or other people.", "General", "bool", group="About you"),
    Setting("reflection.use_in_replies", "Use those facts in answers and remarks",
            "Given as context, never read out.", "General", "bool", group="About you"),
    Setting("reflection.idle_min", "Reflect after you've been away for",
            "Once a day, the first time you've been idle this long.",
            "General", "float", minimum=1, maximum=240, step=5, unit=" min", group="About you"),
    Setting("reflection.file", "About-you file folder",
            "Where about_you.md is kept. Edit or delete its lines freely.", "General", "folder",
            group="About you"),
    _tool("Clock", "Answers \"what time is it?\".", "get_time"),
    _tool("Stopwatch", "Starts, checks and stops a stopwatch.", "stopwatch"),
    _tool("Timers", "Sets, lists and cancels countdown timers.",
          "set_timer", "list_timers", "cancel_timer"),
    _tool("Notes", "Saves a note when you say \"note that…\", and reads notes back.",
          "read_notes", "search_notes", "save_notes"),
    Setting("tools.notes_file", "Notes folder",
            "Where notes.md is written. A folder inside an Obsidian vault works.",
            "General", "folder", live=False, group="Tools"),
    Setting("ui.start_hidden", "Start hidden in the tray",
            "Summon it with the hotkey or the tray icon.", "General", "bool", live=False,
            group="Starting"),
    # -- Remarks ----------------------------------------------------------------
    Setting("proactive.enabled", "Comment on what you're reading",
            "Now and then one short thing about the page you're on. The say button works "
            "either way.", "Remarks", "bool", group="Speaking up"),
    Setting("proactive.speak_aloud", "Speak remarks aloud",
            "Off: remarks only appear in the window.", "Remarks", "bool", group="Speaking up"),
    Setting("proactive.show_reason", "Show why a remark was made",
            "A small line under each remark. Never spoken.", "Remarks", "bool", group="Speaking up"),
    Setting("learning.enabled", "Learn where remarks are welcome",
            "Replies and thumbs up count for a site; Esc, Quiet and thumbs down against. Rarer "
            "where unwanted, never silenced.", "Remarks", "bool", group="Speaking up"),
    Setting("proactive.callbacks", "Mention a page from an earlier day",
            "At most once a day, when it truly relates. Needs the activity log.",
            "Remarks", "bool", group="Speaking up"),
    Setting("proactive.hold_while_audio_plays", "Wait for videos and music to pause",
            "Remarks never talk over sound. Needs \"Hear what's playing\".",
            "Remarks", "bool", group="Speaking up"),
    Setting("proactive.cooldown_s", "Minimum time between remarks",
            "However interesting the pages.",
            "Remarks", "float", minimum=10, maximum=3600, step=5, unit=" s", group="How often"),
    Setting("proactive.max_per_hour", "Remarks per hour, at most",
            "A ceiling for a long session.", "Remarks", "int", minimum=1, maximum=120,
            group="How often"),
    Setting("proactive.min_time_on_page_s", "Time on a page before a remark",
            "Flipping through tabs never gets one.",
            "Remarks", "float", minimum=0, maximum=600, step=1, unit=" s", group="How often"),
    Setting("proactive.max_remarks_per_page", "Remarks per page, at most",
            "However long you stay on it.", "Remarks", "int", minimum=1, maximum=10,
            group="How often"),
    Setting("proactive.dwell_seconds", "Time on a page before another remark",
            "One more only once you have stayed and scrolled.",
            "Remarks", "float", minimum=30, maximum=3600, step=10, unit=" s", group="How often"),
    Setting("proactive.quiet_after_user_s", "Quiet after you speak",
            "No remarks for this long after you ask or say something.",
            "Remarks", "float", minimum=0, maximum=600, step=5, unit=" s", group="How often"),
    Setting("proactive.natural_moments", "Speak at natural stopping points",
            "When a video ends, at the end of a page, or leaving one you read, rather than as a "
            "page opens.", "Remarks", "bool", group="Natural moments"),
    Setting("proactive.media_min_s", "A video or song counts after playing",
            "Its end is a moment to speak only if it played this long.",
            "Remarks", "float", minimum=5, maximum=600, step=5, unit=" s", group="Natural moments"),
    Setting("proactive.moment_wait_s", "Wait for the end of a page, at most",
            "On pages that report scrolling, before commenting anyway.",
            "Remarks", "float", minimum=0, maximum=600, step=10, unit=" s", group="Natural moments"),
    Setting("proactive.long_stay_s", "Comment when leaving a page read for",
            "One remark about a page you spent this long on.",
            "Remarks", "float", minimum=30, maximum=3600, step=30, unit=" s", group="Natural moments"),
    # -- Screen -----------------------------------------------------------------
    Setting("ambient.enabled", "Watch the screen while idle",
            "Needed for remarks. Costs under 2% of one core.", "Screen", "bool", live=False,
            group="Seeing"),
    Setting("vision.enabled", "Look at the screen, not only read it",
            "A screenshot for pictures, charts, visual questions or \"look at my screen\". Local "
            "model only; never saved.", "Screen", "bool", group="Seeing"),
    Setting("vision.watch_remarks", "Watch the screen for remarks when asked",
            "After \"watch my screen\", each remark gets a screenshot, used once and discarded, "
            "until \"stop watching\".", "Screen", "bool", group="Seeing"),
    Setting("monitor_index", "Monitor to watch",
            "Which screen it reads. The window moves there at once; reading switches after a "
            "restart.", "Screen", "monitor", live=False, group="Seeing"),
    Setting("activity.enabled", "Keep a log of pages you spend time on",
            "Title, app and time, so you can ask about yesterday's article. Never page text or "
            "screenshots.", "Screen", "bool", group="Activity log"),
    Setting("activity.min_seconds", "Log pages you stay on at least",
            "Shorter visits, like flipping through tabs, aren't logged.",
            "Screen", "float", minimum=0, maximum=600, step=5, unit=" s", group="Activity log"),
    Setting("activity.folder", "Activity log folder",
            "One CSV file a month; open it in Excel, edit or delete lines freely.", "Screen", "dir",
            group="Activity log"),
    # -- Voice ------------------------------------------------------------------
    Setting("voice.enabled", "Speak answers aloud",
            "Off: answers are text only.", "Voice", "bool", live=False, group="Speaking"),
    Setting("speech.enabled", "Talk to it with the microphone",
            "The talk shortcut, the mic button and hands-free listening.",
            "Voice", "bool", live=False, group="Listening to you"),
    Setting("speech.input_device", "Microphone",
            "System default follows Windows. If the chosen one is unplugged, the default listens "
            "and the window says so.", "Voice", "microphone", group="Listening to you"),
    Setting("speech.pause_s", "Pause that ends what you say",
            "Hands-free listening sends what you said after a pause this long.",
            "Voice", "float", minimum=0.3, maximum=3.0, step=0.1, unit=" s", group="Listening to you"),
    Setting("speech.mic_hears_speakers", "The microphone can hear my speakers",
            "On: stops listening while it talks or sound plays. Off (headphones): talking "
            "interrupts it.", "Voice", "bool", group="Listening to you"),
    Setting("speech.preview_interval_s", "Show what the mic button heard, every",
            "While recording with the mic button. 0 turns it off.",
            "Voice", "float", minimum=0, maximum=5, step=0.5, unit=" s", group="Listening to you"),
    Setting("speech.listen_on_start", "Start with hands-free listening on",
            "Listening begins as soon as the companion starts.", "Voice", "bool", live=False,
            group="Listening to you"),
    Setting("audio.enabled", "Hear what's playing on the computer",
            "Understands videos, and lets remarks wait for them. Transcribed in memory only.",
            "Voice", "bool", live=False, group="Sound on the computer"),
    Setting("perception.now_playing", "Know which song is playing",
            "Asks Windows' media controls for the exact title and artist, for music questions. "
            "Stays on this machine.", "Voice", "bool", group="Sound on the computer"),
    Setting("music.enabled", "Tell the key, tempo and chords of music",
            "Ask \"what key is this in?\" about what's playing. Needs \"Hear what's playing\".",
            "Voice", "bool", group="Sound on the computer"),
    Setting("music.listen_seconds", "Music questions listen back",
            "How much of what just played is analysed. Kept in memory only.",
            "Voice", "float", minimum=10, maximum=60, step=5, unit=" s", group="Sound on the computer"),
    # -- Avatar -----------------------------------------------------------------
    Setting("avatar.enabled", "Show the avatar on the desktop",
            "A Live2D character whose mouth moves when the companion speaks. Clicks pass "
            "through it.", "Avatar", "bool", group="On the desktop"),
    Setting("avatar.hidden", "Hide the avatar",
            "Out of sight but still loaded, so it comes back at once. \"Hide avatar\" in the "
            "tray does the same.", "Avatar", "bool", group="On the desktop"),
    Setting("avatar.take_window_place", "Take the chat window's place when hidden",
            "Glides into the window's place and back. Moved meanwhile, it only steps clear of "
            "the window.", "Avatar", "bool", group="On the desktop"),
    Setting("avatar.move_hotkey", "Shortcut to move it",
            "Press it, drag the avatar (the mouse wheel resizes it), then press it again or Esc. "
            "Empty: none.", "Avatar", "hotkey", live=False, group="On the desktop"),
    Setting("avatar.mouth_shapes", "Mouth shapes each sound",
            "The shape of each speech sound (a, i, u, e, o), not only opening with loudness.",
            "Avatar", "bool", live=False, group="Face"),
    Setting("avatar.follow_mouse", "Looks at your mouse",
            "Head and eyes follow the pointer, and turn back to you when it rests or while it "
            "speaks.", "Avatar", "bool", group="Face"),
    Setting("avatar.expressions", "Shows a mood",
            "Happy, sad, surprised or thinking, read from your message and the reply.",
            "Avatar", "bool", group="Face"),
    Setting("avatar.folder", "Models folder",
            "Live2D models, each in a folder with a .model3.json file. The first found is shown.",
            "Avatar", "dir", group="Model"),
    Setting("avatar.height", "Height",
            "How tall its window is; the width follows.",
            "Avatar", "int", minimum=200, maximum=1400, step=50, unit=" px", group="Model"),
    Setting("avatar.fps", "Frame rate",
            "Lower uses less CPU. 30 is smooth; 60 cost 8–12% of one core.",
            "Avatar", "int", minimum=10, maximum=60, step=5, unit=" fps", group="Model"),
    # -- AI model: how the model is asked ---------------------------
    Setting("ollama.max_reply_tokens", "Longest reply",
            "A safety stop for a reply that runs on. Answers need up to about 550; 0 is no limit.",
            "AI model", "int", live=False, minimum=0, maximum=8192, step=256, unit=" tokens",
            group="Replies"),
    Setting("llm.modular_system_prompt", "Send only the rules a message needs",
            "Tool rules with tool questions, audio rules with audio, and so on — fewer rules to follow at once.",
            "AI model", "bool", group="What it is sent"),
    Setting("llm.screen_only_when_relevant", "Send the screen only when asked about",
            "\"How are you\" goes without it; \"what is this?\" or a word from the page goes with it.",
            "AI model", "bool", group="What it is sent"),
    Setting("llm.log_prompts", "Save what is sent to the model",
            "Every message in full, screen text included, one file a day. For checking a reply "
            "that made no sense.", "AI model", "bool", group="Checking replies"),
    Setting("llm.prompt_log_folder", "Saved prompts folder",
            "Where those files go. Screenshots aren't saved; a day's file stops at 20 MB.",
            "AI model", "dir", group="Checking replies"),
    # -- Your data, beside the reset buttons -------------------------------------
    Setting("archive_folder", "Archive folder",
            "Where resets move old data, one dated folder each. Nothing is deleted; move files "
            "back to undo.", "Your data", "dir", group="Where old data goes"),
)


def _resolve(config, key: str) -> tuple[Any, str]:
    parts = key.split(".")
    target = config
    for part in parts[:-1]:
        target = getattr(target, part)
    return target, parts[-1]


def _folder_of(config, path: str) -> str:
    full = Path(path) if Path(path).is_absolute() else config.root / path
    return os.path.normpath(str(full.parent))


def read(config, setting: Setting) -> Any:
    """The setting's value as the page shows it."""
    if setting.kind == "tool":
        return not any(name in config.tools.disabled for name in setting.tools)
    target, attr = _resolve(config, setting.key)
    value = getattr(target, attr)
    if setting.kind == "folder":
        return _folder_of(config, value)
    if setting.kind == "dir":
        full = Path(value) if Path(value).is_absolute() else config.root / value
        return os.path.normpath(str(full))
    if setting.kind == "microphone":
        return "" if value is None else str(value)
    return value


def write(config, setting: Setting, value: Any) -> None:
    """Put a value from the page into the config object."""
    if setting.kind == "tool":
        disabled = [n for n in config.tools.disabled if n not in setting.tools]
        if not value:
            disabled += list(setting.tools)
        config.tools.disabled = disabled
        return
    target, attr = _resolve(config, setting.key)
    if setting.kind == "folder":
        name = Path(getattr(target, attr)).name
        value = str(Path(value) / name)
    elif setting.kind == "dir":
        value = str(value)
    elif setting.kind == "bool":
        value = bool(value)
    elif setting.kind in ("int", "monitor"):
        value = int(value)
    elif setting.kind == "float":
        value = float(value)
    elif setting.kind == "hotkey":
        value = str(value or "").strip().lower()
    elif setting.kind == "microphone":
        # An index typed into config.yaml by hand stays an index.
        text = "" if value is None else str(value)
        value = None if not text else int(text) if text.isdigit() else text
    elif setting.kind == "choice":
        value = str(value)
        if value not in dict(setting.choices):
            raise ValueError(f"{setting.key}: {value!r} is not one of {[c for c, _ in setting.choices]}")
    setattr(target, attr, value)


def current_values(config) -> dict[str, Any]:
    return {s.id: read(config, s) for s in SETTINGS}


def apply(config, values: dict[str, Any]) -> list[Setting]:
    """Write the page's values into `config`; return the settings that changed."""
    changed = []
    for setting in SETTINGS:
        if setting.id not in values:
            continue
        before = read(config, setting)
        write(config, setting, values[setting.id])
        if read(config, setting) != before:
            changed.append(setting)
    return changed


def overlay(config, base) -> dict:
    """What to save: every page-managed value that differs from config.yaml."""
    nested: dict = {}
    for setting in SETTINGS:
        if read(config, setting) == read(base, setting):
            continue
        if setting.kind == "tool":
            nested.setdefault("tools", {})["disabled"] = list(config.tools.disabled)
            continue
        target, attr = _resolve(config, setting.key)
        parts = setting.key.split(".")
        node = nested
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = getattr(target, attr)
    return nested


_HEADER = (
    "# Written by the companion's settings page. These values override\n"
    "# config.yaml; delete a line (or the whole file) to go back to it.\n"
)


class SettingsStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            log.warning("could not read %s; ignoring it", self.path)
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, nested: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = _HEADER + (
            yaml.safe_dump(nested, sort_keys=True, allow_unicode=True) if nested else ""
        )
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.path)
        log.info("settings saved to %s", self.path)
