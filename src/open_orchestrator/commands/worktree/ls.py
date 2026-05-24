"""``owt list`` — quick text list of worktrees and branch sessions."""

from __future__ import annotations

import click
from rich.table import Table

from open_orchestrator.commands import worktree as _pkg
from open_orchestrator.commands._shared import console
from open_orchestrator.models.status import AIActivityStatus


def _activity_label(act: AIActivityStatus) -> str:
    """Render an activity status as a colored Rich label."""
    if act == AIActivityStatus.WORKING:
        return "[green]● working[/green]"
    if act == AIActivityStatus.IDLE:
        return "[dim]○ idle[/dim]"
    if act == AIActivityStatus.BLOCKED:
        return "[yellow]⚠ blocked[/yellow]"
    if act == AIActivityStatus.COMPLETED:
        return "[cyan]✓ done[/cyan]"
    return f"[dim]{act.value}[/dim]"


@click.command("list")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show all worktrees including main.")
def list_worktrees(show_all: bool) -> None:
    """List all worktrees and branch sessions with status.

    Quick text list (non-interactive, for scripts/pipes).
    Shows branch-mode sessions alongside git worktrees.
    """
    wt_manager = _pkg.get_worktree_manager()
    worktrees = wt_manager.list_all()

    tracker = _pkg.get_status_tracker(wt_manager.git_root)
    all_statuses = {s.worktree_name: s for s in tracker.get_all_statuses()}

    # Collect branch-mode sessions from status DB (not in git worktree list)
    worktree_names = {wt.name for wt in worktrees}
    branch_sessions: list[dict[str, str]] = []
    for s in tracker.get_all_statuses():
        if s.worktree_name not in worktree_names and s.branch:
            branch_sessions.append(
                {
                    "name": s.worktree_name,
                    "branch": s.branch,
                }
            )

    if not show_all:
        worktrees = [wt for wt in worktrees if not wt.is_main]

    if not worktrees and not branch_sessions:
        console.print("[dim]No worktrees or branch sessions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Branch")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Task")
    table.add_column("Session")

    for wt in worktrees:
        status = all_statuses.get(wt.name)
        status_str = ""
        task_str = ""
        session_str = ""

        if status:
            status_str = _activity_label(status.activity_status)
            task_str = (status.current_task or "")[:40]
            session_id = status.backend_session_id or status.tmux_session or ""
            session_str = f"{status.backend_kind}:{session_id}" if session_id else ""

        name = "[bold]" + wt.name + "[/bold]" if wt.is_main else wt.name
        type_str = "[bold]main[/bold]" if wt.is_main else "[dim]worktree[/dim]"
        table.add_row(name, wt.branch, type_str, status_str, task_str, session_str)

    for bs in branch_sessions:
        status = all_statuses.get(bs["name"])
        status_str = ""
        task_str = ""
        session_str = ""
        if status:
            status_str = _activity_label(status.activity_status)
            task_str = (status.current_task or "")[:40]
            session_id = status.backend_session_id or status.tmux_session or ""
            session_str = f"{status.backend_kind}:{session_id}" if session_id else ""
        table.add_row(
            bs["name"],
            bs["branch"],
            "[cyan]branch[/cyan]",
            status_str,
            task_str,
            session_str,
        )

    console.print(table)
