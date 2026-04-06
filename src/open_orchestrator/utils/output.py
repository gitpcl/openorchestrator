"""Structured output formatting: Rich console and JSON modes.

Provides OutputFormatter that switches between Rich (human) and JSON
(machine) output based on a global flag. JSON envelope format:
  {"status": "ok"|"error", "data": ..., "errors": [...]}
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console


class OutputFormatter:
    """Wraps Rich console with optional JSON output mode.

    Usage:
        fmt = OutputFormatter(json_mode=ctx.obj.get("json", False))
        fmt.success(data={"worktrees": [...]})
        fmt.error("Something failed", errors=["detail"])
        fmt.print("Human-readable output")  # Suppressed in JSON mode
    """

    def __init__(self, *, json_mode: bool = False, console: Console | None = None) -> None:
        self._json_mode = json_mode
        self._console = console or Console()
        self._json_buffer: dict[str, Any] | None = None

    @property
    def is_json(self) -> bool:
        return self._json_mode

    def print(self, message: str) -> None:
        """Print a message (suppressed in JSON mode)."""
        if not self._json_mode:
            self._console.print(message)

    def success(self, data: Any = None, message: str = "") -> None:
        """Output success result."""
        if self._json_mode:
            envelope = {"status": "ok", "data": data}
            if message:
                envelope["message"] = message
            self._console.print(json.dumps(envelope, indent=2, default=str))
        elif message:
            self._console.print(f"[green]{message}[/green]")

    def error(self, message: str, errors: list[str] | None = None) -> None:
        """Output error result."""
        if self._json_mode:
            envelope: dict[str, Any] = {"status": "error", "message": message}
            if errors:
                envelope["errors"] = errors
            self._console.print(json.dumps(envelope, indent=2, default=str))
        else:
            self._console.print(f"[red]{message}[/red]")
            if errors:
                for err in errors:
                    self._console.print(f"  [dim]{err}[/dim]")

    def data(self, obj: Any) -> None:
        """Output raw data — JSON in json mode, pretty in normal mode."""
        if self._json_mode:
            self._console.print(json.dumps(obj, indent=2, default=str))
        else:
            self._console.print(obj)
