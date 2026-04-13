"""Detect installed AI coding tools via the tool registry.

All detection flows through ``tool_registry.get_registry().list_installed()``
so built-in tools (claude, opencode, droid) and registered extras
(codex, gemini-cli, aider, amp, kilo-code) are discovered uniformly.
"""

from __future__ import annotations

from open_orchestrator.core.tool_registry import get_registry


def detect_installed_agents() -> list[str]:
    """Return the names of all installed AI tools, sorted alphabetically."""
    return sorted(tool.name for tool in get_registry().list_installed())


def detect_all_agents() -> list[str]:
    """Alias for ``detect_installed_agents``.

    Kept for backwards compatibility with call sites that distinguished
    "core" from "extras" when detection was split across the enum and an
    ad-hoc binary list.
    """
    return detect_installed_agents()
