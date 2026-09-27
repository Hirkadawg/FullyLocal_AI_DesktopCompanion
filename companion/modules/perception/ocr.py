"""OCR perception via RapidOCR (ONNX, CPU).

Runs on the CPU by design. The GPU budget on an 8 GB card is spoken for by the
language model, and RapidOCR is fast enough on CPU for on-demand use.

Detection output arrives in essentially arbitrary order, so the bulk of the work
here is reassembling it into reading order: cluster boxes into lines by vertical
position, then sort each line left to right. Without that step the model
receives a bag of words and summarises accordingly.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

import numpy as np
from PIL import Image

from core.errors import OCRError
from core.logging import get_logger, quieten, stage
from core.types import ScreenContext, TextRegion
from modules.capture.screen import ScreenSource
from modules.perception.base import PerceptionSource

log = get_logger(__name__)


class RapidOCRSource(PerceptionSource):
    """Screenshot -> OCR -> reading-ordered text."""

    name = "ocr"

    def __init__(
        self,
        screen: ScreenSource,
        max_long_edge: int = 1920,
        min_confidence: float = 0.5,
        monitor_index: int = 1,
    ) -> None:
        self.screen = screen
        self.max_long_edge = max_long_edge
        self.min_confidence = min_confidence
        self.monitor_index = monitor_index
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        """Load RapidOCR lazily; it downloads ~15 MB of models on first use."""
        if self._engine is None:
            try:
                from rapidocr import RapidOCR  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover
                raise OCRError(
                    "rapidocr is not installed. Run: pip install -r requirements.txt"
                ) from exc
            log.info("loading OCR models (first run also downloads ~15 MB)")
            import logging as _logging

            with quieten(_logging.INFO):
                self._engine = RapidOCR()
            # ERROR, not WARNING: RapidOCR warns on every blank screen, which is
            # a case `read()` already reports properly.
            for name in ("RapidOCR", "rapidocr"):
                _logging.getLogger(name).setLevel(_logging.ERROR)
        return self._engine

    def read(self) -> ScreenContext:
        timings: dict[str, float] = {}

        with stage("capture", timings, log):
            image = self.screen.grab()

        with stage("preprocess", timings, log):
            scaled, scale = self._downscale(image)
            array = np.asarray(scaled)

        with stage("ocr", timings, log):
            raw = self._get_engine()(array)

        with stage("layout", timings, log):
            regions = self._to_regions(raw, scale)
            text = self._reading_order(regions)

        hint = self.screen.window_hint()
        context = ScreenContext(
            text=text,
            regions=regions,
            image=image,
            source=self.name,
            window_title=hint.title if hint else None,
            app_name=hint.process if hint else None,
            monitor_index=self.monitor_index,
            captured_at=time.time(),
            timings_ms=timings,
        )
        log.debug("perception: %s", context.summary())
        return context

    def _downscale(self, image: Image.Image) -> tuple[Image.Image, float]:
        """Shrink so the long edge fits `max_long_edge`. Returns the scale used."""
        long_edge = max(image.width, image.height)
        if self.max_long_edge <= 0 or long_edge <= self.max_long_edge:
            return image, 1.0
        scale = self.max_long_edge / long_edge
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        return image.resize(size, Image.Resampling.LANCZOS), scale

    def _to_regions(self, raw: Any, scale: float) -> list[TextRegion]:
        """Normalise RapidOCR output and map boxes back to full-resolution coords."""
        inv = 1.0 / scale if scale else 1.0
        regions: list[TextRegion] = []
        for box, text, score in _iter_detections(raw):
            text = (text or "").strip()
            if not text or score < self.min_confidence:
                continue
            rect = _box_to_rect(box)
            if rect is None:
                continue
            regions.append(
                TextRegion(
                    text=text,
                    box=(
                        round(rect[0] * inv),
                        round(rect[1] * inv),
                        round(rect[2] * inv),
                        round(rect[3] * inv),
                    ),
                    confidence=float(score),
                )
            )
        return regions

    @staticmethod
    def _reading_order(regions: Sequence[TextRegion]) -> str:
        """Group regions into lines by vertical overlap, then sort left to right.

        The line tolerance is derived from the median detected text height, so it
        adapts to display scaling and font size instead of hard-coding pixels.
        """
        if not regions:
            return ""
        heights = sorted(r.height for r in regions if r.height > 0)
        median_height = heights[len(heights) // 2] if heights else 12.0
        tolerance = max(6.0, median_height * 0.6)

        lines: list[list[TextRegion]] = []
        line_centers: list[float] = []
        for region in sorted(regions, key=lambda r: (r.y_center, r.box[0])):
            for i, center in enumerate(line_centers):
                if abs(region.y_center - center) <= tolerance:
                    lines[i].append(region)
                    # Running average keeps a drifting line from splitting in two.
                    line_centers[i] = sum(r.y_center for r in lines[i]) / len(lines[i])
                    break
            else:
                lines.append([region])
                line_centers.append(region.y_center)

        ordered = sorted(
            zip(line_centers, lines), key=lambda pair: pair[0]
        )
        return "\n".join(
            "  ".join(r.text for r in sorted(line, key=lambda r: r.box[0]))
            for _, line in ordered
        ).strip()

    def close(self) -> None:
        self._engine = None


def _iter_detections(raw: Any):
    """Yield (box, text, score) across RapidOCR's several output shapes.

    v3.x returns an object exposing .boxes/.txts/.scores; v1.x returned a
    (results, elapsed) tuple of [box, text, score] triples. Both appear in the
    wild, and neither is worth pinning the whole project to.
    """
    if raw is None:
        return

    # v3.x: a RapidOCROutput. `txts` is None when nothing was recognised, and
    # a detection-only result carries the attribute but no text at all.
    if hasattr(raw, "txts"):
        txts = raw.txts
        if not txts:
            return
        boxes = getattr(raw, "boxes", None)
        scores = getattr(raw, "scores", None)
        if boxes is None:
            boxes = [None] * len(txts)
        if scores is None:
            scores = [1.0] * len(txts)
        yield from zip(boxes, txts, scores)
        return

    # Legacy tuple form: (results, elapsed).
    if isinstance(raw, tuple) and raw:
        raw = raw[0]
    if not raw:
        return
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            yield item[0], item[1], item[2]


def _box_to_rect(box: Any) -> tuple[float, float, float, float] | None:
    """Reduce a 4-point polygon (or an existing rect) to an axis-aligned box."""
    if box is None:
        return None
    points = np.asarray(box, dtype=float).reshape(-1, 2)
    if points.size == 0:
        return None
    xs, ys = points[:, 0], points[:, 1]
    return (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
