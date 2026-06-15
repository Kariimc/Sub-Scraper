"""Structured, low-noise logging for the download engine.

Everything in the engine logs through :func:`get_logger` instead of ``print``.
A single root logger (``subscraper``) fans out to two sinks:

* a ``StreamHandler`` (stderr) for developers / the terminal, and
* an optional GUI sink so the in-app "Download Log" shows the same structured
  records the engine emits.

Messages are written ``event.name key=value key=value`` so they stay greppable
and parseable without pulling in a heavyweight structured-logging dependency.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

LOGGER_NAME = "subscraper"

_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def kv(**fields: object) -> str:
    """Render keyword fields as a compact ``key=value`` suffix.

    ``log.info("download.start " + kv(track=t.id, source="spotify"))`` keeps log
    lines machine-parseable while reading naturally to a human.
    """
    parts = []
    for key, value in fields.items():
        text = str(value)
        if any(ch.isspace() for ch in text):
            text = f'"{text}"'
        parts.append(f"{key}={text}")
    return " ".join(parts)


class _CallbackHandler(logging.Handler):
    """Forwards formatted records to a thread-safe sink (e.g. the GUI queue)."""

    def __init__(self, sink: Callable[[str], None], level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink(self.format(record))
        except Exception:  # never let logging crash a worker
            pass


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a child of the shared ``subscraper`` logger."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def configure_logging(
    level: int = logging.INFO,
    *,
    gui_sink: Optional[Callable[[str], None]] = None,
    gui_level: int = logging.INFO,
) -> logging.Logger:
    """Idempotently configure the shared logger.

    Args:
        level: threshold for the stderr stream handler.
        gui_sink: optional callable that receives already-formatted log lines.
        gui_level: threshold for the GUI sink (kept higher to avoid debug spam).
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(min(level, gui_level))
    logger.propagate = False

    # Reset handlers so re-configuration (e.g. test runs) never stacks sinks.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT, _DATEFMT)

    stream = logging.StreamHandler()
    stream.setLevel(level)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if gui_sink is not None:
        gui = _CallbackHandler(gui_sink, level=gui_level)
        gui.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(gui)

    return logger
