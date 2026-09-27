"""Background worker that owns the Companion.

Everything slow happens here -- screen capture, OCR, and generation -- so the
window never freezes mid-answer.

One long-lived thread rather than one per question, for a specific reason: UI
Automation is COM, and COM objects belong to the thread that created them. A
fresh thread per question would either re-initialise the whole UIA client every
time or hand objects across apartments. So the Companion is built inside
`run()`, and every question is serviced by that same thread for the life of the
app.
"""

from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QThread, Signal

from core.config import AppConfig
from core.errors import CompanionError, PrivacyBlocked
from core.logging import get_logger

log = get_logger(__name__)

_STOP = object()

#: How long an interruption stays worth mentioning to the model. "Go on" and
#: "wait, what were you saying?" come within seconds; a question minutes later
#: starts something new, and telling the model it was cut off would only
#: confuse it.
INTERRUPTION_RELEVANT_S = 120.0


class _Transcribe:
    """Queue item carrying recorded audio, so it is handled in order with
    questions rather than racing them."""

    __slots__ = ("audio", "quiet")

    def __init__(self, audio, quiet: bool = False) -> None:
        self.audio = audio
        #: Hands-free: hearing nothing intelligible is normal, not worth a notice.
        self.quiet = quiet


class CompanionWorker(QThread):
    """Serialises questions onto one thread and streams answers back as signals."""

    #: Startup finished. Payload is an error message, or "" on success.
    ready = Signal(str)
    #: A question has been picked up; the UI can show a thinking state.
    thinking = Signal()
    #: Perception finished. Payload is a short status line.
    observed = Signal(str)
    #: One piece of the answer.
    chunk = Signal(str)
    #: The answer completed (or was cancelled).
    finished_answer = Signal()
    #: Something went wrong with this question; the worker stays alive.
    failed = Signal(str)
    #: The ambient loop re-read a settled screen. Payload is a status line.
    watching = Signal(str)
    #: Voice could not be started; the companion still works, silently.
    voice_unavailable = Signal(str)
    #: True while an answer is being written or spoken; False once fully done.
    busy_changed = Signal(bool)
    #: Transcription result. Empty payload means nothing intelligible was said.
    heard = Signal(str)
    #: Speech recognition could not start; typing still works.
    speech_unavailable = Signal(str)
    #: A countdown timer came due.
    timer_fired = Signal(str)
    #: An unprompted remark about what is on screen, and why it was made. The
    #: reason is for the window only; it is never spoken.
    remarked = Signal(str, str, object)
    #: An answer finished (or was stopped). Payload: {"question", "reply",
    #: "page"} -- what a rating of it records.
    answered = Signal(object)
    #: What the mic button has heard so far: (recording session, text).
    previewed = Signal(int, str)
    #: A remark was asked for and nothing was worth saying. Payload: why not.
    remark_declined = Signal(str)
    #: A mood for what is being said (core/mood.py), for the avatar's face.
    mood = Signal(str)
    #: Watching the screen for remarks started (True) or stopped (False), as asked.
    watching_screen = Signal(bool)

    #: Queue item: "say something about this", from the hotkey, tray or window.
    _REMARK_NOW = object()

    def __init__(self, config: AppConfig, image_path: str | None = None) -> None:
        super().__init__()
        self._config = config
        self._image_path = image_path
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._answering = threading.Event()
        self._companion = None
        self._speaker = None
        self._stt = None
        #: The language pinned with the lang button, None for auto; starts as
        #: speech.language. See set_speech_language().
        self._language: str | None = (config.speech.language or "").lower() or None
        self._was_busy = False
        self._attention = None
        self._orchestrator = None
        # (when, Delivery) for the last utterance cut off before it finished.
        self._interrupted = None
        self._last_spoke_at = 0.0
        # Daily reflection: when the user last did anything, the
        # day it last ran, and what they said this session -- kept in memory
        # only, for reflection to read.
        self._last_user_at = time.time()
        self._reflected_on = ""
        self._said: list = []

    # -- called from the UI thread -------------------------------------------

    def ask(self, question: str) -> None:
        self._cancel.clear()
        self._answering.set()
        if self._orchestrator is not None:
            # Cancels any queued prompt-to-fill-silence: they are talking now.
            self._orchestrator.note_user_message()
        self._queue.put(question)

    def note_user_talking(self) -> None:
        """The talk key went down: they are speaking, so nothing unprompted.

        Called when recording starts rather than once the words are known, so
        the quiet period covers the talking itself. A remark composed while the
        key is held would talk over them and end up in the recording.
        """
        self._last_user_at = time.time()
        if self._orchestrator is not None:
            self._orchestrator.note_user_message()

    def remark_now(self) -> None:
        """Ask for a remark about the page on screen; runs on the worker thread,
        where the screen reader and the model live."""
        self._queue.put(self._REMARK_NOW)

    def preview(self, audio, session: int) -> None:
        """Transcribe a recording in progress, to show -- never to ask."""
        self._queue.put(("preview", session, audio))

    def set_speech_language(self, language: str | None) -> None:
        """Pin a language, or None for auto. Pinned, it listens for that
        language, answers and makes remarks in it, and speaks with its voice.
        Video listening is separate.

        Safe from the UI thread: it only sets fields read on the next
        transcription, answer or remark. Chosen before the worker has started,
        it is kept and applied once everything is built.
        """
        language = language.lower() if language else None
        self._language = language
        if self._stt is not None:
            self._stt.set_language(language)
        if self._speaker is not None:
            self._speaker.pin_language(language)
        if self._companion is not None:
            self._companion.reply_language = language
        if self._orchestrator is not None:
            self._orchestrator.reply_language = language

    def set_muted(self, muted: bool) -> bool:
        """Silence or restore unprompted remarks. Returns the new state."""
        if self._attention is None:
            return True
        # No reset on unmuting: the page on screen is still a standing
        # candidate, so it can be remarked on once the quiet is lifted.
        self._attention.muted = muted
        if muted and self._orchestrator is not None:
            self._orchestrator.note_quiet()
        log.info("proactive remarks %s", "muted" if muted else "unmuted")
        return muted

    def is_busy(self) -> bool:
        """True while an answer is being written *or* spoken.

        Speech outlives generation by a wide margin -- a three-sentence answer
        takes ~2 s to write and ~12 s to say -- so "still answering" and "still
        talking" are very different questions. Anything deciding whether there
        is something to interrupt has to ask this one.
        """
        if self._answering.is_set():
            return True
        return self._speaker is not None and self._speaker.is_speaking

    def cancel(self) -> None:
        """Stop the answer in progress.

        Memory keeps everything written so far, since it is on screen. How much
        was actually heard is recorded separately, for the next question.

        Silencing audio happens here, on the UI thread, rather than waiting for
        the worker to notice the flag -- an interruption that takes a sentence
        to take effect doesn't feel like an interruption.
        """
        self._cancel.set()
        if self._speaker is not None:
            # Read before stopping: stop() ends the utterance this describes.
            delivery = self._speaker.delivery()
            self._speaker.stop()
            if delivery is not None and not delivery.complete:
                self._interrupted = (time.time(), delivery)
                log.info(
                    "interrupted after %d of %d sentence(s)%s",
                    delivery.finished,
                    len(delivery.sentences),
                    ", partway through the next" if delivery.partial else "",
                )
        if self._orchestrator is not None:
            # Stopping it mid-sentence is about the clearest "not now" there
            # is. Without this a remark could start the moment it was cut off.
            self._orchestrator.note_user_message(reply=False)
        self._publish_busy()

    def _take_interruption(self):
        """The interruption the next question should know about, if any. Once."""
        pending, self._interrupted = self._interrupted, None
        if pending is None:
            return None
        at, delivery = pending
        if time.time() - at > INTERRUPTION_RELEVANT_S:
            return None
        return delivery

    def clear_memory(self) -> None:
        # The reply an interruption refers to has just been forgotten.
        self._interrupted = None
        if self._companion is not None:
            self._companion.memory.clear()

    def transcribe(self, audio, quiet: bool = False) -> None:
        """Queue captured audio for transcription on the worker thread.

        `quiet`: say nothing if nothing intelligible was heard. Hands-free
        listening sends whatever sounded like speech; a cough shouldn't answer
        "I didn't catch that".
        """
        self._queue.put(_Transcribe(audio, quiet))

    def is_speaking_recently(self, tail_s: float) -> bool:
        """Speaking now, or stopped less than `tail_s` ago."""
        speaker = self._speaker
        if speaker is None:
            return False
        if speaker.is_speaking:
            self._last_spoke_at = time.time()
            return True
        return time.time() - self._last_spoke_at < tail_s

    def sound_playing(self) -> bool:
        """Is anything audible playing? Needs system-audio listening (audio.enabled)."""
        capture = getattr(getattr(self._companion, "audio", None), "capture", None)
        if capture is None:
            return False
        return capture.seconds_since_sound < self._config.proactive.audio_quiet_s

    def apply_settings(self) -> None:
        """Take up settings changed while running.

        Most settings are read where they are used, so changing the shared
        config is enough. These were copied into objects at startup.
        """
        p = self._config.proactive
        if self._attention is not None:
            self._attention.cooldown_s = p.cooldown_s
            self._attention.max_per_hour = p.max_per_hour
            self._attention.min_chars = p.min_chars
            self._attention.quiet_after_user_s = p.quiet_after_user_s
        if self._orchestrator is not None:
            self._orchestrator.min_time_on_page_s = p.min_time_on_page_s
            self._orchestrator.dwell_seconds = p.dwell_seconds
            self._orchestrator.max_remarks_per_page = p.max_remarks_per_page
            self._orchestrator.natural_moments = p.natural_moments
            self._orchestrator.media_min_s = p.media_min_s
            self._orchestrator.moment_wait_s = p.moment_wait_s
            self._orchestrator.long_stay_s = p.long_stay_s
            self._orchestrator.callbacks = p.callbacks
            if self._config.learning.enabled != (self._orchestrator.learning is not None):
                self._orchestrator.learning = self._build_learning()
        if self._companion is not None:
            self._companion.memory.enabled = self._config.memory.enabled

    def speech_level(self) -> float:
        """How loud the companion's voice is right now, 0-1, for the avatar's
        mouth. Read from the UI thread; the player writes a single float."""
        player = getattr(self._speaker, "player", None)
        return float(getattr(player, "level", 0.0)) if player is not None else 0.0

    def speech_shape(self) -> str | None:
        """The mouth shape of the speech sound playing now, or None."""
        shape_now = getattr(getattr(self._speaker, "player", None), "shape_now", None)
        return shape_now() if callable(shape_now) else None

    def reset_data(self, reset_id: str):
        """A reset button: move that data to the archive (core/reset.py), and
        forget what the running companion holds of it. Only the learned counts
        are held in memory; the other stores read their files each time."""
        from core import reset

        learning = getattr(self._orchestrator, "learning", None)
        if reset_id == "learning" and learning is not None:
            return learning.forget(lambda: reset.archive(self._config, reset_id))
        return reset.archive(self._config, reset_id)

    def shutdown(self) -> None:
        self._cancel.set()
        self._queue.put(_STOP)
        self.wait(5000)

    # -- worker thread --------------------------------------------------------

    def run(self) -> None:
        try:
            import comtypes

            # UIA lives in this thread; COM has to be initialised here too.
            comtypes.CoInitialize()
        except Exception:  # pragma: no cover -- comtypes absent or already init
            log.debug("CoInitialize skipped", exc_info=True)

        try:
            from core.companion import build_companion

            self._companion = build_companion(self._config, image_path=self._image_path)
            self._companion.llm.health_check()
        except Exception as exc:
            log.error("worker failed to start", exc_info=True)
            self.ready.emit(str(exc))
            return

        # Voice is optional: a missing voice model or no audio device must
        # degrade to a silent-but-working companion, not a broken one.
        try:
            from core.companion import build_speaker

            self._speaker = build_speaker(self._config)
        except Exception as exc:
            log.warning("voice unavailable: %s", exc)
            self.voice_unavailable.emit(str(exc))

        # System-audio capture taps the OUTPUT device, so it hears the
        # companion's own speech. Tell the transcriber when that is happening so
        # it can drop those chunks instead of transcribing the companion and
        # feeding its own words back as "what was heard".
        if self._speaker is not None and self._companion.audio is not None:
            speaker = self._speaker
            self._companion.audio.speaking_probe = lambda: speaker.is_speaking
            log.debug("echo suppression wired: audio capture ignores own speech")


        # Likewise optional: without speech recognition you can still type.
        try:
            from core.companion import build_stt

            self._stt = build_stt(self._config)
        except Exception as exc:
            log.warning("speech recognition unavailable: %s", exc)
            self.speech_unavailable.emit(str(exc))

        self._build_orchestrator()
        # speech.language from the config, or a pick made while starting up.
        self.set_speech_language(self._language)
        self.ready.emit("")

        # After `ready`, not before: the window becomes usable immediately and
        # the model loads while the user is still reading or typing.
        if self._config.ollama.warm_up:
            self._companion.llm.warm_up()
            self._warm_up_embedder()

        watching = self._companion.ambient is not None
        # Always wait with a timeout, even when not watching the screen: the
        # idle branch is also where the end of speech gets noticed.
        interval = self._config.ambient.sample_interval_s if watching else 0.25

        while True:
            try:
                # Waiting with a timeout doubles as the ambient clock: idle time
                # becomes sampling time, and sampling naturally stops while a
                # question is being answered.
                item = self._queue.get(timeout=interval)
            except queue.Empty:
                if watching:
                    self._ambient_tick()
                self._check_timers()
                self._pump_events()
                self._maybe_reflect()
                self._publish_busy()
                continue

            if item is _STOP:
                break
            if item is self._REMARK_NOW:
                self._remark_now()
                continue
            if isinstance(item, tuple) and item and item[0] == "preview":
                self._preview(item[1], item[2])
                continue
            if isinstance(item, _Transcribe):
                self._transcribe(item.audio, quiet=item.quiet)
                continue
            try:
                self._answer(str(item))
            except Exception as exc:  # never let one bad question kill the thread
                log.error("question failed", exc_info=True)
                self.failed.emit(str(exc))
                self.finished_answer.emit()

        if self._orchestrator is not None:
            self._orchestrator.leave()  # the page on screen at quit is a visit too
        for closer in (self._speaker, self._stt, self._companion):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:
                log.debug("%s close failed", type(closer).__name__, exc_info=True)

    def _transcribe(self, audio, quiet: bool = False) -> None:
        """Turn recorded audio into a question, and ask it."""
        if self._stt is None:
            return
        try:
            text = self._stt.transcribe(audio)
        except Exception as exc:
            log.warning("transcription failed", exc_info=True)
            self.failed.emit(f"Could not transcribe: {exc}")
            return

        if not text:
            # Silence, a cough, a mis-hit key. Say so rather than asking the
            # model an empty question -- unless listening hands-free, where
            # that is ordinary and a notice each time would be noise.
            if not quiet:
                self.heard.emit("")
            return

        # Answer in the language it was asked in, with a voice that can
        # pronounce it. The language is already known from recognition, which
        # beats guessing it from the answer text afterwards.
        spoken_language = getattr(self._stt, "last_language", None)
        if self._speaker is not None and spoken_language:
            self._speaker.set_language(spoken_language)

        self.heard.emit(text)
        if self._orchestrator is not None:
            # Typed questions do this in ask(). Spoken ones never pass through
            # ask(), so after a voice exchange nothing started the quiet period
            # and a remark could follow the spoken answer immediately.
            self._orchestrator.note_user_message()
        self._answering.set()
        self._publish_busy()
        try:
            self._answer(text)
        except Exception as exc:
            log.error("spoken question failed", exc_info=True)
            self.failed.emit(str(exc))
            self._answering.clear()
            self.finished_answer.emit()

    def _build_orchestrator(self) -> None:
        """Wire the event loop that decides when to speak unprompted.

        Built even with `proactive.enabled` off, because "say something about
        this" uses it on request. Only unprompted remarks check the switch, in
        `_pump_events`.
        """
        from core.attention import AttentionPolicy
        from core.orchestrator import Orchestrator, load_persona

        p = self._config.proactive
        try:
            persona = load_persona(self._config.root / p.persona_file)
        except OSError as exc:
            # Not fatal: answering questions doesn't need a persona.
            log.warning("unprompted remarks disabled, persona unreadable: %s", exc)
            return
        self._attention = AttentionPolicy(
            cooldown_s=p.cooldown_s,
            max_per_hour=p.max_per_hour,
            min_chars=p.min_chars,
            quiet_after_user_s=p.quiet_after_user_s,
        )
        self._orchestrator = Orchestrator(
            learning=self._build_learning(),
            outcome_window_s=self._config.learning.outcome_window_s,
            llm=self._companion.llm,
            policy=self._attention,
            max_words=p.max_words,
            memory=self._companion.memory,
            temperature=p.temperature,
            min_time_on_page_s=p.min_time_on_page_s,
            dwell_seconds=p.dwell_seconds,
            max_remarks_per_page=p.max_remarks_per_page,
            persona=persona,
            # Checks vision.enabled on every call, so the setting applies live.
            screenshot=getattr(self._companion, "screenshot", None),
            vision_edge=self._config.vision.max_image_edge,
            min_picture_colours=self._config.vision.min_picture_colours,
            on_leave=self._record_visit,
            natural_moments=p.natural_moments,
            media_min_s=p.media_min_s,
            audio_quiet_s=p.audio_quiet_s,
            page_end_percent=p.page_end_percent,
            moment_wait_s=p.moment_wait_s,
            long_stay_s=p.long_stay_s,
            parting_window_s=p.parting_window_s,
            about=self._about_facts,
            recall=self._recall_earlier,
            shared=self._shared_moments,
            callbacks=p.callbacks,
            watching=self._watching_screen,
        )
        self._orchestrator.reply_language = self._language
        log.info(
            "orchestrator ready (a page can be remarked on after %.0fs on it)",
            p.min_time_on_page_s,
        )
        if p.hold_while_audio_plays and self._companion.audio is None:
            log.info(
                "remarks cannot wait for videos to pause: system-audio "
                "listening (audio.enabled) is off"
            )

    def _build_learning(self):
        """What was learned about where remarks are welcome, or None when off."""
        settings = self._config.learning
        if not settings.enabled:
            return None
        from core.learning import Learning

        learning = Learning(self._config.root / settings.file,
                            min_allowance=settings.min_allowance,
                            max_allowance=settings.max_allowance)
        log.info("learned so far: %s", learning.summary())
        return learning

    def note_remark_rating(self, page: str, move: str, trigger: str, rating: str) -> None:
        """A thumbs up or down on a remark, for learning where remarks are welcome."""
        if self._orchestrator is not None:
            self._orchestrator.note_rating(page, move, trigger, rating)

    def _maybe_reflect(self, now: float | None = None) -> bool:
        """Once a day, while the user is away and nothing is using the model,
        update the facts file. Returns whether it ran."""
        reflection = self._config.reflection
        if not reflection.enabled or self._companion is None or self.is_busy():
            return False
        now = time.time() if now is None else now
        if now - self._last_user_at < reflection.idle_min * 60:
            return False
        from datetime import datetime

        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if self._reflected_on == today:
            return False
        self._reflected_on = today  # once a day, even if it fails
        from core import reflection as reflecting
        from core.activity import ActivityLog

        try:
            report = reflecting.reflect(
                self._companion.llm,
                self._config.root / reflection.file,
                ActivityLog(self._config.root / self._config.activity.folder),
                list(self._said),
                now=datetime.fromtimestamp(now),
                min_visits=reflection.min_visits,
                days=reflection.days,
                max_facts=reflection.max_facts,
            )
        except Exception:
            log.warning("daily reflection failed", exc_info=True)
            return True
        for text, why in report.rejected:
            log.info("reflection kept out %r: %s", text, why)
        return True

    def _about_facts(self, candidate) -> list[str]:
        """Facts from about_you.md that bear on this page -- sharing words with
        its title or description -- for a remark's context."""
        if not self._config.reflection.use_in_replies:
            return []
        from core.reflection import about_facts, relevant_facts

        context = candidate.context
        summary = candidate.activity.summary if candidate.activity else ""
        return relevant_facts(
            about_facts(self._config.root / self._config.reflection.file),
            f"{context.window_title or ''} {summary}",
        )

    def _warm_up_embedder(self) -> None:
        """Load the embedding model and embed the liked moments now:
        a cold load was 4-23 s, which would otherwise land on the first answer."""
        embedder = getattr(self._companion, "embedder", None)
        if embedder is None:
            return
        from core.moments import document, liked_moments
        from core.ratings import RatingStore

        try:
            moments = liked_moments(RatingStore(self._config.root / self._config.ratings.file).ratings())
            embedder.warm_up([document(m) for m in moments])
        except Exception:  # never fatal: matching falls back to words
            log.debug("embedding warm-up failed", exc_info=True)

    def _shared_moments(self, candidate) -> list[tuple[str, str]]:
        """Replies they liked that bear on this page, for a remark:
        sharing words with its title or description."""
        ratings = self._config.ratings
        if not (ratings.enabled and ratings.remember_moments):
            return []
        from core.moments import describe, liked_moments, relevant_moments
        from core.ratings import RatingStore

        context = candidate.context
        summary = candidate.activity.summary if candidate.activity else ""
        companion = getattr(self, "_companion", None)
        moments = relevant_moments(
            liked_moments(RatingStore(self._config.root / ratings.file).ratings()),
            summary, context.window_title or "", embedder=getattr(companion, "embedder", None),
        )
        # A liked remark first: remarks are voted on for their reason, which is
        # what a new remark can build on.
        moments.sort(key=lambda m: m.kind != "remark")
        return [(describe(m), m.reply) for m in moments]

    def _recall_earlier(self, candidate) -> str | None:
        """A page from an earlier day related to this one, for a remark to
        connect to (proactive.callbacks)."""
        activity = self._config.activity
        if not activity.enabled:
            return None
        from datetime import datetime

        from core.activity import ActivityLog
        from core.orchestrator import clean_title

        title = clean_title(candidate.context.window_title or "")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for visit in ActivityLog(self._config.root / activity.folder).search(title, end=today):
            if visit.title.casefold() != title.casefold():
                return f"{visit.title} ({visit.started:%A})"
        return None

    def _record_visit(self, candidate, ended_at: float) -> None:
        """A page was left: add it to the activity log, when the log is on.

        Only pages that reached the orchestrator, so a screen with a window on
        the privacy list -- which ambient watching never reads -- leaves nothing.
        """
        activity = self._config.activity
        if not activity.enabled or ended_at - candidate.arrived_at < activity.min_seconds:
            return
        from datetime import datetime

        from core.activity import ActivityLog, Visit
        from core.orchestrator import clean_title

        context = candidate.context
        ActivityLog(self._config.root / activity.folder).record(Visit(
            started=datetime.fromtimestamp(candidate.arrived_at),
            ended=datetime.fromtimestamp(ended_at),
            app=context.app_name or "",
            title=clean_title(context.window_title or ""),
            activity=candidate.activity.summary if candidate.activity else "",
            remarks=candidate.remarks,
            outcome=candidate.outcome or ("ignored" if candidate.remarks else ""),
        ))

    def _check_timers(self) -> None:
        """Announce any timers that have come due.

        Polled from the idle loop rather than fired from a timer thread, so the
        announcement happens where speaking and updating the window are already
        safe.
        """
        store = getattr(self._companion.tools, "timers", None) if self._companion else None
        if store is None:
            return
        for timer in store.due():
            message = f"Timer finished: {timer.label}."
            log.info("%s", message)
            self.timer_fired.emit(message)
            # Remembered for the same reason remarks are: "cancel it" or
            # "set another one" is a reply to this, and without a record the
            # model has no idea what "it" refers to.
            self._companion.memory.add_remark(message)
            self._interrupted = None  # the announcement is now the latest thing said
            if self._speaker is not None and not self._speaker.is_speaking:
                # Only speak if it isn't already mid-answer -- an alarm talking
                # over a reply is worse than an alarm a few seconds late.
                self._speaker.begin_utterance()
                self._speaker.feed(message + " ")
                self._speaker.flush()

    def _publish_busy(self) -> None:
        """Emit busy_changed on transitions, so the UI can track speech ending."""
        busy = self.is_busy()
        if busy != self._was_busy:
            self._was_busy = busy
            self.busy_changed.emit(busy)

    def _ambient_tick(self) -> None:
        """One cheap sample. Emits only when a full re-read actually happened."""
        try:
            context = self._companion.ambient_tick()
        except Exception:
            log.debug("ambient tick failed", exc_info=True)
            return
        if context is None:
            return
        self.watching.emit(_watching_status(context))
        self._maybe_remark(context)

    def _maybe_remark(self, context) -> None:
        """Tell the orchestrator the screen settled. Free, and never speaks.

        Every settled read counts, including a scroll on the same page: that is
        the user stopping somewhere, which the old event queue threw away.
        Whether anything gets said is decided in the pump.
        """
        if self._orchestrator is not None:
            self._orchestrator.observe(context)

    def _pump_events(self) -> None:
        """Give the orchestrator its tick: maybe one remark, usually nothing."""
        if self._orchestrator is None or not self._config.proactive.enabled:
            return

        remark = self._orchestrator.poll(
            busy=self.is_busy(), hold=self._audio_is_playing(), sound=self._sound_stretch()
        )
        if remark is not None:
            self._deliver_remark(remark)

    def _preview(self, session: int, audio) -> None:
        """What the mic button has heard so far. Shown in the window, never asked.

        Always answers, even with nothing, so the window knows it may send the
        next preview.
        """
        text = ""
        if self._stt is not None:
            try:
                text = self._stt.transcribe(audio) or ""
            except Exception:
                log.debug("preview transcription failed", exc_info=True)
        self.previewed.emit(session, text)

    def _remark_now(self) -> None:
        """"Say something about this": a fresh read, then a remark or a short no."""
        if self._orchestrator is None:
            self.remark_declined.emit(
                "Remarks are unavailable: the persona file couldn't be read."
            )
            return
        from core.errors import PrivacyBlocked

        try:
            context = self._companion.observe()
        except PrivacyBlocked:
            self.remark_declined.emit(
                "Not looking: a window on the privacy list is open."
            )
            return
        except Exception as exc:
            log.warning("could not read the screen for a remark", exc_info=True)
            self.remark_declined.emit(f"Couldn't read the screen: {exc}")
            return

        remark, why_not = self._orchestrator.remark_now(context)
        if remark is not None:
            self._deliver_remark(remark)
            return
        log.info("asked for a remark; declined: %s", why_not)
        self.remark_declined.emit(f"Nothing worth saying about this one ({why_not}).")
        self._say("Nothing worth saying about this one.")

    def _deliver_remark(self, remark) -> None:
        self.remarked.emit(remark.text, remark.why, remark)
        # Now the latest thing it said; an interruption of something earlier no
        # longer describes where the conversation is.
        self._interrupted = None
        self._emit_mood("", remark.text)
        self._say(remark.text)

    #: The reply's opening decides its mood once this much of it has arrived --
    #: enough to hold "Congratulations" or "I'm sorry", early enough to show
    #: before the first sentence is spoken.
    MOOD_AFTER_CHARS = 60

    def _emit_mood(self, message: str, reply: str) -> str:
        """Decide the mood of an exchange in code and hand it to the avatar."""
        from core.mood import mood_of

        mood = mood_of(message, reply)
        self.mood.emit(mood)
        return mood

    def _say(self, text: str) -> None:
        """Speak a remark, if remarks are spoken. The reason never comes here."""
        if self._speaker is not None and self._config.proactive.speak_aloud:
            self._speaker.begin_utterance()
            self._speaker.feed(text + " ")
            self._speaker.flush()

    def _sound_stretch(self) -> tuple[float, float] | None:
        """(latest stretch of sound, seconds since it stopped), or None without
        system-audio listening. How a video ending is noticed."""
        capture = getattr(getattr(self._companion, "audio", None), "capture", None)
        if capture is None:
            return None
        return (getattr(capture, "seconds_of_sound", 0.0), capture.seconds_since_sound)

    def _audio_is_playing(self) -> bool:
        """Is the user listening to something? Then remarks wait for it to stop.

        Read off the output device, so it covers anything audible -- a video,
        music, a call -- and the companion's own voice too, which only ever
        delays a remark by `audio_quiet_s` after it finishes speaking.
        """
        proactive = self._config.proactive
        if not proactive.hold_while_audio_plays:
            return False
        capture = getattr(getattr(self._companion, "audio", None), "capture", None)
        if capture is None:
            return False
        return capture.seconds_since_sound < proactive.audio_quiet_s

    def _watching_screen(self) -> bool:
        """They asked it to watch, and the settings allow screenshots for
        remarks. Asked at every remark, so switching either setting off stops
        the screenshots at once."""
        vision = self._config.vision
        return bool(getattr(self._companion, "watching", False)
                    and vision.enabled and vision.watch_remarks)

    def _answer(self, question: str) -> None:
        assert self._companion is not None
        # Cleared here, where every answer begins, rather than at each entry
        # point. It was previously cleared only in ask(), so a spoken question
        # arriving after an interruption inherited the cancel flag and bailed
        # out of its own token loop before emitting anything -- the question
        # appeared, the answer never did.
        self._cancel.clear()
        self._last_user_at = time.time()
        from datetime import datetime

        from core.vision import asks_to_look

        self._said =(self._said + [(datetime.now(), question)])[-200:]
        self.thinking.emit()

        try:
            # Asked to look: the text is read afresh too, so it agrees with the
            # screenshot taken for this answer instead of describing an earlier page.
            context = self._companion.observe(
                max_age_s=0.0 if asks_to_look(question) else self._config.ambient.max_cache_age_s
            )
        except PrivacyBlocked as exc:
            self.failed.emit(
                f"{exc}\nNothing was captured. Close that window, or adjust "
                f"`privacy` in config.yaml."
            )
            self.finished_answer.emit()
            return
        except CompanionError as exc:
            self.failed.emit(str(exc))
            self.finished_answer.emit()
            return

        if context.is_empty:
            self.failed.emit(
                "I couldn't read any text on that screen. Try --probe to see "
                "which window is being read."
            )
            self.finished_answer.emit()
            return

        self.observed.emit(_status(context))

        was_watching = getattr(self._companion, "watching", False)
        answer = self._companion.ask(
            question, context=context, interrupted=self._take_interruption()
        )
        if getattr(self._companion, "watching", False) != was_watching:
            self.watching_screen.emit(self._companion.watching)
        if self._speaker is not None:
            self._speaker.begin_utterance()
        pieces: list[str] = []
        mood_given = False
        try:
            for piece in answer.chunks:
                if self._cancel.is_set():
                    # Closing the generator runs its `finally`, so the partial
                    # answer still reaches memory.
                    answer.chunks.close()
                    break
                pieces.append(piece)
                self.chunk.emit(piece)
                if not mood_given and sum(len(p) for p in pieces) >= self.MOOD_AFTER_CHARS:
                    self._emit_mood(question, "".join(pieces))
                    mood_given = True
                if self._speaker is not None:
                    self._speaker.feed(piece)
        except CompanionError as exc:
            self.failed.emit(str(exc))
        finally:
            if self._speaker is not None and not self._cancel.is_set():
                self._speaker.flush()
            # Generation is done, but speech usually is not -- busy stays true
            # until the idle poll sees the audio queue drain.
            self._answering.clear()
            self._publish_busy()
            reply = "".join(pieces).strip()
            if reply and not mood_given and not self._cancel.is_set():
                self._emit_mood(question, reply)  # a reply too short to decide it earlier
            if reply:
                self.answered.emit({
                    "question": question,
                    "reply": reply,
                    "page": context.window_title or context.app_name or "",
                })
            self.finished_answer.emit()


def _status(context) -> str:
    timings = "  ".join(f"{k} {v:.0f}ms" for k, v in context.timings_ms.items())
    where = context.window_title or context.app_name or "screen"
    age = "" if context.age_seconds < 1 else f" · {context.age_seconds:.0f}s ago"
    return f"{context.source} · {len(context.text)} chars · {where}{age} · {timings}"


def _watching_status(context) -> str:
    where = context.window_title or context.app_name or "screen"
    return f"watching · read {len(context.text)} chars from {_short(where)}"


def _short(text: str, limit: int = 44) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
