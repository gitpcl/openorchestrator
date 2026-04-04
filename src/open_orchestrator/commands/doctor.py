"""Diagnostic command: owt doctor — detect and fix orphaned resources."""

from __future__ import annotations

import click
from rich.table import Table

from open_orchestrator.commands._shared import console, get_status_tracker, get_worktree_manager
from open_orchestrator.core.tmux_manager import TmuxManager


@click.command("doctor")
@click.option("--fix", is_flag=True, help="Clean up detected orphans (default is diagnosis only).")
def doctor(fix: bool) -> None:
    """Diagnose and fix orphaned worktree resources.

    Cross-checks worktrees, tmux sessions, and status DB entries
    to find inconsistencies: worktrees without tmux sessions,
    tmux sessions without worktrees, and stale status entries.

    Read-only by default. Use --fix to clean up.
    """
    wt_manager = get_worktree_manager()
    tracker = get_status_tracker(wt_manager.git_root)
    tmux = TmuxManager()

    # Gather current state
    worktrees = {wt.name for wt in wt_manager.list_all() if not wt.is_main}
    statuses = {s.worktree_name for s in tracker.get_all_statuses()}

    tmux_sessions: set[str] = set()
    for wt_name in worktrees | statuses:
        session_name = tmux.generate_session_name(wt_name)
        if tmux.session_exists(session_name):
            tmux_sessions.add(wt_name)

    # Detect orphans
    orphan_wt_no_tmux = worktrees - tmux_sessions  # worktrees with no tmux session
    orphan_tmux_no_wt = tmux_sessions - worktrees  # tmux sessions with no worktree
    orphan_status_no_wt = statuses - worktrees  # status entries with no worktree

    total_issues = len(orphan_wt_no_tmux) + len(orphan_tmux_no_wt) + len(orphan_status_no_wt)

    if total_issues == 0:
        console.print("[green]No orphaned resources found. All clean.[/green]")
        return

    # Report
    table = Table(title="Orphaned Resources", show_header=True, header_style="bold")
    table.add_column("Resource")
    table.add_column("Issue")
    table.add_column("Name")

    for name in sorted(orphan_wt_no_tmux):
        table.add_row("worktree", "[yellow]no tmux session[/yellow]", name)
    for name in sorted(orphan_tmux_no_wt):
        table.add_row("tmux", "[yellow]no worktree[/yellow]", tmux.generate_session_name(name))
    for name in sorted(orphan_status_no_wt):
        table.add_row("status", "[yellow]no worktree[/yellow]", name)

    console.print(table)
    console.print(f"\n[bold]{total_issues} issue(s) found.[/bold]")

    if not fix:
        console.print("[dim]Run with --fix to clean up.[/dim]")
        return

    # Fix orphans
    fixed = 0

    # Kill orphaned tmux sessions (tmux exists but worktree doesn't)
    for name in orphan_tmux_no_wt:
        session_name = tmux.generate_session_name(name)
        try:
            tmux.kill_session(session_name)
            console.print(f"  [green]Killed tmux session:[/green] {session_name}")
            fixed += 1
        except Exception as e:
            console.print(f"  [red]Failed to kill {session_name}: {e}[/red]")

    # Remove orphaned status entries (status exists but worktree doesn't)
    for name in orphan_status_no_wt:
        try:
            tracker.remove_status(name)
            console.print(f"  [green]Removed status entry:[/green] {name}")
            fixed += 1
        except Exception as e:
            console.print(f"  [red]Failed to remove status for {name}: {e}[/red]")

    # Note: orphaned worktrees (no tmux) are NOT deleted — they may be headless
    if orphan_wt_no_tmux:
        console.print(
            f"\n[dim]{len(orphan_wt_no_tmux)} worktree(s) without tmux sessions left untouched "
            f"(may be headless). Use 'owt delete' to remove manually.[/dim]"
        )

    console.print(f"\n[green]Fixed {fixed} issue(s).[/green]")


def register(main: click.Group) -> None:
    """Register doctor command on the main CLI group."""
    main.add_command(doctor)
