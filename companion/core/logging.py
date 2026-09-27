"""Logging and per-stage timing.

Timings are here on purpose: making continuous perception cheap needs a
baseline to measure against.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator, MutableMapping

_CONFIGURED = False


def setup_logging(level: str = "INFO", log_file: "Path | None" = None) -> None:
    """Configure logging, optionally also to a file.

    The GUI runs under pythonw.exe, which has no console, so anything written
    to stderr there is lost. A file handler is the only way a problem hit in
    the window can be diagnosed afterwards.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            # Rotating, so a long-running session can't fill the disk.
            handlers.append(
                RotatingFileHandler(
                    log_file, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
                )
            )
        except OSError:
            pass  # a missing log file must never stop the app starting

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    # Third-party chatter: httpx logs every Ollama call at INFO, and onnxruntime
    # narrates its provider selection. Neither is useful here.
    for noisy in ("httpx", "httpcore", "RapidOCR", "rapidocr", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("onnxruntime").setLevel(logging.ERROR)
    _CONFIGURED = True


@contextmanager
def quieten(level: int = logging.INFO) -> Iterator[None]:
    """Suppress log records at or below `level` for the duration of the block.

    RapidOCR sets its own logger to INFO while building its pipeline, which
    undoes anything configured beforehand, so the only reliable way to keep
    startup quiet is to gag logging around construction.
    """
    previous = logging.root.manager.disable
    logging.disable(level)
    try:
        yield
    finally:
        logging.disable(previous)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def stage(
    name: str,
    sink: MutableMapping[str, float] | None = None,
    logger: logging.Logger | None = None,
) -> Iterator[None]:
    """Time a pipeline stage, recording the result into `sink`.

    >>> timings: dict[str, float] = {}
    >>> with stage("capture", timings):
    ...     ...
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if sink is not None:
            sink[name] = round(elapsed_ms, 1)
        if logger is not None:
            logger.debug("%s took %.1f ms", name, elapsed_ms)
