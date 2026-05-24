"""Worktree CRUD commands: new, list, switch, delete, branch, attach.

This package replaces the former ``commands/worktree.py`` monolith
(Sprint 027 Phase 8 carve-up). Each Click subcommand lives in its own
submodule. Private helpers that were previously module-level
(``_resolve_ai_tool``, ``_resolve_branch``, ``load_config_safe``,
``_check_git_ref_conflicts``) plus a handful of collaborator imports
(``get_worktree_manager``, ``get_status_tracker``, ``AgentLauncher``,
``get_registry``) are re-exported here so the package presents the
same patchable surface tests relied on against the old monolith
(``tests/test_commands_worktree.py``, ``tests/test_headless*.py``,
``tests/test_branch_mode.py``). Submodules look these names up via
``open_orchestrator.commands.worktree`` at call time so monkeypatching
the package attribute is observed inside the subcommand handlers.
"""

from __future__ import annotations

import click

# Patchable collaborator imports MUST be bound on the package before any
# subcommand module is imported, because each subcommand module reaches
# back via ``from open_orchestrator.commands import worktree as _pkg``
# at import time. The reference itself (``_pkg``) is bound to the
# partially-initialised package module; attribute resolution happens
# later at call time, so by then these names exist on the package.
from open_orchestrator.commands._shared import (
    get_status_tracker,
    get_worktree_manager,
)
from open_orchestrator.commands.worktree._shared import (
    _check_git_ref_conflicts,
    _resolve_ai_tool,
    _resolve_branch,
    load_config_safe,
)
from open_orchestrator.core.agent_launcher import AgentLauncher
from open_orchestrator.core.tool_registry import get_registry

# isort: split
# Submodule imports come AFTER the patchable shims so that even though
# Python returns the still-initialising package module to submodules,
# the names they will resolve at call time are already set.
from open_orchestrator.commands.worktree.attach import attach_worktree
from open_orchestrator.commands.worktree.branch import branch_cmd
from open_orchestrator.commands.worktree.delete import delete_worktree
from open_orchestrator.commands.worktree.ls import list_worktrees
from open_orchestrator.commands.worktree.new import new_worktree
from open_orchestrator.commands.worktree.switch import switch_worktree

__all__ = [
    "AgentLauncher",
    "_check_git_ref_conflicts",
    "_resolve_ai_tool",
    "_resolve_branch",
    "attach_worktree",
    "branch_cmd",
    "delete_worktree",
    "get_registry",
    "get_status_tracker",
    "get_worktree_manager",
    "list_worktrees",
    "load_config_safe",
    "new_worktree",
    "register",
    "switch_worktree",
]


def register(main: click.Group) -> None:
    """Register worktree commands on the main CLI group."""
    main.add_command(new_worktree)
    main.add_command(list_worktrees)
    main.add_command(switch_worktree)
    main.add_command(delete_worktree)
    main.add_command(branch_cmd)
    main.add_command(attach_worktree)
