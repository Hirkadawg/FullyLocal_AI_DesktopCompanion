"""The orchestrator.

`Companion` is the application's real API. The CLI is a thin shell over it; the
GUI and the voice loop call the same two methods rather than reimplementing the
pipeline.

Prompt assembly lives here, not in the LLM provider, so that swapping the local
model for a cloud one changes nothing about how questions are framed.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterator

from core.ambient import AmbientObserver, Verdict
from core.config import AppConfig
from core.errors import PrivacyBlocked
from core.logging import get_logger
from core.memory import ConversationMemory
from core.privacy import PrivacyGuard
from core.types import Answer, Delivery, Message, ScreenContext, ToolCall
from modules.tools.base import ToolRegistry
from core.vision import asks_to_look, asks_visual, encode, wants_image, watch_request
from modules.tools.requests import asked_for, note_request, offered

#: What the model is told when a call to a state-changing tool is refused.
_REFUSED = (
    "Not done: their message didn't ask for this, so nothing was changed. "
    "Don't mention it."
)
from core.winapi import visible_windows
from modules.capture.screen import (
    MSSCapture,
    ScreenSource,
    StaticImageSource,
    WindowCapture,
)
from modules.llm.base import LLMProvider
from modules.llm.ollama_client import OllamaLLM
from modules.llm.prompt_log import PromptLog
from modules.perception.base import PerceptionSource
from modules.perception.composite import FallbackPerception
from modules.perception.differ import FrameDiffer
from modules.perception.ocr import RapidOCRSource
from modules.perception.uia import UIAutomationSource

log = get_logger(__name__)


class Companion:
    """Capture -> perceive -> reason, with a privacy gate in front."""

    #: The language pinned with the lang button (worker.set_speech_language):
    #: every answer is in it. None follows the language of each question.
    reply_language: str | None = None
    #: They asked it to watch the screen ("watch my screen") and haven't said
    #: "stop watching". Remarks then get a fresh screenshot each, when the
    #: settings allow (worker._watching_screen). In memory only: closing ends it.
    watching: bool = False
    #: Whether the last answer was sent the screen: "why?" and "go on" follow it.
    screen_last_turn: bool = False

    def __init__(
        self,
        config: AppConfig,
        screen: ScreenSource,
        perception: PerceptionSource,
        llm: LLMProvider,
        privacy: PrivacyGuard,
        memory: ConversationMemory | None = None,
        ambient: AmbientObserver | None = None,
        tools: ToolRegistry | None = None,
        audio: "AudioTranscriber | None" = None,
    ) -> None:
        self.tools = tools
        self.audio = audio
        music = tools.get("analyse_music") if tools is not None else None
        if music is not None:
            # It hears through the same capture the transcriber uses.
            music.audio = audio
        self.config = config
        self.screen = screen
        self.perception = perception
        self.llm = llm
        self.privacy = privacy
        self.ambient = ambient
        # `is None`, not `or`: ConversationMemory defines __len__, so an empty
        # one is falsy and `or` would silently swap in a disabled memory --
        # which is every conversation's first turn.
        self.memory = memory if memory is not None else ConversationMemory(
            enabled=False
        )
        self.last_context: ScreenContext | None = None
        #: Matches liked moments by meaning; None matches by words.
        self.embedder = build_embedder(config)

    def observe(self, max_age_s: float = 0.0) -> ScreenContext:
        """Read the screen, refusing if a blocked window is visible.

        The privacy check runs before the screenshot is taken, so blocked
        content is never captured in the first place rather than captured and
        then discarded. It runs even when the answer comes from cache: a
        password manager appearing must block the question now, regardless of
        what was on screen when the cache was filled.

        `max_age_s` allows reusing a recent ambient read instead of perceiving
        again, which is what makes questions answer instantly while watching.
        """
        self._guard()

        cached = self.last_context
        if max_age_s > 0 and cached is not None and cached.age_seconds <= max_age_s:
            log.debug("using cached context (%.1fs old)", cached.age_seconds)
            return cached

        return self.refresh()

    def refresh(self) -> ScreenContext:
        """Perceive the screen now and update the cache."""
        self._guard()
        context = self.perception.read()
        self.last_context = context
        return context

    def _guard(self) -> None:
        if not self.screen.is_live:
            return
        block = self.privacy.check(visible_windows(within=self.screen.bounds()))
        if block is not None:
            # Drop anything already cached: it is no longer safe to answer from
            # a screen that now has a blocked window on it.
            self.last_context = None
            raise PrivacyBlocked(block)

    def ambient_tick(self) -> ScreenContext | None:
        """Advance the watch loop by one cheap sample.

        Returns a fresh ScreenContext when the screen settled and was re-read,
        otherwise None. Callers drive this from an idle loop; the expensive path
        runs only on a DUE verdict.
        """
        if self.ambient is None:
            return None
        try:
            self._guard()
        except PrivacyBlocked:
            # Stay quiet while a blocked window is up, and treat whatever is on
            # screen afterwards as new.
            self.ambient.invalidate()
            return None

        if self.ambient.tick() is not Verdict.DUE:
            return None
        return self.refresh()

    def ask(
        self,
        question: str,
        context: ScreenContext | None = None,
        interrupted: Delivery | None = None,
    ) -> Answer:
        """Answer a question about the screen.

        Pass an existing `context` to reuse an observation the caller already
        made -- otherwise this captures afresh. Cached perception uses the same
        door.

        `interrupted` describes the previous reply if the user stopped it while
        it was being read aloud, so this turn knows where speech got to.
        """
        if context is None:
            context = self.observe()
        note = self._save_requested_note(question, context)
        watch = self._update_watching(question)
        screen = self.wants_screen(question, context)
        images = self._images_for(question, context) if screen else ()
        messages = self.build_messages(
            question, context, interrupted=interrupted, note=note,
            seeing=self._seeing_block(question, bool(images), watch),
            from_screenshot=bool(images) and asks_to_look(question),
            screen=screen,
        )
        self.screen_last_turn = screen
        if images:
            # On this turn's message only: memory records the words, not this.
            messages[-1] = replace(messages[-1], images=images)
        return Answer(
            context=context,
            chunks=self._remember(question, self._generate(messages, question)),
        )

    def wants_screen(self, question: str, context: ScreenContext) -> bool:
        """Whether this message goes to the model with the screen.

        With the screen on every message, 35 of 48 everyday messages were pulled
        onto it. Off in the settings, every message gets it, as before.
        """
        if not self.config.llm.screen_only_when_relevant:
            return True
        if asks_to_look(question) or asks_visual(question) or watch_request(question):
            return True
        from core.relevance import about_screen, subject_words

        if len((context.text or "").strip()) < self.config.vision.thin_text_chars and subject_words(question):
            # Next to no text -- a picture, a video: nothing to match the words
            # against, so a message with a subject of its own may be about it.
            return True
        return about_screen(question, context.text or "", context.window_title or "",
                            after_remark=self.memory.last_was_remark(),
                            screen_last_turn=self.screen_last_turn)

    def _about_block(self, question: str = "") -> str:
        """The facts in about_you.md that bear on this question.

        Only those: a question about the user gets them all, any other only the
        facts sharing its words -- usually none.
        """
        reflection = self.config.reflection
        if not reflection.use_in_replies:
            return ""
        from core.reflection import about_facts, relevant_facts

        facts = relevant_facts(
            about_facts(self.config.root / reflection.file)[: reflection.max_facts], question
        )
        if not facts:
            return ""
        listed = "\n".join(f"- {fact}" for fact in facts)
        return (
            "[ABOUT THEM — things they have said or keep coming back to. Use only "
            f"when it fits; don't mention this list.]\n{listed}\n[/ABOUT THEM]\n\n"
        )

    def _moments_block(self, question: str, context: ScreenContext) -> str:
        """Replies they gave a 👍 that bear on this question or page."""
        ratings = self.config.ratings
        if not (ratings.enabled and ratings.remember_moments):
            return ""
        from core.moments import asks_to_remember, describe, liked_moments, relevant_moments
        from core.ratings import RatingStore

        # Matched on the question alone, not the page. With the page's title too,
        # on a page about the same topic 5 ordinary answers in 12 brought the
        # moment up, 4 of them as a paragraph tacked on; a note asking for a
        # short clause only made it 3 and 2. Remarks match on the page instead.
        chosen = relevant_moments(
            liked_moments(RatingStore(self.config.root / ratings.file).ratings()), question,
            embedder=getattr(self, "embedder", None),
        )
        if not chosen:
            return ""
        listed = "\n".join(f"- {describe(moment)}" for moment in chosen)
        use = ("They are asking about before: answer from these alone. The screen shows today, not what "
               "you talked about." if asks_to_remember(question) else
               "Answer what they ask. If one truly connects, you may touch on it in a short clause inside "
               "that answer, never as a sentence or paragraph of its own.")
        return (f"[SHARED MOMENTS — what you remember from earlier conversations with them. {use} "
                f"Keep who said what.]\n{listed}\n[/SHARED MOMENTS]\n\n")

    def _audio_lines(self) -> list:
        """What system audio was heard lately, newest first, as timed lines."""
        recent = getattr(self.audio, "recent", None)
        if not callable(recent):
            return []
        return recent(minutes=self.config.audio.context_minutes,
                      max_chars=self.config.audio.max_transcript_chars)

    def now_playing(self) -> list:
        """What Windows' media controls report playing (modules/perception/media.py)."""
        from modules.perception.media import now_playing

        return now_playing()

    def _media_block(self, question: str) -> str:
        """The exact track playing, for a question about music. Only then: asking
        Windows costs ~0.3 s."""
        if not self.config.perception.now_playing or not _MUSIC_QUESTION.search(question or ""):
            return ""
        from modules.perception.media import describe

        return describe(self.now_playing())

    def _length_block(self, question: str) -> str:
        """How long this answer should be, in words (core/length.py). In this
        turn only: a standing prompt rule once broke how "Yes." was answered."""
        from core.length import length_note

        return length_note(question, after_remark=self.memory.last_was_remark(),
                           length=self.config.llm.answer_length)

    def _switched_off(self) -> set[str]:
        """Tools the settings have switched off, directly or with their feature."""
        off = set(self.config.tools.disabled)
        if not self.config.activity.enabled:
            off.add("search_activity")
        if not (self.config.music.enabled and self.config.audio.enabled):
            off.add("analyse_music")
        return off

    def screenshot(self):
        """The screen as an image for the model, or None when vision is off.

        The privacy check runs first, exactly as for reading text: with a
        blocked window on screen this raises before a single pixel is read.
        """
        if not self.config.vision.enabled:
            return None
        self._guard()
        return self.screen.grab()

    def _images_for(self, question: str, context: ScreenContext) -> tuple[bytes, ...]:
        """A screenshot for this answer, when the rules in core/vision.py want one."""
        vision = self.config.vision
        if not vision.enabled or not wants_image(
            vision.when, question, context.text, vision.thin_text_chars
        ):
            return ()
        looking = asks_to_look(question)
        try:
            # Asked to look: taken now. The image the text was read from can be
            # seconds old, and "look again" usually follows a wrong answer.
            shot = (context.image if context.image is not None and not looking
                    else self.screenshot())
        except PrivacyBlocked:
            log.info("no screenshot: a window on the privacy list is on screen")
            return ()
        except Exception:
            log.warning("could not take a screenshot", exc_info=True)
            return ()
        if shot is None:
            return ()
        log.info(
            "attaching a screenshot (%s)",
            "asked to look" if looking
            else "a visual question" if asks_visual(question) else "little text on screen",
        )
        return (encode(shot, vision.max_image_edge),)

    def _update_watching(self, question: str) -> str | None:
        """Start or stop watching the screen for remarks, when the message asks.

        Returns "started", "stopped", "not allowed" (the settings don't let it
        watch), or None when the message didn't ask.
        """
        request = watch_request(question)
        if request is None:
            return None
        if request == "stop":
            self.watching = False
            log.info("stopped watching the screen for remarks")
            return "stopped"
        vision = self.config.vision
        if not (vision.enabled and vision.watch_remarks):
            log.info("asked to watch the screen, but the settings don't allow it")
            return "not allowed"
        self.watching = True
        log.info("watching the screen for remarks, until asked to stop")
        return "started"

    def _seeing_block(self, question: str, looked: bool, watch: str | None) -> str:
        """What it can truly see, when they ask it to look or to watch.

        Measured before: asked to look while the screen's text was a moment
        stale, 0 of 6 answers came from the screen -- the two with a screenshot
        attached still repeated the text's page. With seeing switched off,
        "can you see my screen?" got "Yes, I can see your screen" 5 of 6.
        """
        parts = []
        if asks_to_look(question) or watch in ("started", "not allowed"):
            vision = self.config.vision
            if looked:
                parts.append(
                    "[SCREENSHOT — they asked you to look: the attached screenshot of their screen "
                    "was taken just now. Answer from what it shows. Where anything said earlier "
                    "disagrees with it, the screenshot is right: say what it shows now.]")
            elif not vision.enabled or vision.when == "never":
                parts.append(
                    "[CAN'T SEE — they asked you to look, but seeing the screen is switched off in the "
                    "settings (\"Look at the screen, not only read it\"). You have only the text read "
                    "from the screen: say so plainly, and never say you can see it.]")
            else:
                parts.append(
                    "[CAN'T SEE — they asked you to look, but no screenshot could be taken just now. "
                    "You have only the text read from the screen: say so, and never say you can see it.]")
        if watch == "started":
            parts.append(
                "[WATCHING — from now on, until they say \"stop watching\", each remark you make on "
                "your own comes with a fresh screenshot, used for that remark and then discarded. "
                "Tell them so in a few words.]")
        elif watch == "not allowed":
            parts.append(
                "[NOT WATCHING — they asked you to keep watching their screen, but that is switched "
                "off in the settings (\"Watch the screen for remarks when asked\"). Tell them so in a "
                "few words.]")
        elif watch == "stopped":
            parts.append(
                "[STOPPED WATCHING — your remarks go back to reading the screen's text, with no "
                "screenshots. Tell them so in a few words.]")
        return "".join(part + "\n\n" for part in parts)

    def _save_requested_note(self, question: str, context: ScreenContext) -> str | None:
        """Save a note if the message asks for one. The model never does this.

        Returns what was saved, "" if a note was asked for but there was nothing
        to save, or None if no note was asked for.
        """
        request = note_request(question)
        notebook = getattr(self.tools, "notebook", None)
        if request is None or notebook is None:
            return None
        if "save_notes" in self.config.tools.disabled:
            log.info("a note was asked for, but notes are switched off")
            return None
        text = request.text or self._last_reply()
        if not text:
            log.info("a note was asked for, but there was nothing to save")
            return ""
        notebook.append(text, context.window_title or context.app_name)
        log.info(
            "note saved %s: %s",
            "as dictated" if request.text else "from the last reply",
            text[:80],
        )
        return text

    def _last_reply(self) -> str:
        """What the companion said last: the "that" in "note that down"."""
        for message in reversed(self.memory.history()):
            if message.role == "assistant" and message.content.strip():
                return message.content.strip()
        return ""

    def _generate(self, messages: list[Message], question: str = "") -> Iterator[str]:
        """Stream a reply, running any tools the model asks for along the way.

        Only the tools this message is about are offered (see
        modules/tools/requests.py); an ordinary reply is offered none. When
        tools are offered it costs nothing: tool calls arrive on their own
        chunks with empty content, so answers still stream (measured: 21 chunks
        with tools, versus one if tools forced a non-streaming call).
        """
        specs = self._offered_tools(question)
        rounds = self.config.tools.max_rounds if self.tools else 0
        # Results so far this turn, by call. qwen3.5:4b answered "Set a one
        # minute timer called tea." with set_timer three times over, which
        # would be three timers and three alerts.
        done: dict[tuple[str, str], str] = {}

        for _ in range(rounds + 1):
            calls: list[ToolCall] = []
            for chunk in self.llm.chat(
                messages,
                tools=specs,
                stream=self.config.llm.stream,
                collect_tool_calls=calls,
            ):
                yield chunk

            if not calls:
                return

            messages = list(messages)
            messages.append(
                Message(role="assistant", content="", tool_calls=tuple(calls))
            )
            refused = set()
            for call in calls:
                key = (call.name, repr(sorted(call.arguments.items())))
                if key in done:
                    # The same call again: say it is done rather than doing it
                    # twice, and take the tool away so the turn can't loop.
                    result = f"Already done, earlier in this turn: {done[key]}"
                    refused.add(call.name)
                else:
                    result = done[key] = self._run_tool(call, question)
                if result == _REFUSED:
                    refused.add(call.name)
                # Labelled as authoritative because the screen text is still in
                # context: without it the model has been seen answering "what
                # are my notes" with the one real note followed by lines lifted
                # off the screen.
                messages.append(
                    Message(
                        role="tool",
                        content=f"[{call.name} result — authoritative and "
                                f"complete]\n{result}",
                        tool_name=call.name,
                    )
                )

            if refused and specs:
                # Asking again for a refused tool is how a turn loops until it
                # runs out of rounds: take it off the table for this turn.
                specs = [s for s in specs if s.name not in refused] or None

        # Out of rounds. One last pass with no tools at all, so the question
        # still gets an answer rather than an apology for a loop.
        log.warning(
            "tool loop hit max_rounds (%d); answering without tools",
            self.config.tools.max_rounds,
        )
        for chunk in self.llm.chat(messages, stream=self.config.llm.stream):
            yield chunk

    def _offered_tools(self, question: str) -> list | None:
        """The tools this message is about, switched on; None when none are."""
        if not self.tools:
            return None
        disabled = self._switched_off()
        return [s for s in self.tools.specs()
                if s.name not in disabled and offered(s.name, question)] or None

    def _system_prompt(self, question: str, screen: bool, audio: bool, note: str | None) -> str:
        """The system prompt, whole or -- with llm.modular_system_prompt -- only the
        sections this message needs (core/prompt.py)."""
        text = self.config.system_prompt
        if not self.config.llm.modular_system_prompt:
            return text
        from core.prompt import asks_about_audio, compose

        needs = set()
        if screen:
            needs.add("screen")
        if self._offered_tools(question) or note is not None:
            needs.add("tools")
        if audio or asks_about_audio(question):
            needs.add("audio")
        history = self.memory.history()
        # Something said unasked: an assistant message with no question before it.
        if any(m.role == "assistant" and (i == 0 or history[i - 1].role != "user")
               for i, m in enumerate(history)):
            needs.add("remarks")
        return compose(text, needs)

    def _run_tool(self, call: ToolCall, question: str) -> str:
        """Run a tool call, unless it would change something nobody asked for.

        Tools that only look -- the clock, the list of timers, reading notes --
        always run. Tools that change things -- a timer that goes off later, the
        stopwatch -- run only if the user's message is about that. qwen3.5:4b
        was measured starting both after "Interesting.".
        """
        if call.name in self._switched_off():
            log.info("refused %s: switched off in settings", call)
            return "Not available: switched off in the settings."
        tool = self.tools.get(call.name)
        if tool is not None and tool.writes and not asked_for(
            call.name, question, call.arguments
        ):
            log.info("refused %s: the message didn't ask for it", call)
            return _REFUSED
        result = self.tools.run(call)
        log.info("tool %s -> %s", call, result[:80])
        return result

    def _remember(self, question: str, stream: Iterator[str]) -> Iterator[str]:
        """Pass tokens through, recording the finished reply into memory.

        The turn is recorded in a `finally` so an answer cut short -- by Ctrl-C,
        or by closing the window mid-stream -- is still remembered as far as it
        got. Losing it entirely would leave the next follow-up referring to
        something the model has no record of saying.
        """
        parts: list[str] = []
        try:
            for chunk in stream:
                parts.append(chunk)
                yield chunk
        finally:
            self.memory.add_turn(question, "".join(parts))

    def build_messages(
        self,
        question: str,
        context: ScreenContext,
        interrupted: Delivery | None = None,
        note: str | None = None,
        seeing: str = "",
        from_screenshot: bool = False,
        screen: bool = True,
    ) -> list[Message]:
        limit = self.config.llm.max_screen_chars
        screen_text = context.text
        if len(screen_text) > limit:
            screen_text = screen_text[:limit] + "\n...[screen text truncated]"

        header = []
        if context.app_name:
            header.append(f"Application: {context.app_name}")
        if context.window_title:
            header.append(f"Window title: {context.window_title}")
        prefix = ("\n".join(header) + "\n\n") if header else ""

        lines = self._audio_lines()
        audio_block = _audio_block(lines) if lines else ""

        if not screen:
            # Not about the screen (wants_screen). The window's title stays: a
            # greeting may still say "I see you're on YouTube".
            screen_block = ("[SCREEN TEXT not sent — their message isn't about what is on the screen. "
                            "Reply to what they said.]\n\n")
        elif from_screenshot:
            # Asked to look: the text is left out. With it there, qwen3.5:4b
            # answered from the text 0 of 6 times when the screenshot showed
            # something else, whatever the note said; without it, 6 of 6 from
            # the screenshot. The text can come from the focused window alone,
            # while the screenshot shows the screen they are asking about.
            screen_block = ("[SCREEN TEXT left out — they asked you to look: answer from the "
                            "attached screenshot]\n\n")
        else:
            screen_block = f"[SCREEN TEXT]\n{screen_text}\n[/SCREEN TEXT]\n\n"
        user = (
            f"{prefix}{screen_block}"
            f"{audio_block}"
            f"{self._media_block(question)}"
            f"{self._about_block(question)}"
            f"{self._moments_block(question, context)}"
            f"{_interruption_note(interrupted)}"
            f"{_note_block(note)}"
            f"{_why_block(question, self.memory.last_reason())}"
            f"{self._length_block(question)}"
            f"{_reply_language_block(question, lines, context.text if screen else '', self.reply_language)}"
            f"{seeing}"
            f"{_now_block(question)}"
            f"Question: {question}"
            f"{_reply_language_reminder(question, self.reply_language)}"
        )
        # Screen text rides only on the current turn: history carries the
        # dialogue, not stale screenshots of pages already scrolled past.
        return [
            Message(role="system", content=self._system_prompt(
                question, screen or from_screenshot, bool(audio_block), note)),
            *self.memory.history(),
            Message(role="user", content=user),
        ]

    def close(self) -> None:
        if self.audio is not None:
            self.audio.stop()
            self.audio.capture.stop()
        self.perception.close()
        close = getattr(self.screen, "close", None)
        if callable(close):
            close()


