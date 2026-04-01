"""Database maintenance commands: purge, vacuum, health."""

from __future__ import annotations

import json

import click

from open_orchestrator.commands._shared import console, get_status_tracker


def register(main: click.Group) -> None:
    """Register db commands on the main CLI group."""

    @main.group("db")
    def db_group() -> None:
        """Database maintenance and diagnostics."""

    @db_group.command("purge")
    @click.option("--days", type=int, default=30, help="Delete messages older than N days.")
    def purge_messages(days: int) -> None:
        """Purge old peer messages from the database."""
        tracker = get_status_tracker()
        deleted = tracker.purge_old_messages(days)
        console.print(f"[green]Purged {deleted} message(s) older than {days} days.[/green]")

    @db_group.command("vacuum")
    def vacuum_db() -> None:
        """Optimize and compact the database."""
        tracker = get_status_tracker()
        tracker.vacuum()
        console.print("[green]Database optimized and vacuumed.[/green]")

    @db_group.command("health")
    @click.option("--check", is_flag=True, help="Exit non-zero if thresholds exceeded.")
    def health_check(check: bool) -> None:
        """Show database health diagnostics as JSON.

        Use --check for CI: exits 1 if message count exceeds 10K
        or DB size exceeds 100MB.
        """
        tracker = get_status_tracker()
        health = tracker.health_check()
        console.print(json.dumps(health, indent=2))

        if check:
            issues: list[str] = []
            if health["peer_message_count"] > 10_000:  # type: ignore[operator]
                issues.append(f"Peer messages: {health['peer_message_count']} (threshold: 10000)")
            if health["db_size_bytes"] > 100_000_000:  # type: ignore[operator]
                issues.append(f"DB size: {health['db_size_bytes']} bytes (threshold: 100MB)")
            if issues:
                for issue in issues:
                    console.print(f"[red]FAIL: {issue}[/red]")
                raise SystemExit(1)
            console.print("[green]All checks passed.[/green]")
