"""Speaks a token stream sentence by sentence.

Waiting for a complete answer before speaking would mean several seconds of
silence on anything long. Instead, tokens are accumulated only until a sentence
boundary, then that sentence is synthesised and queued while the model is still
writing the next one. First audio arrives about as soon as the first sentence
does, and playback stays ahead of generation from then on.

Synthesis runs on its own thread. It is fast (~15x real time) but not free, and
doing it inline would stall the tokens going to the screen.
"""

from __future__ import annotations

import queue
import re
import threading

from core.logging import get_logger
from core.types import Delivery
from modules.voice.language import guess_language
from modules.voice.player import AudioPlayer
from modules.voice.tts.base import TTSEngine

log = get_logger(__name__)

_STOP = object()

#: A sentence ends at .!? possibly followed by quotes/brackets, then whitespace.
_SENTENCE_END = re.compile(r'[.!?]["\'\)\]]*(?=\s)')

#: Abbreviations that end in a period without ending a sentence.
_ABBREVIATIONS = {
    "e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "prof.",
    "st.", "no.", "fig.", "approx.", "c.", "ca.", "al.",
}

#: Markdown that should never be read aloud as characters.
_MARKDOWN = re.compile(r"(\*\*|\*|__|_|`{1,3}|^#{1,6}\s|^\s*[-*+]\s)", re.MULTILINE)


def speakable(text: str) -> str:
    """Strip formatting that would otherwise be pronounced."""
    text = _MARKDOWN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(buffer: str, min_chars: int = 12) -> tuple[list[str], str]:
    """Split off complete sentences, returning (sentences, remainder).

    `min_chars` avoids emitting fragments so short that synthesising them costs
    more than it saves -- but it is small, because getting the *first* sentence
    out quickly is what makes the reply feel prompt.
    """
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(buffer):
        end = match.end()
        candidate = buffer[start:end].strip()
        if len(candidate) < min_chars:
            continue
        if candidate.split()[-1].lower() in _ABBREVIATIONS:
            continue  # "e.g." is not the end of a thought
        sentences.append(candidate)
        start = end
    return sentences, buffer[start:]