def _interruption_note(delivery: Delivery | None) -> str:
    """Where speech got to, for the turn straight after an interruption.

    The whole reply stays in memory because it is on screen -- trimming memory
    to the spoken part would let the model deny saying what the transcript
    shows. This says only how far the READING got, which is what "go on"
    depends on.

    What to do about it travels inside the note, not in prompts/system.md.
    Measured on the test fixture: as a standing section of the system prompt,
    the same rule disturbed an unrelated behaviour -- a bare "Yes." after a
    remark came back as a 31-word article summary in 1 of 3 tries. Inside the
    note it reaches only the turn it applies to, and every other turn keeps
    exactly the prompt it had. The note without the instruction didn't work
    either: "go on" resumed 0 times out of 2.
    """
    if delivery is None or delivery.complete:
        return ""
    heard, cut = delivery.heard, delivery.cut_during
    if heard and cut:
        where = (
            f'They heard up to "{_quote(heard[-1])}", and were cut off partway '
            f'through "{_quote(cut)}".'
        )
    elif heard:
        where = f'They heard up to "{_quote(heard[-1])}".'
    elif cut:
        where = f'They were cut off partway through the first sentence, "{_quote(cut)}".'
    else:
        where = "None of it had been read aloud yet."
    return (
        "[INTERRUPTED — they stopped your previous reply while it was being "
        f"read aloud. {where} The rest was shown in the window but not "
        "spoken. If they ask you to go on, continue from that point instead of "
        "starting over or moving on to something new. If they ask something "
        "else, just answer it and don't mention this.]\n\n"
    )


