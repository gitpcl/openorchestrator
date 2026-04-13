"""Protocol definition for AI coding tools.

All AI tools (built-in and custom) implement this protocol so the
orchestrator can discover, launch, and manage them uniformly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class AIToolProtocol(Protocol):
    """Interface that all AI coding tools must satisfy."""

    @property
    def name(self) -> str:
        """Unique tool identifier (e.g., 'claude', 'aider')."""
        ...

    @property
    def binary(self) -> str:
        """Binary/executable name."""
        ...

    @property
    def supports_hooks(self) -> bool:
        """Whether this tool supports OWT status hooks."""
        ...

    @property
    def supports_headless(self) -> bool:
        """Whether the tool can run non-interactively.

        Requires both a non-interactive execution mode (e.g. Claude's ``-p``)
        and a hook mechanism to report status back to ``owt``.
        """
        ...

    @property
    def supports_plan_mode(self) -> bool:
        """Whether the tool supports a plan-first (read-only) mode."""
        ...

    @property
    def install_hint(self) -> str:
        """Installation instructions for the user."""
        ...

    def get_command(
        self,
        *,
        executable_path: str | None = None,
        plan_mode: bool = False,
        prompt: str | None = None,
    ) -> str:
        """Build the shell command to launch this tool."""
        ...

    def is_installed(self) -> bool:
        """Check if this tool is available on the system."""
        ...

    def get_known_paths(self) -> list[Path]:
        """Known installation paths to check beyond PATH."""
        ...

    def install_hooks(
        self,
        worktree_path: Path,
        worktree_name: str,
        db_path: str | Path | None = None,
    ) -> bool:
        """Install status-reporting hooks into the worktree.

        Tools where ``supports_hooks`` is False must return False without
        writing anything.
        """
        ...
