"""Configuration loading.

Every section has working defaults, so a missing or partial `config.yaml` still
produces a runnable app. Unknown keys are warned about rather than ignored --
silently dropping a misspelled `blocked_processes` would be a privacy bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Type, TypeVar

import yaml

from core.errors import ConfigError
from core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


@dataclass
class OllamaConfig:
    model: str = "qwen3.5:4b"
    host: str = "http://localhost:11434"
    keep_alive: str = "10m"
    # Ollama's own default is ~4k. OCR text plus the system prompt plus the
    # answer overflows that easily, and the overflow is silent: the model just
    # stops seeing the top of the screen.
    num_ctx: int = 8192
    # Qwen3 is a hybrid reasoning model and emits <think> blocks by default.
    # For "what's on my screen" that is latency without benefit.
    think: bool = False
    request_timeout_s: float = 180.0
    # Load the model into VRAM at startup. A cold load is ~4 s, and without
    # this it lands on the first question -- which, once answers are spoken,
    # is four seconds of silence before the companion says anything.
    warm_up: bool = True
    # Extra sampling options sent with every request, on top of the model's own
    # defaults. The app still sets the temperature per call (answers 0.3,
    # remarks 0.85), so a temperature here has no effect.
    options: dict = field(default_factory=dict)
    # The longest a reply may run, in tokens: a safety stop for one that runs on
    #. 0 means no cap.
    max_reply_tokens: int = 0


@dataclass
class OCRConfig:
    engine: str = "rapidocr"
    # Downscaling a 1440p/4K screenshot before detection costs little accuracy
    # on body text and saves seconds of CPU.
    max_long_edge: int = 1920
    min_confidence: float = 0.5


@dataclass
class CaptureConfig:
    # "monitor" grabs the whole display; "window" grabs only the active window,
    # which is faster to OCR and free of surrounding clutter.
    mode: str = "monitor"
    # A window must have at least this much of itself on the target monitor to
    # be considered for reading. Stops a window that is really on another
    # display, but overlaps this one by a few pixels, from being picked.
    min_window_on_monitor: float = 0.5
    # Pin reading to a window whose title or process contains this text. Empty
    # means "decide automatically from Z-order".
    window_match: str = ""
    # Full-screen overlays that sit above real windows and contain nothing
    # readable.
    ignore_processes: list[str] = field(
        default_factory=lambda: [
            "nvidia overlay.exe",
            "nvcontainer.exe",
            "gameoverlayui.exe",
            "discord.exe",
            "textinputhost.exe",
        ]
    )


@dataclass
class PerceptionConfig:
    # Tried in order; the first source returning at least `min_chars` wins.
    sources: list[str] = field(default_factory=lambda: ["uia", "ocr"])
    min_chars: int = 40
    # For questions about music, ask Windows' media controls for the exact track
    # playing (modules/perception/media.py; ~0.3 s, nothing installed).
    now_playing: bool = False


@dataclass
class UIAConfig:
    max_chars: int = 20000
    max_depth: int = 14
    max_nodes: int = 3000
    # Chromium switches its accessibility tree on asynchronously when asked,
    # so an empty first read is retried after this delay.
    retry_delay_s: float = 0.6


@dataclass
class LLMConfig:
    max_screen_chars: int = 12000
    temperature: float = 0.3
    stream: bool = True
    # How long answers are: "short", "normal" or "detailed" (core/length.py).
    # Said in words, in each turn; asking for detail always gets a full answer.
    answer_length: str = "normal"
    # Save exactly what is sent to the model, and its reply, to a text file a
    # day (modules/llm/prompt_log.py). It holds the screen's text.
    log_prompts: bool = False
    prompt_log_folder: str = "data/prompts"
    # Send the screen's text only with a message that is about it, decided in
    # code (core/relevance.py); off, every message gets it.
    screen_only_when_relevant: bool = False
    # Send only the system prompt's sections a message needs -- tools, audio,
    # the screen, remarks -- decided in code (core/prompt.py).
    modular_system_prompt: bool = False


@dataclass
class PrivacyConfig:
    enabled: bool = True
    blocked_processes: list[str] = field(default_factory=list)
    blocked_title_patterns: list[str] = field(default_factory=list)


@dataclass
class AmbientConfig:
    enabled: bool = True
    # How often to take a cheap thumbnail. A full-screen grab costs ~21 ms on a
    # 1440p display and that is the floor -- everything after it is ~0.02 ms --
    # so this interval, not the processing, sets the idle CPU cost.
    sample_interval_s: float = 1.0
    # The screen must hold still this long before a full read. Without it,
    # scrolling would fire a read per frame, each of a half-scrolled page.
    stable_delay_s: float = 0.8
    # Mean absolute per-pixel change (0..1) that counts as the screen changing.
    # Low enough to catch a new paragraph, high enough to ignore a blinking
    # cursor or a clock ticking over.
    change_threshold: float = 0.012
    # Floor between full reads, so a video or live dashboard can't pin the CPU.
    min_refresh_interval_s: float = 3.0
    # Questions reuse an ambient read up to this old instead of perceiving again.
    max_cache_age_s: float = 20.0
    sample_long_edge: int = 160


@dataclass
class VoiceConfig:
    enabled: bool = True
    engine: str = "piper"
    voice: str = "en_US-lessac-medium"
    voices_dir: str = "data/voices"
    speed: float = 1.0
    volume: float = 0.9
    # Output device: None uses the system default. Accepts an index or a
    # substring of the device name.
    device: str | int | None = None
    # Voice per language, chosen from the language the question was asked in.
    # An English voice reading Turkish is somewhere between comic and
    # unintelligible. Voices not installed are ignored, falling back to `voice`.
    voices_by_language: dict = field(
        default_factory=lambda: {
            "en": "en_GB-alba-medium",
            "tr": "tr_TR-dfki-medium",
        }
    )
    # Short, because getting the FIRST sentence out fast is what makes a reply
    # feel prompt; later sentences queue behind it anyway.
    min_sentence_chars: int = 12
    # Load the voice at startup so the first answer doesn't pay ~3s of model
    # load and ONNX warm-up.
    warm_up: bool = True


@dataclass
class SpeechConfig:
    """Listening. Push-to-talk only in this phase, deliberately."""

    enabled: bool = True
    engine: str = "faster-whisper"
    # Measured per-utterance on this machine (CPU/int8): tiny.en 244 ms,
    # base.en 453 ms, distil-small.en 1460 ms, small.en 1442 ms. Whisper pads to
    # a 30 s window, so cost is per utterance, not per second -- model size is
    # the only real lever on latency.
    model: str = "base.en"
    device: str = "cpu"  # "cuda" needs cuBLAS/cuDNN DLLs that aren't installed
    compute_type: str = "int8"
    # Force one language, or None to choose between `languages` below.
    language: str | None = None
    # Shortlist for detection. Free auto-detect over 99 languages mishears
    # English as Polish or Turkish on a middling microphone; picking between
    # two candidates is far more reliable. Empty = detect over everything.
    # This is speech INPUT only -- video listening still hears any language.
    languages: list[str] = field(default_factory=lambda: ["en", "tr"])
    beam_size: int = 1
    # Hold to talk. Must be a different combination from ui.hotkey.
    hotkey: str = "ctrl+shift+a"
    # A "mic" button in the window: click to start speaking, click again to
    # send. The same recording as the talk key, without holding anything.
    mic_button: bool = True
    # While recording with the mic button, show what has been heard so far,
    # updated this often, so nothing is sent blind. 0 = no preview. Each update
    # is one transcription on the CPU: measured ~0.85 s with "base", whatever
    # the length, so 1.5 s keeps up with one at a time.
    preview_interval_s: float = 1.5
    # A "listen" button in the window: the microphone stays on, and each thing
    # you say is answered after a pause -- no key, no send -- until clicked off.
    listen_button: bool = True
    # Start with hands-free listening already on.
    listen_on_start: bool = False
    # How long a pause means you have finished saying something.
    pause_s: float = 0.8
    # Can the microphone hear your speakers? True, the safe default, pauses
    # listening while the companion talks and while other sound plays, so it
    # never answers itself or a video. False (headphones, or a microphone that
    # doesn't pick the speakers up) keeps listening, and speaking interrupts it.
    mic_hears_speakers: bool = True
    # With mic_hears_speakers: how long after the companion stops talking
    # before listening resumes, for the room's echo to die away.
    echo_tail_s: float = 0.6
    input_device: str | int | None = None
    max_seconds: float = 30.0
    # Shorter than this is a stray keypress rather than speech.
    min_seconds: float = 0.3
    # Given silence, Whisper does not return nothing -- it returns "You" or
    # "Thank you". Audio quieter than this never reaches the model.
    silence_rms: float = 0.004
    # Off by default: the RMS gate already stops silence reaching the model,
    # and the VAD was measured clipping speech edges ("when was it published"
    # -> "when was is published"). Worth turning on in a noisy room.
    vad_filter: bool = False
    warm_up: bool = True


@dataclass
class AudioConfig:
    """Hearing what the speakers are playing.

    Off by default. Screen reading was asked for explicitly; continuously
    transcribing everything audible -- calls, music, whatever is in another
    tab -- is a bigger step, so it is opt-in rather than something that starts
    happening after an update.
    """

    enabled: bool = False
    # Its own model instance: faster-whisper is not documented as thread-safe
    # and push-to-talk is using the other one.
    #
    # Multilingual ("base", not "base.en") so a foreign-language video is
    # understood rather than mangled. The .en models are slightly better at
    # English but cannot handle anything else at all.
    model: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"
    # None = auto-detect, which is the point: you don't know what language a
    # video is in before it starts playing.
    language: str | None = None
    # "transcribe" keeps the original words and lets the local model translate
    # on request -- to any language, not just English, and preserving what was
    # actually said. "translate" makes Whisper output English directly: faster,
    # one step, but English-only and the original is lost.
    task: str = "transcribe"
    # 15 s balances latency against cost: 805 ms per chunk with base.en,
    # about 5.4% of a core while sound is playing and nothing while it isn't.
    chunk_seconds: float = 15.0
    silence_rms: float = 0.005
    buffer_minutes: float = 5.0
    keep_minutes: float = 10.0
    max_transcript_chars: int = 3000
    # How much recent speech to put in front of the model with a question,
    # newest first and timed. Was 5: a minute-old Turkish video still steered
    # English answers.
    context_minutes: float = 2.0
    output_device: str | None = None  # None = the default speaker


@dataclass
class ProactiveConfig:
    """Speaking without being asked.

    Off by default. This is the feature most able to make the companion
    unbearable, and the failure is asymmetric -- one remark too many annoys far
    more than one missed remark costs. Turn it on deliberately.
    """

    enabled: bool = False
    # Minimum gap between unprompted remarks.
    cooldown_s: float = 75.0
    max_per_hour: int = 25
    # Below this much text there is nothing worth an opinion -- menus, empty
    # pages, the desktop.
    min_chars: int = 400
    # How long a page must hold their attention before it can be remarked on.
    # Flipping through tabs never gets that far, so it costs nothing.
    min_time_on_page_s: float = 10.0
    # Staying on one page this long, then scrolling, earns another remark.
    dwell_seconds: float = 300.0
    # At most this many remarks about any one page, however long they stay.
    max_remarks_per_page: int = 2
    # Silence after the user speaks. Interrupting someone who just talked to
    # you is the rudest version of this.
    quiet_after_user_s: float = 45.0
    # Remarks are short by construction: a long unprompted monologue is worse
    # than a wrong one.
    max_words: int = 25
    # Remarks get their own temperature. 0.3 suits answers, where the same
    # question should get the same answer; for remarks it meant the same page
    # produced the identical sentence every time (measured: 4 of 4).
    temperature: float = 0.85
    # What the companion is like when it speaks unprompted: a description and
    # example remarks. A placeholder for now, to be replaced by the user's own
    # persona.
    persona_file: str = "prompts/remarks.md"
    speak_aloud: bool = True
    # Show why each remark was made, as a small line under it in the window and
    # in the tray notification. Never spoken. Proactive help disrupts less when
    # people can see why it came (CHI 2025).
    show_reason: bool = True
    # "Say something about this": a global hotkey that asks for a remark about
    # the page right now, even with proactive remarks switched off. It skips
    # the waiting (time on page, cooldown, hourly budget, per-page limit), never
    # the quality checks. Global hotkeys take the combination from every other
    # app, so this avoids common ones (Ctrl+Shift+X/C/S/Q). "" = no hotkey; the
    # window button and tray item still work.
    remark_now_hotkey: str = "ctrl+shift+1"
    # Wait for videos, music and calls to go quiet before speaking up, instead
    # of talking over them. Needs audio.enabled, since the signal comes from
    # the system-audio capture. Background music means no remarks while it
    # plays -- turn this off if that is the wrong trade.
    hold_while_audio_plays: bool = True
    # How long the sound must have stopped first, so a pause between two
    # sentences of a video doesn't count as the video stopping.
    audio_quiet_s: float = 2.0
    # Natural moments: speak when something just ended rather
    # than whenever a page has been open long enough. Best first: a video or
    # music that played at least media_min_s on the page stops; the end of the
    # page is reached (when the app reports scrolling); a page read for
    # long_stay_s without a remark is left. A page just opened comes last, and
    # on a page that reports scrolling it waits up to moment_wait_s for the end.
    natural_moments: bool = False
    media_min_s: float = 20.0
    # Scrolled this far (percent) counts as the end of the page.
    page_end_percent: float = 95.0
    moment_wait_s: float = 90.0
    long_stay_s: float = 180.0
    # A remark about a page just left must come this soon after, or not at all.
    parting_window_s: float = 30.0
    # A remark may connect to a page from an earlier day ("you were reading
    # about X on Friday"), at most once a day. Needs the activity log.
    callbacks: bool = False


@dataclass
class ToolsConfig:
    enabled: bool = True
    # How many tool rounds before giving up. Two is enough for "check the time,
    # then set a timer"; more usually means the model is looping.
    max_rounds: int = 3
    notes_file: str = "data/notes.md"
    timers_file: str = "data/timers.json"
    # Tools switched off in the settings page: never offered to the model and
    # never run. "save_notes" stops the app saving notes when asked.
    disabled: list[str] = field(default_factory=list)


@dataclass
class VisionConfig:
    """Letting the model see a screenshot, not only read the text.

    Off by default in code; measured before turning it on (see core/vision.py).
    The screenshot goes to the local model only, after the privacy check, and
    is never written to disk.
    """

    enabled: bool = False
    # "thin_text": with a question about something visual, or when the screen
    # has too little text to answer from. "always" or "never" otherwise.
    when: str = "thin_text"
    # Long edge of the image sent. 768 answered everything 1024 did, for 280
    # fewer prompt tokens.
    max_image_edge: int = 768
    # Fewer characters than this counts as too little text to answer from.
    thin_text_chars: int = 400
    # A page short on text gets unprompted remarks only if it shows a picture:
    # at least this many distinct colours in a thumbnail. Measured 108-123 for
    # photo and video pages, 8-12 for text, charts and interfaces.
    min_picture_colours: int = 48
    # "watch my screen": each unprompted remark then gets a screenshot taken
    # for it, discarded once the remark is written, until "stop watching".
    watch_remarks: bool = False


@dataclass
class ActivityConfig:
    """A log of the pages you spend time on.

    Readable CSV files, one per month: title, app, times, a one-line
    description. Never page text or screenshots; nothing leaves the machine.
    """

    enabled: bool = False
    # Relative to the companion folder unless absolute.
    folder: str = "data/activity"
    # Visits shorter than this -- flipping through tabs -- are not logged.
    min_seconds: float = 10.0


@dataclass
class ReflectionConfig:
    """A few lasting facts about you, learned once a day.

    Written to a Markdown file you can edit; every fact is checked in code
    against its evidence and against sensitive subjects. Off by default.
    """

    enabled: bool = False
    # The facts file; its folder also holds about_you.state.json.
    file: str = "data/about_you.md"
    # Reflect once a day, after you have been away this many minutes.
    idle_min: float = 10.0
    # Something you do needs this many different pages behind it.
    min_visits: int = 3
    # How far back to look.
    days: int = 7
    max_facts: int = 20
    # Give answers and remarks the facts as context.
    use_in_replies: bool = True


@dataclass
class LearningConfig:
    """Learning where remarks are welcome, in code.

    Replies, thumbs up and asked-for remarks count for; Esc, Quiet and thumbs
    down against; ignored remarks count for nothing. Per site, kind of remark
    and moment, bounded so one negative never silences anything.
    """

    enabled: bool = False
    file: str = "data/learning.json"
    # A reaction this soon after a remark is the reaction to it.
    outcome_window_s: float = 120.0
    # The learned allowance per site stays between these.
    min_allowance: float = 0.25
    max_allowance: float = 1.5


@dataclass
class MusicConfig:
    """Telling the key, tempo and chords of what is playing.

    Needs system audio (audio.enabled). Analysed only when asked, from the last
    `listen_seconds` of sound held in memory -- nothing runs the rest of the time.
    """

    enabled: bool = False
    # How much of what just played is analysed. The buffer holds 60 s.
    listen_seconds: float = 30.0


@dataclass
class RatingsConfig:
    """Thumbs up / down under each answer and remark in the window.

    A click saves the rating, with the reply it rates, to a file on this
    machine; nothing is sent anywhere. Liked replies are what a later fine-tune
    would learn from. A second click on the same reply replaces its rating.
    """

    # Show the thumbs under answers and remarks.
    enabled: bool = True
    # A level beside the window's title that grows with the votes (core/relationship.py).
    show_level: bool = False
    # Replies you gave a 👍 come back as shared moments when what you're doing
    # relates to them; a remark with why it was made (core/moments.py).
    remember_moments: bool = False
    # Match those moments by meaning with a small local embedding model, not by
    # shared words (modules/llm/embeddings.py); "" matches by words.
    moments_embedding_model: str = ""
    # Where ratings are kept, relative to the companion folder unless absolute.
    file: str = "data/ratings.jsonl"


@dataclass
class MemoryConfig:
    enabled: bool = True
    max_turns: int = 8
    # Kept modest on purpose: screen text can already be 12000 chars, and both
    # share the 8192-token context with the answer.
    max_chars: int = 4000


@dataclass
class UIConfig:
    # ctrl+alt+space is commonly taken already (it is on this machine), so the
    # default is a combination that was verified free here.
    hotkey: str = "ctrl+shift+space"
    opacity: float = 0.96
    width: int = 520
    height: int = 620
    # Screen corner to dock to: bottom-right, bottom-left, top-right, top-left.
    corner: str = "bottom-right"
    margin: int = 24
    font_size: int = 14
    start_hidden: bool = False


@dataclass
class AvatarConfig:
    """A Live2D model standing on the desktop.

    Drawn with live2d-py in a transparent window that clicks pass through; its
    mouth follows the companion's voice. Off in code: it is a window of its own
    on the user's screen.
    """

    enabled: bool = False
    # Models live here, each in its own folder with a .model3.json file.
    folder: str = "data/avatars"
    # A .model3.json file name in the folder; "" = the first one found.
    model: str = ""
    # Window height in pixels; the width follows at 2:3.
    height: int = 600
    # Measured: 60 fps cost 8-12% of one core, so it draws at 30 by default.
    fps: int = 30
    # The mouth takes each speech sound's shape (Piper's phoneme timings; needs
    # the onnx package) instead of only opening with loudness. After a restart:
    # the voice is loaded with them.
    mouth_shapes: bool = False
    # Its head and eyes follow the mouse pointer, looking back at the user when
    # the mouse rests or while it speaks.
    follow_mouse: bool = False
    # A mood on its face for what is being said -- happy, surprised, sad,
    # thinking -- decided in code from the words (core/mood.py).
    expressions: bool = False
    # Out of sight but still loaded, so showing it again is quick; hidden, it
    # draws nothing. The tray's "Hide avatar" sets this too.
    hidden: bool = False
    # While the chat window is hidden, the avatar glides sideways into its place,
    # and back when it is shown -- never left overlapping it.
    take_window_place: bool = False
    # A global shortcut that turns move mode on and off: drag the avatar, and the
    # mouse wheel resizes it. "" = none; the tray's "Move avatar" still works.
    move_hotkey: str = ""
    # Where the avatar was last moved to.
    state_file: str = "data/avatar.json"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    # The GUI runs under pythonw.exe, which has no console, so stderr is lost.
    # Without a file there is no way to diagnose anything that happens in the
    # window. Empty string disables it.
    file: str = "data/companion.log"


@dataclass
class AppConfig:
    monitor_index: int = 1
    system_prompt_file: str = "prompts/system.md"
    # The settings page's choices, applied on top of this file when loading.
    settings_file: str = "data/settings.yaml"
    # Where the settings page's reset buttons move old data. Nothing is deleted.
    archive_folder: str = "data/archive"
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    uia: UIAConfig = field(default_factory=UIAConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ambient: AmbientConfig = field(default_factory=AmbientConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)
    ratings: RatingsConfig = field(default_factory=RatingsConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    activity: ActivityConfig = field(default_factory=ActivityConfig)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    avatar: AvatarConfig = field(default_factory=AvatarConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    root: Path = field(default_factory=Path.cwd)
    #: The config file this was loaded from, if any.
    source: Path | None = None

    @property
    def system_prompt(self) -> str:
        path = self.root / self.system_prompt_file
        if not path.is_file():
            raise ConfigError(f"system prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()

    @classmethod
    def load(cls, path: str | Path, settings: bool = True) -> "AppConfig":
        """Read config.yaml, then -- unless `settings` is False -- apply the
        settings page's saved choices (`settings_file`) on top."""
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"could not parse {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level")

        if settings:
            overlay_path = path.parent / raw.get("settings_file", "data/settings.yaml")
            if overlay_path.is_file():
                try:
                    chosen = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
                except (yaml.YAMLError, OSError):
                    # A damaged settings file must not stop the app starting;
                    # config.yaml alone still makes a working companion.
                    chosen = None
                raw = _merge_settings(raw, chosen)

        return cls(
            monitor_index=raw.get("monitor_index", 1),
            system_prompt_file=raw.get("system_prompt_file", "prompts/system.md"),
            settings_file=raw.get("settings_file", "data/settings.yaml"),
            archive_folder=raw.get("archive_folder", "data/archive"),
            ollama=_section(OllamaConfig, raw.get("ollama"), "ollama"),
            capture=_section(CaptureConfig, raw.get("capture"), "capture"),
            perception=_section(PerceptionConfig, raw.get("perception"), "perception"),
            uia=_section(UIAConfig, raw.get("uia"), "uia"),
            ocr=_section(OCRConfig, raw.get("ocr"), "ocr"),
            llm=_section(LLMConfig, raw.get("llm"), "llm"),
            ambient=_section(AmbientConfig, raw.get("ambient"), "ambient"),
            voice=_section(VoiceConfig, raw.get("voice"), "voice"),
            speech=_section(SpeechConfig, raw.get("speech"), "speech"),
            audio=_section(AudioConfig, raw.get("audio"), "audio"),
            proactive=_section(ProactiveConfig, raw.get("proactive"), "proactive"),
            ratings=_section(RatingsConfig, raw.get("ratings"), "ratings"),
            vision=_section(VisionConfig, raw.get("vision"), "vision"),
            activity=_section(ActivityConfig, raw.get("activity"), "activity"),
            reflection=_section(ReflectionConfig, raw.get("reflection"), "reflection"),
            learning=_section(LearningConfig, raw.get("learning"), "learning"),
            music=_section(MusicConfig, raw.get("music"), "music"),
            tools=_section(ToolsConfig, raw.get("tools"), "tools"),
            memory=_section(MemoryConfig, raw.get("memory"), "memory"),
            ui=_section(UIConfig, raw.get("ui"), "ui"),
            avatar=_section(AvatarConfig, raw.get("avatar"), "avatar"),
            privacy=_section(PrivacyConfig, raw.get("privacy"), "privacy"),
            logging=_section(LoggingConfig, raw.get("logging"), "logging"),
            root=path.parent.resolve(),
            source=path.resolve(),
        )