def _age(seconds: float) -> str:
    if seconds < 30:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} s ago"
    return f"{int(seconds // 60)} min ago"


def _audio_block(lines: list, now: float | None = None) -> str:
    """Recent system audio, NEWEST FIRST, each line with its age and language.

    It used to be five minutes, oldest first, undated, under the language of the
    last chunk alone. With Turkish heard a minute before an English question about
    an English article, the reply was Turkish 3 times in 5; timed lines and the
    reply-language note made it English 5 of 5.
    """
    import time

    now = time.time() if now is None else now
    rows = []
    for line in lines:
        language = f", {line.language}" if getattr(line, "language", None) else ""
        rows.append(f"- ({_age(now - line.at)}{language}) {line.text}")
    return ("[RECENT AUDIO — heard through the speakers, NEWEST FIRST, each line with when it was "
            "heard and its language. Only the last minute is what is playing now.]\n"
            + "\n".join(rows) + "\n[/RECENT AUDIO]\n\n")


#: A request to translate or to answer in a named language: the reply's language
#: is theirs to choose, so no reply-language note.
_TRANSLATION = re.compile(
    r"\b(translat\w*|in (english|turkish|german|french|spanish|japanese|chinese|italian|russian|arabic)|"
    r"say (that|it|this) in|çevir\w*|tercüme\w*|ingilizce\w*|türkçe\w*|almanca\w*|fransızca\w*)\b",
    re.IGNORECASE,
)


