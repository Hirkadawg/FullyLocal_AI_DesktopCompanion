"""Speech recognition with faster-whisper, on the CPU.

CPU by default, and measured rather than assumed. On this machine:

    tiny.en           244 ms per utterance
    base.en           453 ms
    distil-small.en  1460 ms
    small.en         1442 ms

Note those are *per utterance*, not per second of audio -- Whisper pads its
input to a 30-second window, so a two-word question costs the same as a long
one. Model size is therefore the only real lever on latency.

`base.en` is the default: fast enough that releasing the talk key feels
responsive, and accurate enough for questions and short commands.

The GPU would be faster, but faster-whisper's CUDA path needs cuBLAS and cuDNN
DLLs that aren't part of the install (it fails with "cublas64_12.dll is not
found"), pulling in roughly a gigabyte of NVIDIA runtime packages, and it would
take ~0.5 GB of VRAM from a card that is already at 7.2 of 8 GB. Not worth it
for 450 ms.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from core.errors import CompanionError
from core.logging import get_logger
from modules.voice.stt.base import STTEngine

log = get_logger(__name__)


class STTUnavailable(CompanionError):
    """The speech model couldn't be loaded."""


#: Whisper does not return nothing when given nothing -- it returns whatever it
#: was most often trained to hear over quiet audio. These are the usual ones.
#: Only applied to very short transcripts, so a genuine "Yes." survives.
_HALLUCINATIONS = {
    "you", "thank you", "thanks for watching", "thank you for watching",
    "bye", "bye bye", "thanks", "okay", "so", "uh", "um", "please subscribe",
    "subtitles by the amaraorg community", "the",
}


