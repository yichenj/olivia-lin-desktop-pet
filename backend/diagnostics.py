"""Bounded JSONL diagnostics containing operational metadata, never model content."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re

LOGGER = logging.getLogger('olivia')
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False
CONTEXT = ContextVar('diagnostic_context', default={})


def configure_logging(database):
    """Each database owns its logs, including temporary development databases."""
    directory = Path(str(database) + '.logs')
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / 'backend.log', maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    close_logging()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    return directory / 'backend.log'


def close_logging():
    for handler in list(LOGGER.handlers):
        if not isinstance(handler, logging.NullHandler):
            LOGGER.removeHandler(handler)
            handler.close()


@contextmanager
def log_context(**fields):
    token = CONTEXT.set({**CONTEXT.get(), **fields})
    try:
        yield
    finally:
        CONTEXT.reset(token)


def event(name, **fields):
    record = {'time': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
              'pid': os.getpid(), **CONTEXT.get(), 'event': name, **fields}
    LOGGER.info(json.dumps(record, ensure_ascii=False))


def error_code(error):
    # Exceptions and validation messages can contain provider text or user paths.
    code = getattr(error, 'code', None)
    if code is None and isinstance(error, ValueError):
        code = str(error).split(':', 1)[0]
    return code if isinstance(code, str) and re.fullmatch(r'[a-z_0-9]+', code) else type(error).__name__
