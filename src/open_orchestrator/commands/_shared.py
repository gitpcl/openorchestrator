"""Shared CLI helpers used across command modules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from open_orchestrator.core.batch import BatchResult

import click
from rich.console import Console

from open_orchestrator.core.status import StatusTracker, runtime_status_config
from open_orchestrator.core.worktree import NotAGitRepositoryError, WorktreeManager

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