def _reply_language_reminder(question: str, chosen: str | None) -> str:
    """The pinned language again, after the question -- the last thing read.

    The note above the question wasn't enough. Reported: pinned to English,
    "benim hakkımda ne biliyorsun?" and "ekranımı görebiliyor musun" got Turkish.
    Measured on a screen showing a chat with some Turkish in it, ten questions
    each: the note alone English 6 of 10, adding a line to the system message
    8 of 10, this line 10 of 10.
    """
    if not chosen or _TRANSLATION.search(question or ""):
        return ""
    from modules.voice.language import LANGUAGE_NAMES

    return f"\n\n(Write your reply in {LANGUAGE_NAMES.get(chosen, chosen)}.)"


def _reply_language_block(question: str, lines: list, screen_text: str,
                          chosen: str | None = None) -> str:
    """Reply in the question's language -- said only when the audio or the screen
    is in another one, where it measurably went wrong.

    A language pinned with the lang button (`chosen`) is said on every turn and
    wins over the question's own language: the user asked for replies in it. A
    translation request still names its own language.
    """
    from modules.voice.language import LANGUAGE_NAMES, guess_language

    if _TRANSLATION.search(question or ""):
        return ""
    if chosen:
        name = LANGUAGE_NAMES.get(chosen, chosen)
        wrote = guess_language(question)
        if wrote and wrote != chosen:
            # Named, because it contradicts the system prompt's "answer in the
            # language the user asked in". Measured: "they have chosen English"
            # alone got a Turkish question Turkish replies 5 of 5; naming the
            # conflict got English 5 of 5.
            return (f"[REPLY LANGUAGE — they wrote in {LANGUAGE_NAMES.get(wrote, wrote)}, but have set "
                    f"the companion to reply in {name}: write the whole reply in {name}.]\n\n")
        return (f"[REPLY LANGUAGE — they have chosen {name}: reply in {name}, whatever language "
                "their message, the screen or the audio is in.]\n\n")
    language = guess_language(question)
    if language is None:
        return ""
    others = {getattr(line, "language", None) for line in lines}
    others.add(guess_language((screen_text or "")[:3000]))
    others.discard(None)
    if not others - {language}:
        return ""
    name = LANGUAGE_NAMES.get(language, language)
    return (f"[REPLY LANGUAGE — they wrote in {name}: reply in {name}, whatever language the "
            "screen or the audio is in.]\n\n")