class Speaker:
    """Feeds streamed text to a TTS engine and out to the speakers."""

    def __init__(
        self,
        engine: TTSEngine,
        player: AudioPlayer,
        min_sentence_chars: int = 12,
        voices_by_language: dict[str, str] | None = None,
    ) -> None:
        self.engine = engine
        self.player = player
        self.min_sentence_chars = min_sentence_chars
        self.voices_by_language = voices_by_language or {}
        # The language being spoken: set by what a sentence is clearly written
        # in, or by speech recognition for a spoken question, and kept for
        # sentences that don't say (modules/voice/language.py).
        self._language: str | None = None
        # The language pinned with the lang button, or None: see pin_language().
        self._pinned: str | None = None

        self._buffer = ""
        self._queue: queue.Queue = queue.Queue()
        self._generation = 0  # bumped on stop(), so stale audio is discarded
        self._synthesizing = False
        # The utterance being spoken now, as written, so an interruption can
        # say how much of it was heard. See begin_utterance() and delivery().
        self._utterance = 0
        self._sentences: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="tts", daemon=True
        )
        self._thread.start()

    # -- called from the answering thread -------------------------------------

    def feed(self, text: str) -> None:
        """Accumulate streamed text, queueing each complete sentence."""
        self._buffer += text
        sentences, self._buffer = split_sentences(
            self._buffer, self.min_sentence_chars
        )
        for sentence in sentences:
            self._enqueue(sentence)

    def flush(self) -> None:
        """Speak whatever is left once the answer ends."""
        remainder, self._buffer = self._buffer.strip(), ""
        if remainder:
            self._enqueue(remainder)

    def begin_utterance(self) -> None:
        """Start tracking a new utterance: one answer, remark or announcement.

        Doesn't stop anything already playing. It only draws the line that
        `delivery()` measures from, so an interruption is judged against what
        is being said now rather than everything since the app started.
        """
        with self._lock:
            self._utterance += 1
            self._sentences = []
        if self._pinned:
            # Each answer starts in the chosen voice, whatever the last one ended in.
            self._language = self._pinned

    def delivery(self) -> Delivery | None:
        """How much of the current utterance has actually been heard.

        None if nothing has been queued for it. Read it BEFORE `stop()`, which
        ends the utterance.
        """
        finished_tag, playing_tag = self.player.progress
        with self._lock:
            utterance, sentences = self._utterance, tuple(self._sentences)
        unqueued = self._buffer
        if not sentences and not unqueued.strip():
            return None
        # Tags are (utterance, sentence index), and sentences play in order.
        if playing_tag is not None and playing_tag[0] == utterance:
            finished, partial = playing_tag[1], True
        elif finished_tag is not None and finished_tag[0] == utterance:
            finished, partial = finished_tag[1] + 1, False
        else:  # nothing of this utterance has started playing yet
            finished, partial = 0, False
        return Delivery(sentences, finished, partial, unqueued)

    def set_language(self, language: str | None) -> None:
        """Speak subsequent answers with the voice for `language`.

        Driven by the language the question was asked in, which is known for
        free from speech recognition -- more reliable than guessing the
        language of the answer text afterwards.
        """
        if not language or not self.voices_by_language:
            return
        if language.lower() in self.voices_by_language:
            self._language = language.lower()
        voice = self.voices_by_language.get(language.lower())
        if not voice:
            return
        setter = getattr(self.engine, "set_voice", None)
        if callable(setter) and setter(voice):
            log.debug("speaking %s with %s", language, voice)

    def pin_language(self, language: str | None) -> None:
        """Speak in the language chosen with the lang button, or None to follow
        the text alone.

        Every utterance starts in its voice, and a sentence that doesn't say
        which language it is keeps it. A sentence clearly in another language
        -- a quoted English title, a translation asked for -- still gets that
        language's voice, rather than being read in the wrong accent. A language
        with no voice installed leaves the voice to the text.
        """
        language = language.lower() if language else None
        self._pinned = language if language in self.voices_by_language else None
        # Back on auto, nothing carries over from the pin: the text decides.
        self._language = self._pinned

    def stop(self) -> None:
        """Barge-in: drop pending text and audio, and silence playback now."""
        with self._lock:
            self._generation += 1
            # A stop ends the utterance. Anyone who needs to know how much of
            # it was heard reads delivery() first.
            self._utterance += 1
            self._sentences = []
        self._buffer = ""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self.player.stop()

    def close(self) -> None:
        self.stop()
        self._queue.put(_STOP)
        self._thread.join(timeout=3)
        self.player.close()
        self.engine.close()

    @property
    def is_speaking(self) -> bool:
        """True if anything is queued, being synthesised, or playing.

        The `_synthesizing` flag matters more than it looks: between a sentence
        being taken off the queue and its audio reaching the player there is a
        gap of roughly 200 ms where the queue is empty and nothing is playing
        yet. Without it, "is it still talking" reads false mid-answer, and
        anything relying on that -- like deciding whether Esc should interrupt
        or hide -- makes the wrong call.
        """
        with self._lock:
            synthesising = self._synthesizing
        return synthesising or self.player.is_playing or not self._queue.empty()

    # -- synthesis thread -----------------------------------------------------

    def _voice_for(self, sentence: str) -> str | None:
        """The voice for this sentence: its own language when clear, else the
        language already being spoken. None leaves the engine's voice alone."""
        if not self.voices_by_language:
            return None
        language = guess_language(sentence)
        if language in self.voices_by_language:
            self._language = language
        return self.voices_by_language.get(self._language) if self._language else None

    def _enqueue(self, sentence: str) -> None:
        spoken = speakable(sentence)
        if spoken:
            voice = self._voice_for(spoken)
            with self._lock:
                generation = self._generation
                # Kept as written, not as pronounced, so it can be quoted back.
                tag = (self._utterance, len(self._sentences))
                self._sentences.append(sentence.strip())
            self._queue.put((generation, tag, spoken, voice))

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            generation, tag, sentence, voice = item
            setter = getattr(self.engine, "set_voice", None)
            if voice and callable(setter):
                # On this thread, just before synthesis: switching from another
                # thread could change voice halfway through a sentence.
                setter(voice)
            with self._lock:
                if generation != self._generation:
                    continue  # interrupted while this was waiting; drop it
                # Claimed before releasing the lock, so there is no instant
                # where this sentence is invisible to `is_speaking`.
                self._synthesizing = True
            try:
                marked = getattr(self.engine, "synthesize_marked", None)
                if callable(marked):
                    samples, marks = marked(sentence)
                else:
                    samples, marks = self.engine.synthesize(sentence), None
                with self._lock:
                    stale = generation != self._generation
                if not stale:  # interrupted while this was being synthesised
                    self.player.sample_rate = self.engine.sample_rate
                    if marks:  # mouth shapes for the avatar, when the voice gave them
                        self.player.enqueue(samples, tag=tag, marks=marks)
                    else:
                        self.player.enqueue(samples, tag=tag)
            except Exception:
                log.warning("synthesis failed for %r", sentence[:40], exc_info=True)
            finally:
                with self._lock:
                    self._synthesizing = False
