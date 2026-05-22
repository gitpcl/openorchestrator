"""Detect installed AI coding tools via the tool registry.

All detection flows through ``tool_registry.get_registry().list_installed()``
so built-in tools (claude, pi, opencode, droid) and registered extras
(codex, gemini-cli, aider, amp, kilo-code) are discovered uniformly.
"""

from __future__ import annotations

from open_orchestrator.core.tool_registry import get_registry

# Auto-pick priority for the default-selection prompt. Tools not listed
# here fall through to alphabetical order after the known set.
_PRIORITY: tuple[str, ...] = ("claude", "pi", "droid", "opencode")


def _priority_key(name: str) -> tuple[int, str]:
    try:
        return (_PRIORITY.index(name), name)
    except ValueError:
        return (len(_PRIORITY), name)


def detect_installed_agents() -> list[str]:
    """Return the names of all installed AI tools, ordered by auto-pick priority."""
    names = [tool.name for tool in get_registry().list_installed()]
    return sorted(names, key=_priority_key)


def detect_all_agents() -> list[str]:
    """Alias for ``detect_installed_agents``.

    Kept for backwards compatibility with call sites that distinguished
    "core" from "extras" when detection was split across the enum and an
    ad-hoc binary list.
    """
    return detect_installed_agents()
