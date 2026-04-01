"""Structured logging with correlation IDs for multi-agent tracing.

Provides a ContextVar-based correlation context and a logging.Filter
that injects structured fields into every log record.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar

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
        record.correlation_id = getattr(record, "correlation_id", None) or correlation_id.get()  # type: ignore[attr-defined]
        record.worktree = getattr(record, "worktree", None) or current_worktree.get()  # type: ignore[attr-defined]
        record.component = getattr(record, "component", None) or current_component.get()  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Formatter that emits one JSON object per line, compatible with jq."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Add structured fields if present
        for field in ("correlation_id", "worktree", "component"):
            value = getattr(record, field, "")
            if value:
                entry[field] = value
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


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
