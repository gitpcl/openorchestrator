"""Dream daemon commands: enable, disable, status, consolidate."""

from __future__ import annotations

import json

import click

from open_orchestrator.commands._shared import console


@click.group("dream")
def dream_group() -> None:
    """Background dream daemon for proactive review and consolidation."""


@dream_group.command("enable")
@click.option("--foreground", is_flag=True, help="Run in foreground (for debugging).")
def enable_dream(foreground: bool) -> None:
    """Start the dream daemon.

    The daemon runs in the background, waking periodically to consolidate
    memory, detect stale worktrees, and generate reports.
    """
    from open_orchestrator.core.dream import DreamDaemon

    daemon = DreamDaemon()
    pid = daemon.start(foreground=foreground)
    if not foreground:
        console.print(f"[green]Dream daemon started[/green] (PID {pid})")


@dream_group.command("disable")
def disable_dream() -> None:
    """Stop the dream daemon."""
    from open_orchestrator.core.dream import DreamDaemon

    daemon = DreamDaemon()
    if daemon.stop():
        console.print("[green]Dream daemon stopped.[/green]")
    else:
        console.print("[dim]Dream daemon was not running.[/dim]")


@dream_group.command("status")
def dream_status() -> None:
    """Show dream daemon status and last heartbeat."""
    from open_orchestrator.core.dream import DreamDaemon

    daemon = DreamDaemon()
    status = daemon.status()

    if status.running:
        console.print(f"[green]Running[/green] (PID {status.pid})")
        if status.last_heartbeat:
            console.print(f"  Last heartbeat: {status.last_heartbeat.isoformat()}")
    else:
        console.print("[dim]Not running[/dim]")

    if status.last_report:
        console.print(f"  Last report: {status.last_report}")


@dream_group.command("consolidate")
def dream_consolidate() -> None:
    """Run consolidation immediately without starting the daemon."""
    from open_orchestrator.core.dream import DreamDaemon

    daemon = DreamDaemon()
    report = daemon.consolidate_now()

    if not report.findings:
        console.print("[green]Nothing to consolidate — all clean.[/green]")
    else:
        console.print(f"[bold]Dream report ({report.duration_seconds:.1f}s):[/bold]")
        for f in report.findings:
            prefix = f"[{f.worktree}] " if f.worktree else ""
            console.print(f"  {prefix}{f.category}: {f.message}")

    console.print(f"[dim]  Memory actions: {report.memory_actions} | Stale worktrees: {report.stale_worktrees}[/dim]")


@dream_group.command("reports")
@click.option("--limit", "-n", default=5, help="Number of reports to show.")
def dream_reports(limit: int) -> None:
    """List recent dream reports."""
    from open_orchestrator.core.dream import DreamDaemon

    daemon = DreamDaemon()
    reports = daemon.list_reports(limit=limit)

    if not reports:
        console.print("[dim]No dream reports found.[/dim]")
        return

    console.print(f"[bold]{len(reports)} recent report(s):[/bold]\n")
    for path in reports:
        try:
            data = json.loads(path.read_text())
            findings = len(data.get("findings", []))
            console.print(f"  {path.name} — {findings} finding(s), {data.get('duration_seconds', 0):.1f}s")
        except (json.JSONDecodeError, OSError):
            console.print(f"  {path.name} — [dim]unreadable[/dim]")


def register(main: click.Group) -> None:
    """Register dream commands on the main CLI group."""
    main.add_command(dream_group)