class FasterWhisperSTT(STTEngine):
    """Transcribes short utterances with a local Whisper model."""

    name = "faster-whisper"
    sample_rate = 16000

    def __init__(
        self,
        model: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = "en",
        beam_size: int = 1,
        min_seconds: float = 0.3,
        silence_rms: float = 0.004,
        vad_filter: bool = True,
        task: str = "transcribe",
        languages: list[str] | None = None,
    ) -> None:
        # Detection is restricted to these when no language is forced. Free
        # auto-detect over 99 languages is unreliable on short utterances from
        # an ordinary microphone -- English gets heard as Polish or Turkish.
        # Choosing between two candidates is a far easier problem.
        self.languages = [lang.lower() for lang in (languages or []) if lang]
        self.silence_rms = silence_rms
        self.vad_filter = vad_filter
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        # None means auto-detect, which is what lets foreign-language video work
        # without being told in advance what language it is.
        self.language = language or None
        self.beam_size = beam_size
        self.min_seconds = min_seconds
        self.task = task
        self._model: Any | None = None
        #: Language of the most recent transcription, when auto-detected.
        self.last_language: str | None = None
        #: How sure Whisper was of that language, 0-1.
        self.last_language_probability: float | None = None

        if task == "translate" and model.endswith(".en"):
            # Refused loudly rather than logged: an English-only model given
            # Spanish does not translate it, it returns approximate nonsense,
            # which looks like a bad translation instead of a misconfiguration.
            raise STTUnavailable(
                f"Model '{model}' is English-only and cannot translate. Use a "
                f"multilingual model -- drop the '.en' (e.g. '{model[:-3]}')."
            )
        if task not in ("transcribe", "translate"):
            raise STTUnavailable(
                f"unknown task {task!r} (use 'transcribe' or 'translate')"
            )

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover
            raise STTUnavailable(
                "faster-whisper is not installed. Run: "
                "pip install -r requirements.txt"
            ) from exc

        log.info("loading speech model %s (%s/%s)",
                 self.model_name, self.device, self.compute_type)
        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            hint = ""
            if self.device == "cuda":
                hint = (
                    "\n  The CUDA path needs cuBLAS/cuDNN libraries that are not "
                    "installed. Set speech.device to 'cpu' in config.yaml."
                )
            raise STTUnavailable(
                f"Could not load speech model '{self.model_name}': {exc}{hint}"
            ) from exc
        return self._model

    def warm_up(self) -> None:
        """Load the model and run one pass, so the first utterance isn't slow."""
        model = self._load()
        try:
            silence = np.zeros(self.sample_rate, dtype=np.float32)
            list(model.transcribe(silence, language=self.language, beam_size=1)[0])
        except Exception:  # pragma: no cover -- warm-up is never fatal
            log.debug("speech warm-up failed", exc_info=True)

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) == 0:
            return ""
        seconds = len(audio) / self.sample_rate
        if seconds < self.min_seconds:
            log.debug("ignoring %.2fs of audio (below min_seconds)", seconds)
            return ""

        audio = np.asarray(audio, dtype=np.float32).reshape(-1)

        # Gate on loudness before involving the model at all. Given silence,
        # Whisper does not return an empty string -- it returns "You", or
        # "Thank you", or whatever it heard most often over quiet audio in
        # training. Cheaper and more reliable to never ask.
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < self.silence_rms:
            log.debug("ignoring %.1fs at RMS %.5f (below silence_rms)", seconds, rms)
            return ""

        model = self._load()
        segments, info = model.transcribe(
            audio,
            # None means auto-detect over everything, which is what makes
            # foreign-language video work. When `languages` is set, detection
            # is narrowed to that shortlist instead.
            language=self.language or self._choose_language(audio, model),
            task=self.task,
            beam_size=self.beam_size,
            # Each push-to-talk utterance stands alone; carrying context between
            # them makes Whisper invent continuations of the previous one.
            condition_on_previous_text=False,
            # Trims non-speech before decoding, which removes most of the
            # remaining opportunity to hallucinate over background noise.
            vad_filter=self.vad_filter,
        )
        text = " ".join(segment.text for segment in segments).strip()

        if _is_hallucination(text):
            log.debug("discarding likely hallucination %r (RMS %.5f)", text, rms)
            return ""

        detected = getattr(info, "language", None)
        self.last_language = detected
        self.last_language_probability = getattr(info, "language_probability", None)
        log.debug(
            "%s %.1fs [%s] -> %r", self.task, seconds, detected or "?", text[:60]
        )
        return text

    def set_language(self, language: str | None) -> None:
        """Force a language, or pass None to go back to the shortlist."""
        self.language = language.lower() if language else None
        log.info("speech language: %s", self.language or f"auto {self.languages}")

    def _choose_language(self, audio, model) -> str | None:
        """Pick the most likely of the allowed languages, or None for free choice.

        Whisper's own detection ranks all 99; asking it to rank only the two
        the user actually speaks removes almost all the confusion, because the
        wrong answers were never plausible candidates to begin with.
        """
        if not self.languages:
            return None
        if len(self.languages) == 1:
            return self.languages[0]
        try:
            _lang, _prob, all_probs = model.detect_language(audio)
        except Exception:
            log.debug("language detection unavailable", exc_info=True)
            return self.languages[0]

        scores = dict(all_probs or [])
        best = max(self.languages, key=lambda code: scores.get(code, 0.0))
        log.debug(
            "language shortlist %s -> %s (%s)",
            self.languages, best,
            ", ".join(f"{c}={scores.get(c, 0.0):.2f}" for c in self.languages),
        )
        return best

    def close(self) -> None:
        self._model = None


def _is_hallucination(text: str) -> bool:
    """Whether a transcript is one of Whisper's stock responses to near-silence.

    Only short transcripts are checked, so a real one-word answer is only
    discarded when it is exactly one of the known filler phrases -- and those
    are not plausible questions to ask a screen-reading companion anyway.
    """
    stripped = "".join(c for c in text.lower() if c.isalnum() or c.isspace()).strip()
    if not stripped:
        return True
    if len(stripped) > 30:
        return False
    return stripped in _HALLUCINATIONS
