"""Shared CLI helpers used across command modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from open_orchestrator.core.batch import BatchResult
    from open_orchestrator.models.status import WorktreeAIStatus
    from open_orchestrator.models.worktree_info import WorktreeInfo

import click
from rich.console import Console

from open_orchestrator.core.status import StatusTracker, runtime_status_config
from open_orchestrator.core.worktree import (
    NotAGitRepositoryError,
    WorktreeManager,
    WorktreeNotFoundError,
)

console = Console()


def get_worktree_manager(repo_path: Path | None = None) -> WorktreeManager:
    """Get a WorktreeManager instance with error handling."""
    try:
        return WorktreeManager(repo_path)
    except NotAGitRepositoryError as e:
        raise click.ClickException(str(e)) from e


def get_status_tracker(repo_path: Path | None = None) -> StatusTracker:
    """Build a status tracker anchored to the current repo when possible."""
    return StatusTracker(runtime_status_config(repo_path))


def print_batch_status(results: list[BatchResult]) -> None:
    """Print compact batch status counts."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    console.print(f"  [dim]{' | '.join(parts)}[/dim]")


@dataclass(frozen=True)
class ResolvedSession:
    """Single answer for "what session does this identifier refer to?"

    Returned by :func:`resolve_session_target` so callers (``owt send``,
    ``owt switch``, ``owt delete``, ``owt attach``) handle worktree-mode
    and branch-mode sessions through one code path.
    """

    name: str
    worktree: WorktreeInfo | None
    status: WorktreeAIStatus | None

    @property
    def session_type(self) -> str:
        if self.status is not None:
            return self.status.session_type or "worktree"
        return "worktree"

    @property
    def is_branch(self) -> bool:
        return self.session_type == "branch"


def resolve_session_target(
    identifier: str,
    wt_manager: WorktreeManager,
    tracker: StatusTracker,
) -> ResolvedSession:
    """Resolve ``identifier`` to a session, looking at worktrees then status DB.

    Order:
      1. Try ``WorktreeManager.get(identifier)`` for worktree-mode sessions.
      2. On ``WorktreeNotFoundError``, look up ``tracker.get_status(identifier)``
         — branch-mode sessions live only in the status DB.
      3. If neither exists, raise :class:`click.ClickException`.
    """
    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError:
        status = tracker.get_status(identifier)
        if status is None:
            raise click.ClickException(
                f"No worktree or branch session named '{identifier}'. Run 'owt list' to see what's available."
            ) from None
        return ResolvedSession(name=status.worktree_name, worktree=None, status=status)
    status = tracker.get_status(worktree.name)
    return ResolvedSession(name=worktree.name, worktree=worktree, status=status)


def print_batch_results(results: list[BatchResult], heading: str = "Batch complete") -> None:
    """Print final batch execution summary."""
    from open_orchestrator.core.batch import BatchStatus

    shipped = sum(1 for r in results if r.status == BatchStatus.SHIPPED)
    completed = sum(1 for r in results if r.status == BatchStatus.COMPLETED)
    failed = sum(1 for r in results if r.status == BatchStatus.FAILED)
    console.print(f"\n[bold]{heading}:[/bold] {shipped} shipped, {completed} done, {failed} failed")
    for r in results:
        icon = {
            BatchStatus.SHIPPED: "[green]✓[/green]",
            BatchStatus.COMPLETED: "[cyan]●[/cyan]",
            BatchStatus.FAILED: "[red]✗[/red]",
        }.get(r.status, "[dim]?[/dim]")
        label = getattr(r.task, "id", None) or r.task.description[:50]
        err = f" — {r.error}" if r.error else ""
        console.print(f"  {icon} {label}{err}")
