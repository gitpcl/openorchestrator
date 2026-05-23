"""Diagnostic command: owt doctor — detect and fix orphaned resources."""

from __future__ import annotations

import click
from rich.table import Table

from open_orchestrator.commands._shared import console, get_status_tracker, get_worktree_manager
from open_orchestrator.core.backend_factory import BackendUnavailableError, select_backend
from open_orchestrator.models.backend import BackendSession


@click.command("doctor")
@click.option("--fix", is_flag=True, help="Clean up detected orphans (default is diagnosis only).")
def doctor(fix: bool) -> None:
    """Diagnose and fix orphaned worktree resources.

    Cross-checks worktrees, backend (tmux/herdr) sessions, and status DB
    entries to find inconsistencies: worktrees without sessions,
    sessions without worktrees, and stale status entries.

    Read-only by default. Use --fix to clean up.
    """
    wt_manager = get_worktree_manager()
    tracker = get_status_tracker(wt_manager.git_root)

    # Gather current state.
    worktrees = {wt.name for wt in wt_manager.list_all() if not wt.is_main}
    all_statuses = list(tracker.get_all_statuses())
    statuses = {s.worktree_name for s in all_statuses}

    # Resolve each worktree's recorded session via the DB; missing rows
    # mean the worktree was created outside of owt or before the backend
    # bookkeeping landed.
    live_sessions: dict[str, BackendSession] = {}
    for wt_name in worktrees | statuses:
        session = tracker.get_backend_session(wt_name)
        if session is None:
            continue
        try:
            backend = select_backend(None, override=session.kind.value)
        except BackendUnavailableError:
            continue
        if backend.is_alive(session):
            live_sessions[wt_name] = session

    live_session_names = set(live_sessions.keys())

    # Detect orphans.
    orphan_wt_no_session = worktrees - live_session_names
    orphan_session_no_wt = live_session_names - worktrees
    orphan_status_no_wt = statuses - worktrees

    total_issues = len(orphan_wt_no_session) + len(orphan_session_no_wt) + len(orphan_status_no_wt)

    if total_issues == 0:
        console.print("[green]No orphaned resources found. All clean.[/green]")
        return

    table = Table(title="Orphaned Resources", show_header=True, header_style="bold")
    table.add_column("Resource")
    table.add_column("Issue")
    table.add_column("Name")

    for name in sorted(orphan_wt_no_session):
        table.add_row("worktree", "[yellow]no session[/yellow]", name)
    for name in sorted(orphan_session_no_wt):
        kind = live_sessions[name].kind.value
        table.add_row(kind, "[yellow]no worktree[/yellow]", live_sessions[name].id)
    for name in sorted(orphan_status_no_wt):
        table.add_row("status", "[yellow]no worktree[/yellow]", name)

    console.print(table)
    console.print(f"\n[bold]{total_issues} issue(s) found.[/bold]")

    if not fix:
        console.print("[dim]Run with --fix to clean up.[/dim]")
        return

    fixed = 0

    # Kill orphaned sessions via their backend (tmux or herdr).
    for name in orphan_session_no_wt:
        session = live_sessions[name]
        try:
            backend = select_backend(None, override=session.kind.value)
            backend.kill(session)
            console.print(f"  [green]Killed {session.kind.value} session:[/green] {session.id}")
            fixed += 1
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]Failed to kill {session.id}: {e}[/red]")

    # Remove orphaned status entries.
    for name in orphan_status_no_wt:
        try:
            tracker.remove_status(name)
            console.print(f"  [green]Removed status entry:[/green] {name}")
            fixed += 1
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]Failed to remove status for {name}: {e}[/red]")

    # Note: orphaned worktrees (no session) are NOT deleted — they may be headless.
    if orphan_wt_no_session:
        console.print(
            f"\n[dim]{len(orphan_wt_no_session)} worktree(s) without sessions left untouched "
            f"(may be headless). Use 'owt delete' to remove manually.[/dim]"
        )

    console.print(f"\n[green]Fixed {fixed} issue(s).[/green]")


def register(main: click.Group) -> None:
    """Register doctor command on the main CLI group."""
    main.add_command(doctor)
