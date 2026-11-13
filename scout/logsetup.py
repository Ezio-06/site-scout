"""Logging for the whole scout: one logger called "scout".

The CLI adds a console handler (INFO by default, -v = DEBUG, --quiet = warnings only, always on
stderr so stdout stays free for --output -). Every run also writes scout.log (DEBUG) into its
output folder. Each log line reads `time LEVEL message` - the message is always last, and a
multi-line message (a traceback) is folded onto one line so every line carries its level.
"""
from __future__ import annotations

import logging
import re
import sys
from contextlib import contextmanager
from pathlib import Path

LOGGER_NAME = "scout"
FORMAT = "%(asctime)s %(levelname)s %(message)s"


class OneLineFormatter(logging.Formatter):
    def format(self, record):
        return re.sub(r"\s*\n\s*", " | ", super().format(record))


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_console(level: int = logging.INFO, stream=None) -> logging.Handler:
    """Console output for the command line. Returns the handler so the caller can remove it."""
    log = get_logger()
    log.setLevel(logging.DEBUG)      # handlers decide what is shown; the file always gets DEBUG
    log.propagate = False
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(OneLineFormatter("%(levelname)-7s %(message)s"))
    log.addHandler(handler)
    return handler


@contextmanager
def run_log_file(out_dir: Path):
    """scout.log (DEBUG) in the run's output folder, for the duration of one run."""
    log = get_logger()
    old_level = log.level
    log.setLevel(logging.DEBUG)
    handler = logging.FileHandler(Path(out_dir) / "scout.log", mode="a", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(OneLineFormatter(FORMAT))
    log.addHandler(handler)
    try:
        yield handler
    finally:
        log.removeHandler(handler)
        handler.close()
        log.setLevel(old_level)
