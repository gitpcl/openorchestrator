"""
Open Orchestrator - Git Worktree + Claude Code orchestration tool.

This package provides functionality for managing parallel development
workflows using Git worktrees and tmux sessions with Claude Code.
"""

__version__ = "0.1.0"

from open_orchestrator.config import Config, load_config

__all__ = [
    "__version__",
    "Config",
    "load_config",
]
