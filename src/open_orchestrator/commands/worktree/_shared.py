"""Helpers shared across the ``worktree`` command package.

These were previously module-level helpers in ``commands/worktree.py``.
They live here so each subcommand module stays focused on its Click
glue. The names with leading underscores are kept as-is because tests
in ``tests/test_commands_worktree.py`` import them directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import click

from open_orchestrator.commands._shared import console

if TYPE_CHECKING:
    from open_orchestrator.config import Config

logger = logging.getLogger(__name__)


def _resolve_ai_tool(ai_tool: str | None) -> str:
    """Auto-detect AI tool if not specified. Returns tool name string."""
    if ai_tool is not None:
        return ai_tool

    from open_orchestrator.core.agent_detector import detect_installed_agents

    installed = detect_installed_agents()
    if len(installed) == 0:
        raise click.ClickException("No AI coding tools found. Install claude, pi, opencode, or droid.")
    if len(installed) == 1:
        return installed[0]

    console.print("\n[bold]Detected AI tools:[/bold]")
    for i, name in enumerate(installed, 1):
        console.print(f"  {i}. {name}")
    choice: int = click.prompt("Select AI tool", type=click.IntRange(1, len(installed)), default=1)
    return installed[choice - 1]


def _resolve_branch(
    description: tuple[str, ...],
    explicit_branch: str | None,
    prefix: str | None,
) -> tuple[str, str]:
    """Resolve task description and branch name. Returns (task_description, branch)."""
    from open_orchestrator.core.branch_namer import generate_branch_name

    if description:
        task_description = " ".join(description)
    elif explicit_branch:
        task_description = ""
    else:
        task_description = click.prompt("What are you working on?")

    if explicit_branch:
        return task_description, explicit_branch

    if not task_description.strip():
        raise click.ClickException("Task description cannot be empty")
    try:
        branch = generate_branch_name(task_description, prefix=prefix)
    except ValueError as e:
        raise click.ClickException(f"Could not generate branch name: {e}") from e
    return task_description, branch


def load_config_safe() -> Config:
    """Load config, falling back to defaults on any error."""
    from open_orchestrator.config import Config, load_config

    try:
        return load_config()
    except Exception:  # noqa: BLE001
        return Config()


def _check_git_ref_conflicts(branch: str) -> str:
    """Check for git ref conflicts and prompt for alternative if needed."""
    from git import Repo

    try:
        repo = Repo(search_parent_directories=True)
        existing_refs = {ref.name for ref in repo.refs}
        branch_parts = branch.split("/")
        for i in range(1, len(branch_parts)):
            partial = "/".join(branch_parts[:i])
            if partial in existing_refs:
                console.print(f"[yellow]Branch '{partial}' exists — cannot create '{branch}' (git ref conflict).[/yellow]")
                result: str = click.prompt("Enter a different branch name")
                return result
    except Exception:
        logger.debug("Git ref conflict check failed", exc_info=True)
    return branch
