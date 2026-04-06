"""Tests for the structured output formatter."""

from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from open_orchestrator.utils.output import OutputFormatter


def _make_formatter(*, json_mode: bool = False) -> tuple[OutputFormatter, StringIO]:
    """Create a formatter with a captured output buffer."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False)
    fmt = OutputFormatter(json_mode=json_mode, console=console)
    return fmt, buf


class TestOutputFormatterRichMode:
    def test_print_outputs_message(self) -> None:
        fmt, buf = _make_formatter()
        fmt.print("[green]Hello[/green]")
        assert "Hello" in buf.getvalue()

    def test_success_outputs_message(self) -> None:
        fmt, buf = _make_formatter()
        fmt.success(message="All good")
        assert "All good" in buf.getvalue()

    def test_error_outputs_message(self) -> None:
        fmt, buf = _make_formatter()
        fmt.error("Failed", errors=["detail 1", "detail 2"])
        output = buf.getvalue()
        assert "Failed" in output
        assert "detail 1" in output

    def test_is_json_false(self) -> None:
        fmt, _ = _make_formatter()
        assert fmt.is_json is False


class TestOutputFormatterJsonMode:
    def test_print_suppressed(self) -> None:
        fmt, buf = _make_formatter(json_mode=True)
        fmt.print("Should not appear")
        assert buf.getvalue() == ""

    def test_success_json_envelope(self) -> None:
        fmt, buf = _make_formatter(json_mode=True)
        fmt.success(data={"count": 5}, message="Done")
        output = buf.getvalue()
        parsed = json.loads(output)
        assert parsed["status"] == "ok"
        assert parsed["data"]["count"] == 5
        assert parsed["message"] == "Done"

    def test_success_json_without_message(self) -> None:
        fmt, buf = _make_formatter(json_mode=True)
        fmt.success(data=[1, 2, 3])
        parsed = json.loads(buf.getvalue())
        assert parsed["status"] == "ok"
        assert parsed["data"] == [1, 2, 3]
        assert "message" not in parsed

    def test_error_json_envelope(self) -> None:
        fmt, buf = _make_formatter(json_mode=True)
        fmt.error("Something broke", errors=["err1", "err2"])
        parsed = json.loads(buf.getvalue())
        assert parsed["status"] == "error"
        assert parsed["message"] == "Something broke"
        assert parsed["errors"] == ["err1", "err2"]

    def test_error_json_no_errors(self) -> None:
        fmt, buf = _make_formatter(json_mode=True)
        fmt.error("Fail")
        parsed = json.loads(buf.getvalue())
        assert parsed["status"] == "error"
        assert "errors" not in parsed

    def test_data_json(self) -> None:
        fmt, buf = _make_formatter(json_mode=True)
        fmt.data({"key": "value"})
        parsed = json.loads(buf.getvalue())
        assert parsed["key"] == "value"

    def test_is_json_true(self) -> None:
        fmt, _ = _make_formatter(json_mode=True)
        assert fmt.is_json is True


class TestCLIJsonFlag:
    def test_json_flag_in_context(self) -> None:
        from click.testing import CliRunner

        from open_orchestrator.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--json", "--help"])
        assert result.exit_code == 0
