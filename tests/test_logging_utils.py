"""Tests for structured logging utilities."""

from __future__ import annotations

import json
import logging

from open_orchestrator.utils.logging import (
    JsonFormatter,
    StructuredLogFilter,
    configure_logging,
    correlation_id,
    current_component,
    current_worktree,
)


class TestStructuredLogFilter:
    """Test StructuredLogFilter context injection."""

    def test_injects_correlation_id(self) -> None:
        token = correlation_id.set("req-123")
        try:
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            f = StructuredLogFilter()
            f.filter(record)
            assert record.correlation_id == "req-123"  # type: ignore[attr-defined]
        finally:
            correlation_id.reset(token)

    def test_injects_worktree(self) -> None:
        token = current_worktree.set("auth-jwt")
        try:
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            f = StructuredLogFilter()
            f.filter(record)
            assert record.worktree == "auth-jwt"  # type: ignore[attr-defined]
        finally:
            current_worktree.reset(token)

    def test_injects_component(self) -> None:
        token = current_component.set("batch")
        try:
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            f = StructuredLogFilter()
            f.filter(record)
            assert record.component == "batch"  # type: ignore[attr-defined]
        finally:
            current_component.reset(token)

    def test_defaults_to_empty(self) -> None:
        """Without context vars set, fields default to empty string."""
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        f = StructuredLogFilter()
        f.filter(record)
        assert record.correlation_id == ""  # type: ignore[attr-defined]

    def test_record_level_overrides_context(self) -> None:
        """Extra passed via record should take precedence over context var."""
        token = correlation_id.set("context-id")
        try:
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            record.correlation_id = "record-id"  # type: ignore[attr-defined]
            f = StructuredLogFilter()
            f.filter(record)
            assert record.correlation_id == "record-id"  # type: ignore[attr-defined]
        finally:
            correlation_id.reset(token)

    def test_always_returns_true(self) -> None:
        """Filter should never suppress records."""
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        f = StructuredLogFilter()
        assert f.filter(record) is True


class TestJsonFormatter:
    """Test JSON log output formatting."""

    def test_produces_valid_json(self) -> None:
        record = logging.LogRecord("test.module", logging.WARNING, "", 0, "something broke", (), None)
        fmt = JsonFormatter()
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "WARNING"
        assert parsed["message"] == "something broke"
        assert parsed["logger"] == "test.module"
        assert "timestamp" in parsed

    def test_includes_structured_fields(self) -> None:
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        record.correlation_id = "abc-123"  # type: ignore[attr-defined]
        record.worktree = "feat-auth"  # type: ignore[attr-defined]
        record.component = ""  # empty should be omitted
        fmt = JsonFormatter()
        parsed = json.loads(fmt.format(record))
        assert parsed["correlation_id"] == "abc-123"
        assert parsed["worktree"] == "feat-auth"
        assert "component" not in parsed

    def test_includes_exception_info(self) -> None:
        import sys

        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord("test", logging.ERROR, "", 0, "failed", (), exc_info)
        fmt = JsonFormatter()
        parsed = json.loads(fmt.format(record))
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestConfigureLogging:
    """Test configure_logging setup."""

    def test_verbose_sets_debug(self) -> None:
        configure_logging(verbose=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        # Cleanup
        configure_logging(verbose=False)

    def test_default_sets_info(self) -> None:
        configure_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_json_format_uses_json_formatter(self) -> None:
        configure_logging(json_format=True)
        root = logging.getLogger()
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)
        # Cleanup
        configure_logging(json_format=False)

    def test_filter_is_installed(self) -> None:
        configure_logging()
        root = logging.getLogger()
        handler = root.handlers[0]
        filter_types = [type(f) for f in handler.filters]
        assert StructuredLogFilter in filter_types