def _merge_settings(raw: dict, chosen: Any) -> dict:
    """config.yaml with the settings page's choices applied over it.

    Anything this version doesn't know -- a setting from a newer or older
    version -- is skipped rather than refused, so a stale settings file can't
    stop the app starting.
    """
    from dataclasses import MISSING, is_dataclass

    if not isinstance(chosen, dict):
        return raw
    merged = dict(raw)
    known = {f.name: f for f in fields(AppConfig) if f.name not in ("root", "source")}
    for key, value in chosen.items():
        spec = known.get(key)
        if spec is None:
            continue
        section_cls = spec.default_factory if spec.default_factory is not MISSING else None
        if isinstance(value, dict):
            if section_cls is None or not is_dataclass(section_cls):
                continue
            names = {f.name for f in fields(section_cls)}
            section = dict(merged.get(key) or {})
            section.update({k: v for k, v in value.items() if k in names})
            merged[key] = section
        elif section_cls is None:
            merged[key] = value
    return merged


def _section(cls: Type[T], data: Any, label: str) -> T:
    """Build one config dataclass, defaulting anything absent."""
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"config section '{label}' must be a mapping")
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = set(data) - known
    if unknown:
        log.warning(
            "ignoring unknown key(s) in '%s': %s", label, ", ".join(sorted(unknown))
        )
    return cls(**{k: v for k, v in data.items() if k in known})