#: Asking about the present moment: what is playing, what is on screen now.
_NOW_QUESTION = re.compile(
    r"\b(now|currently|right now|at the moment|playing|this song|this track|what song|which song|"
    r"şu an\w*|şimdi|çalan|çalıyor|bu şarkı\w*)\b",
    re.IGNORECASE,
)
#: Asking about music: the question that gets the NOW PLAYING block.
_MUSIC_QUESTION = re.compile(
    r"\b(songs?|tracks?|music|playing|album|artist|singer|band|listening|playlist|"
    r"şarkı\w*|müzi\w*|çalan|çalıyor|albüm\w*|sanatçı\w*|şarkıcı\w*|dinliyorum|dinlediğim)\b",
    re.IGNORECASE,
)


def _now_block(question: str) -> str:
    """For a question about now: fresh observations outrank what was said before.

    With the new song's title on screen and a remembered answer naming the old
    one, 4 of 5 answers named the new song without this and 5 of 5 with it.
    """
    if not _NOW_QUESTION.search(question or ""):
        return ""
    return ("[RIGHT NOW — what is on screen and heard just now outranks anything said earlier in "
            "this conversation; if it has changed since, say so.]\n\n")


def _note_block(note: str | None) -> str:
    """What the app saved for a note request, so the model confirms it truthfully.

    The model has no note tool, so without this it would either deny a note it
    can see was asked for, or claim one that was never written.
    """
    if note is None:
        return ""
    if not note:
        return (
            "[NOTE NOT SAVED — they asked for a note, but there was nothing to "
            "save yet. Ask what they would like noted.]\n\n"
        )
    return (
        "[NOTE SAVED — the app has just saved this to their notebook: "
        f'"{_quote(note, 300)}". Confirm it in a few words; don\'t repeat the '
        "whole note.]\n\n"
    )


