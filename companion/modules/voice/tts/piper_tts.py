"""Piper text-to-speech, on the CPU.

CPU is a deliberate choice, not a limitation: it synthesises at roughly 15x real
time on this machine, so the GPU stays entirely free for the language model. On
an 8 GB card that is the difference between voice being free and voice competing
with the thing doing the thinking.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import numpy as np

from core.errors import CompanionError
from core.logging import get_logger
from modules.voice.tts.base import TTSEngine
from modules.voice.visemes import mouth_shapes

log = get_logger(__name__)


class VoiceNotFound(CompanionError):
    """The voice model isn't on disk. Message says how to fetch it."""


class PiperTTS(TTSEngine):
    """Speech from a Piper ONNX voice."""

    name = "piper"

    def __init__(
        self,
        voice: str = "en_US-lessac-medium",
        voices_dir: str | Path = "data/voices",
        speed: float = 1.0,
        volume: float = 1.0,
        # Phoneme timings for the avatar's mouth shapes. Patches the voice model
        # in memory (needs the onnx package); without onnx, speech is unchanged
        # and the mouth follows loudness instead.
        alignments: bool = False,
    ) -> None:
        self.alignments = alignments
        self.voice_name = voice
        self.default_voice = voice
        self.voices_dir = Path(voices_dir)
        self.speed = max(0.25, min(4.0, speed))
        self.volume = max(0.0, min(1.0, volume))
        # Cached per name, so switching languages mid-session doesn't reload a
        # model that was already paid for. ~60 MB of RAM each.
        self._voices: dict[str, Any] = {}
        self._voice: Any | None = None
        self._sample_rate = 22050

    def set_voice(self, voice: str | None) -> bool:
        """Switch voice, loading it if needed. False if it isn't installed.

        An English voice reading Turkish produces something between comic and
        unintelligible, so answering in the user's language means speaking it
        with a voice that knows the phonetics.
        """
        target = voice or self.default_voice
        if target == self.voice_name and self._voice is not None:
            return True
        if not (self.voices_dir / f"{target}.onnx").is_file():
            log.debug("voice %s not installed, keeping %s", target, self.voice_name)
            return False
        self.voice_name = target
        self._voice = self._voices.get(target)
        if self._voice is not None:
            self._sample_rate = int(self._voice.config.sample_rate)
        return True

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def model_path(self) -> Path:
        return self.voices_dir / f"{self.voice_name}.onnx"

    def _load(self) -> Any:
        if self._voice is not None:
            return self._voice
        cached = self._voices.get(self.voice_name)
        if cached is not None:
            self._voice = cached
            self._sample_rate = int(cached.config.sample_rate)
            return cached

        if not self.model_path.is_file():
            raise VoiceNotFound(
                f"Voice '{self.voice_name}' not found at {self.model_path}.\n"
                f"  Fetch it with:  .venv\\Scripts\\python.exe -m piper.download_voices "
                f"{self.voice_name} --data-dir {self.voices_dir}"
            )

        from piper import PiperVoice

        log.info("loading voice %s", self.voice_name)
        # use_cuda stays False: see the module docstring.
        self._voice = PiperVoice.load(self.model_path, use_cuda=False,
                                      include_alignments=self.alignments)
        self._voices[self.voice_name] = self._voice
        self._sample_rate = int(self._voice.config.sample_rate)
        return self._voice

    def _synthesis_config(self) -> Any:
        """Build a SynthesisConfig using only fields this Piper version has.

        Piper's options have moved around between releases; introspecting keeps
        a rename from turning into a crash on a machine we can't test on.
        """
        try:
            from piper.config import SynthesisConfig
        except ImportError:
            return None

        wanted = {
            "length_scale": 1.0 / self.speed,  # higher = slower speech
            "volume": self.volume,
        }
        available = {f.name for f in dataclasses.fields(SynthesisConfig)}
        kwargs = {k: v for k, v in wanted.items() if k in available}
        missing = set(wanted) - available
        if missing:
            log.debug("piper SynthesisConfig lacks %s", ", ".join(sorted(missing)))
        return SynthesisConfig(**kwargs)

    def warm_up(self) -> None:
        """Load the model and run one tiny synthesis.

        The first call carries model load plus ONNX graph warm-up -- about three
        seconds. Paying it at startup keeps it out of the first answer.
        """
        self._load()
        try:
            self.synthesize("Ready.")
        except Exception:  # pragma: no cover -- warm-up must never be fatal
            log.debug("warm-up synthesis failed", exc_info=True)

    def synthesize(self, text: str) -> np.ndarray:
        return self.synthesize_marked(text)[0]

    def synthesize_marked(self, text: str) -> tuple[np.ndarray, list[tuple[str, int]] | None]:
        """Samples, and mouth shapes over them when alignments are on and worked."""
        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.int16), None

        voice = self._load()
        config = self._synthesis_config()
        parts: list[np.ndarray] = []
        marks: list[tuple[str, int]] = []
        complete = self.alignments
        for chunk in voice.synthesize(text, syn_config=config, include_alignments=self.alignments):
            audio = np.asarray(chunk.audio_int16_array, dtype=np.int16)
            parts.append(audio)
            self._sample_rate = int(chunk.sample_rate)
            aligned = getattr(chunk, "phoneme_alignments", None) if self.alignments else None
            if aligned is None:
                complete = False
            else:
                marks.extend(mouth_shapes([(a.phoneme, int(a.num_samples)) for a in aligned]))

        if not parts:
            return np.zeros(0, dtype=np.int16), None
        samples = np.concatenate(parts) if len(parts) > 1 else parts[0]
        return samples, (marks if complete and marks else None)

    def close(self) -> None:
        self._voice = None
