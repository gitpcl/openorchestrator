"""Structured logging with correlation IDs for multi-agent tracing.

Provides a ContextVar-based correlation context and a logging.Filter
that injects structured fields into every log record.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

# Context variables for structured logging — works across sync and async
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
current_worktree: ContextVar[str] = ContextVar("current_worktree", default="")
current_component: ContextVar[str] = ContextVar("current_component", default="")


class StructuredLogFilter(logging.Filter):
    """Logging filter that injects correlation context into every record.

    Install on a handler or the root logger to add correlation_id,
    worktree, and component fields to all log records.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", None) or correlation_id.get()
        record.worktree = getattr(record, "worktree", None) or current_worktree.get()
        record.component = getattr(record, "component", None) or current_component.get()
        return True


_STANDARD_LOGRECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Formatter that emits one JSON object per line, compatible with jq.

    In addition to the standard ``timestamp/level/logger/message`` envelope
    and the correlation-context fields, every non-standard ``LogRecord``
    attribute (i.e. anything passed via ``logger.info(..., extra={...})``)
    is captured as a top-level JSON field. This is what gives callers like
    :func:`log_event` and the dream daemon heartbeat their structured shape.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Correlation context — always promote when populated
        for field in ("correlation_id", "worktree", "component"):
            value = getattr(record, field, "")
            if value:
                entry[field] = value
        # Anything else stashed on the record via extra= becomes a field.
        # Skip empty strings to preserve the correlation-context omission
        # behavior callers already rely on.
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key in entry:
                continue
            if key.startswith("_"):
                continue
            if value == "" or value is None:
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            entry[key] = value
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a structured event line through ``logger``.

    Equivalent to ``logger.log(level, event, extra={"event": event, **fields})``
    but enforces the convention that ``event`` is always present in the
    serialized JSON. Use for heartbeats and other machine-consumed signals
    where the message string itself is not enough.
    """
    logger.log(level, event, extra={"event": event, **fields})


def configure_logging(
    *,
    verbose: bool = False,
    json_format: bool = False,
) -> None:
    """Configure root logger with structured filter and optional JSON output.

    Args:
        verbose: If True, set level to DEBUG. Otherwise INFO.
        json_format: If True, use JSON formatter. Otherwise standard text.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Remove existing handlers to avoid duplicates
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.addFilter(StructuredLogFilter())

    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root.addHandler(handler)