#: Asking why the companion said something: a bare "why?", "why did you say
#: that", "what made you ask", "neden söyledin".
_ASKS_WHY = re.compile(
    r"^\W*(?:why|how come|neden|niye|niçin)\W*$"
    r"|\b(?:why|how come)\b.{0,40}\b(?:say|said|saying|mention\w*|ask\w*|bring\w*|"
    r"comment\w*|remark\w*|tell\w*|think)\b"
    r"|\bwhat made you\b"
    r"|\b(?:neden|niye|niçin)\b.{0,40}(?:söyledin|söylüyorsun|dedin|diyorsun|sordun|"
    r"soruyorsun|bahsettin|yorum yaptın)",
    re.IGNORECASE,
)


def _why_block(question: str, last: tuple[str, str] | None) -> str:
    """The reason behind the remark just made, when they ask why.

    Remarks are composed in a separate call, so the model answering now never
    saw its own reason. Handing it over only when asked keeps it out of every
    other turn -- the same rule that put the interruption note in the turn.
    """
    if last is None or not _ASKS_WHY.search(question or ""):
        return ""
    remark, reason = last
    return (
        "[WHY YOU SAID IT — your last message was a remark you made on your own: "
        f'"{_quote(remark)}". Your reason at the time: "{_quote(reason)}". If they '
        "are asking why you said it, explain that briefly in your own words.]\n\n"
    )


