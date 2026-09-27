"""Mouth shapes from speech sounds, for the avatar.

Piper can say how many audio samples each phoneme took (its alignments, an
experimental feature that patches the voice model in memory and needs the `onnx`
package). Measured on this machine: the samples add up exactly to the audio
(82,432 of 82,432 for a long sentence; English and Turkish alike), a voice
loads ~0.1-0.3 s slower once, and synthesis costs about the same. The speech
isn't changed by it -- though Piper varies each rendering a little anyway (one
sentence three times: 46336, 44800, 42752 samples).

Most Live2D models have two mouth parameters, not one per vowel: how open
(ParamMouthOpenY) and its form (ParamMouthForm, -1 rounded to 1 wide). So each
phoneme is sorted into a few shapes, and each shape is a pair of those values.
"""

from __future__ import annotations

import unicodedata

REST = "rest"

#: Shape -> (how open, 0-1; form, -1 rounded .. 1 wide).
SHAPES: dict[str, tuple[float, float]] = {
    "a": (1.0, 0.0),
    "i": (0.35, 1.0),
    "u": (0.3, -1.0),
    "e": (0.6, 0.5),
    "o": (0.75, -0.6),
    "closed": (0.0, 0.0),     # m, b, p: lips together
    "consonant": (0.25, 0.0),
    REST: (0.0, 0.0),
}

# IPA as espeak-ng writes it for Piper, English and Turkish included (ı = ɯ,
# ö = ø, ü = y).
_VOWELS = {
    "a": "aɑæɐʌɶ",
    "i": "iɪɨɯj",
    "u": "uʊwyʏɥ",
    "e": "eɛəɜɚɝɘ",
    "o": "oɔɒøœɵ",
    "closed": "mbp",
}
#: Stress marks come before their syllable: they take the next sound's shape.
_BEFORE = "ˈˌ"
#: Length and similar marks come after: they take the previous sound's shape.
_AFTER = "ːˑʲʰʷˠˤ"

_NEXT, _PREVIOUS = "<next>", "<previous>"


def shape_of(phoneme: str) -> str:
    """The shape of one Piper phoneme (possibly a marker that borrows one)."""
    if not phoneme or phoneme.isspace():
        return REST
    ch = phoneme[0]
    if ch in _BEFORE:
        return _NEXT
    if ch in _AFTER or unicodedata.combining(ch):
        return _PREVIOUS
    for shape, chars in _VOWELS.items():
        if ch in chars:
            return shape
    # ^ and $ (start and end), _ (padding), punctuation: the mouth rests.
    return "consonant" if ch.isalpha() else REST


def mouth_shapes(alignments: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """(shape, samples) for (phoneme, samples), markers resolved, repeats merged.

    The samples are kept exactly, so the shapes stay in step with the audio.
    """
    labels = [shape_of(phoneme) for phoneme, _ in alignments]

    def real(label: str) -> bool:
        return label not in (_NEXT, _PREVIOUS)

    for _ in range(2):  # a length mark after a stress mark needs a second pass
        for i, label in enumerate(labels):
            if label == _PREVIOUS and i and real(labels[i - 1]):
                labels[i] = labels[i - 1]
        for i in range(len(labels) - 1, -1, -1):
            if labels[i] == _NEXT and i + 1 < len(labels) and real(labels[i + 1]):
                labels[i] = labels[i + 1]
    merged: list[tuple[str, int]] = []
    for label, (_, samples) in zip(labels, alignments):
        label = label if real(label) else REST
        if merged and merged[-1][0] == label:
            merged[-1] = (label, merged[-1][1] + int(samples))
        else:
            merged.append((label, int(samples)))
    return merged
