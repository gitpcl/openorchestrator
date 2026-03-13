"""Detect installed AI coding tools on the system.

Extends the existing AITool enum detection with support for additional tools
beyond the core three (claude, opencode, droid).
"""

import shutil

from open_orchestrator.config import AITool

# Additional tools not in the AITool enum but detectable on the system
_EXTRA_AGENT_BINARIES: dict[str, str] = {
    "codex": "codex",
    "gemini-cli": "gemini",
    "aider": "aider",
    "amp": "amp",
    "kilo-code": "kilo-code",
}


def detect_installed_agents() -> list[AITool]:
    """Detect all installed AI coding tools.

    Checks the core AITool enum members first, then additional known tools.

    Returns:
        List of installed AITool enum values. For tools not in the enum,
        they are returned as the closest matching enum value or skipped.
    """
    installed: list[AITool] = []

    # Check core tools via existing AITool.is_installed()
    for tool in AITool:
        if AITool.is_installed(tool):
            installed.append(tool)

    return installed


def detect_all_agents() -> list[str]:
    """Detect all installed AI coding tools, including non-enum ones.

    Returns:
        List of tool name strings for all detected tools.
    """
    found: list[str] = []

    # Core tools
    for tool in AITool:
        if AITool.is_installed(tool):
            found.append(tool.value)

    # Extra tools
    for name, binary in _EXTRA_AGENT_BINARIES.items():
        if shutil.which(binary) is not None:
            found.append(name)

    return found