def _quote(sentence: str, limit: int = 200) -> str:
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def build_companion(
    config: AppConfig, image_path: str | None = None
) -> Companion:
    """Construct the module graph from config.

    Single place where config strings become implementations, so swapping a
    backend never touches a call site.
    """
    screen: ScreenSource
    if image_path:
        screen = StaticImageSource(image_path)
        log.info("replay mode: reading %s instead of the live screen", image_path)
    elif config.capture.mode in ("window", "monitor"):
        factory = WindowCapture if config.capture.mode == "window" else MSSCapture
        screen = factory(
            monitor_index=config.monitor_index,
            ignore_processes=config.capture.ignore_processes,
            min_window_on_monitor=config.capture.min_window_on_monitor,
            window_match=config.capture.window_match,
        )
    else:
        raise ValueError(
            f"unknown capture.mode {config.capture.mode!r} (use 'monitor' or 'window')"
        )

    perception = _build_perception(config, screen, replaying=bool(image_path))

    llm = OllamaLLM(
        model=config.ollama.model,
        host=config.ollama.host,
        keep_alive=config.ollama.keep_alive,
        num_ctx=config.ollama.num_ctx,
        temperature=config.llm.temperature,
        think=config.ollama.think,
        request_timeout_s=config.ollama.request_timeout_s,
        options=config.ollama.options,
        max_reply_tokens=config.ollama.max_reply_tokens,
        prompt_log=PromptLog(config),
    )

    privacy = PrivacyGuard(
        enabled=config.privacy.enabled,
        blocked_processes=config.privacy.blocked_processes,
        blocked_title_patterns=config.privacy.blocked_title_patterns,
    )

    memory = ConversationMemory(
        max_turns=config.memory.max_turns,
        max_chars=config.memory.max_chars,
        enabled=config.memory.enabled,
    )

    ambient = None
    if config.ambient.enabled and screen.is_live:
        ambient = AmbientObserver(
            screen=screen,
            differ=FrameDiffer(
                threshold=config.ambient.change_threshold,
                sample_long_edge=config.ambient.sample_long_edge,
            ),
            stable_delay_s=config.ambient.stable_delay_s,
            min_refresh_interval_s=config.ambient.min_refresh_interval_s,
        )

    tools = build_tools(config)

    # Optional: without it the companion is simply deaf to media, which is a
    # smaller loss than failing to start.
    audio = None
    try:
        audio = build_audio(config)
    except Exception as exc:
        log.warning("system audio unavailable: %s", exc)

    return Companion(
        config, screen, perception, llm, privacy, memory, ambient, tools, audio
    )


def build_embedder(config: AppConfig):
    """The model that matches liked moments by meaning, or None to match by words."""
    ratings = config.ratings
    if not (ratings.enabled and ratings.remember_moments and ratings.moments_embedding_model):
        return None
    from modules.llm.embeddings import OllamaEmbedder

    return OllamaEmbedder(model=ratings.moments_embedding_model, host=config.ollama.host,
                          keep_alive=config.ollama.keep_alive)


def build_audio(config: AppConfig):
    """Start system-audio capture and transcription, or return None.

    Raises if enabled but unusable, so a broken loopback is reported rather
    than leaving the companion quietly deaf.
    """
    if not config.audio.enabled:
        return None

    from modules.audio.loopback import SystemAudioCapture
    from modules.audio.music import BUFFER_SECONDS
    from modules.audio.transcriber import AudioTranscriber
    from modules.voice.stt.whisper_stt import FasterWhisperSTT

    capture = SystemAudioCapture(
        sample_rate=16000,
        buffer_seconds=config.audio.buffer_minutes * 60,
        # Kept whenever audio is on, so music questions work as soon as the
        # settings page switches them on. 3.8 MB.
        recent_seconds=BUFFER_SECONDS,
        device=config.audio.output_device,
        # The transcriber's own silence threshold, so "something is playing"
        # and "something worth transcribing" mean the same thing.
        sound_rms=config.audio.silence_rms,
    )
    capture.start()

    stt = FasterWhisperSTT(
        model=config.audio.model,
        device=config.audio.device,
        compute_type=config.audio.compute_type,
        language=config.audio.language,
        task=config.audio.task,
        # A 15 s chunk of a lecture is never "too short to bother with", and
        # the transcriber does its own silence gating before calling.
        min_seconds=0.5,
    )
    transcriber = AudioTranscriber(
        capture=capture,
        stt=stt,
        chunk_seconds=config.audio.chunk_seconds,
        silence_rms=config.audio.silence_rms,
        keep_minutes=config.audio.keep_minutes,
    )
    transcriber.start()
    log.info("listening to system audio (%s)", config.audio.model)
    return transcriber


def build_tools(config: AppConfig) -> ToolRegistry | None:
    """Assemble the tool registry, or None when tools are disabled."""
    if not config.tools.enabled:
        return None

    from modules.tools.clock import GetTime, Stopwatch
    from modules.tools.activity import SearchActivity
    from modules.tools.music import AnalyseMusic
    from modules.tools.notes import NoteBook, ReadNotes, SearchNotes
    from modules.tools.timers import CancelTimer, ListTimers, SetTimer, TimerStore

    store = TimerStore(config.root / config.tools.timers_file)
    notebook = NoteBook(config.root / config.tools.notes_file)

    registry = ToolRegistry(
        [
            GetTime(),
            Stopwatch(),
            SetTimer(store),
            ListTimers(store),
            CancelTimer(store),
            ReadNotes(notebook),
            SearchNotes(notebook),
            SearchActivity(config),
            AnalyseMusic(config),
        ]
    )
    # Kept on the registry so the worker's idle loop can poll for expiry
    # without reaching back through the tool objects.
    registry.timers = store  # type: ignore[attr-defined]
    # Notes are written by the app when a message asks for one, never by the
    # model, so Companion needs the notebook itself (modules/tools/requests.py).
    registry.notebook = notebook  # type: ignore[attr-defined]
    log.debug("tools available: %s", ", ".join(registry.names))
    return registry


def build_speaker(config: AppConfig):
    """Construct the voice pipeline, or return None when voice is off.

    Raises if voice is enabled but unusable -- a missing voice model should be
    reported, not silently swallowed into a companion that never speaks.
    """
    if not config.voice.enabled:
        return None

    from modules.voice.player import AudioPlayer
    from modules.voice.speaker import Speaker
    from modules.voice.tts.piper_tts import PiperTTS

    if config.voice.engine != "piper":
        raise ValueError(
            f"unknown voice.engine {config.voice.engine!r} (supported: 'piper')"
        )

    engine = PiperTTS(
        voice=config.voice.voice,
        voices_dir=config.root / config.voice.voices_dir,
        speed=config.voice.speed,
        volume=config.voice.volume,
        # Phoneme timings only when there is an avatar to use them.
        alignments=config.avatar.enabled and config.avatar.mouth_shapes,
    )
    if config.voice.warm_up:
        engine.warm_up()

    player = AudioPlayer(
        sample_rate=engine.sample_rate, device=config.voice.device
    )
    return Speaker(
        engine,
        player,
        min_sentence_chars=config.voice.min_sentence_chars,
        voices_by_language=config.voice.voices_by_language,
    )


def build_stt(config: AppConfig):
    """Construct speech recognition, or None when listening is off."""
    if not config.speech.enabled:
        return None

    from modules.voice.stt.whisper_stt import FasterWhisperSTT

    if config.speech.engine != "faster-whisper":
        raise ValueError(
            f"unknown speech.engine {config.speech.engine!r} "
            "(supported: 'faster-whisper')"
        )

    engine = FasterWhisperSTT(
        model=config.speech.model,
        device=config.speech.device,
        compute_type=config.speech.compute_type,
        language=config.speech.language,
        languages=config.speech.languages,
        beam_size=config.speech.beam_size,
        min_seconds=config.speech.min_seconds,
        silence_rms=config.speech.silence_rms,
        vad_filter=config.speech.vad_filter,
    )
    if config.speech.warm_up:
        engine.warm_up()
    return engine


def build_perception_sources(
    config: AppConfig, screen: ScreenSource, replaying: bool = False
) -> list[PerceptionSource]:
    """Instantiate the configured perception sources, in priority order.

    Exposed separately from `_build_perception` so `--probe` can time each
    source individually instead of only seeing whichever one won.
    """
    sources: list[PerceptionSource] = []
    for name in config.perception.sources:
        if name == "uia":
            if replaying:
                # UIA reads the live desktop, so including it during replay
                # would silently answer from the real screen instead of the
                # image under test -- destroying the point of replay mode.
                log.debug("replay mode: skipping the uia source")
                continue
            sources.append(
                UIAutomationSource(
                    screen=screen,
                    max_chars=config.uia.max_chars,
                    max_depth=config.uia.max_depth,
                    max_nodes=config.uia.max_nodes,
                    retry_delay_s=config.uia.retry_delay_s,
                    min_chars=config.perception.min_chars,
                    monitor_index=config.monitor_index,
                )
            )
        elif name == "ocr":
            if config.ocr.engine != "rapidocr":
                raise ValueError(
                    f"unknown ocr.engine {config.ocr.engine!r} (supported: 'rapidocr')"
                )
            sources.append(
                RapidOCRSource(
                    screen=screen,
                    max_long_edge=config.ocr.max_long_edge,
                    min_confidence=config.ocr.min_confidence,
                    monitor_index=config.monitor_index,
                )
            )
        else:
            raise ValueError(
                f"unknown perception source {name!r} (supported: 'uia', 'ocr')"
            )

    if not sources:
        raise ValueError(
            "no usable perception sources; set perception.sources in config.yaml"
        )
    return sources


def _build_perception(
    config: AppConfig, screen: ScreenSource, replaying: bool
) -> PerceptionSource:
    sources = build_perception_sources(config, screen, replaying)
    if len(sources) == 1:
        return sources[0]
    return FallbackPerception(sources, min_chars=config.perception.min_chars)
