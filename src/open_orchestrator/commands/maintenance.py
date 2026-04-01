"""Maintenance commands: sync, cleanup, version."""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext

import click

from open_orchestrator.commands._shared import console, get_worktree_manager
from open_orchestrator.config import load_config
from open_orchestrator.core.worktree import WorktreeNotFoundError

logger = logging.getLogger(__name__)


def register(main: click.Group) -> None:
    """Register maintenance commands on the main CLI group."""

    @main.command("sync")
    @click.argument("identifier", required=False)
    @click.option("--all", "sync_all", is_flag=True, help="Sync all worktrees.")
    @click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
    def sync_worktrees(identifier: str | None, sync_all: bool, json_output: bool) -> None:
        """Sync worktree(s) with upstream.

        Examples:
            owt sync my-feature      # Sync single worktree
            owt sync --all           # Sync all worktrees
        """
        from open_orchestrator.core.sync import SyncConfig as SyncServiceConfig
        from open_orchestrator.core.sync import SyncService

        config = load_config()
        sync_config = SyncServiceConfig(
            strategy=config.sync.default_strategy,
            auto_stash=config.sync.auto_stash,
            prune_remote=config.sync.prune_remote,
        )

        wt_manager = get_worktree_manager()
        sync_service = SyncService(sync_config)

        if sync_all:
            worktrees = [wt for wt in wt_manager.list_all() if not wt.is_main]
            worktree_paths = [str(wt.path) for wt in worktrees]
            with console.status("[bold blue]Syncing all worktrees...") if not json_output else nullcontext():
                report = sync_service.sync_all(worktree_paths)

            if json_output:
                console.print(json.dumps(report.model_dump(mode="json"), indent=2, default=str))
            else:
                console.print(
                    f"\n[bold]Sync complete:[/bold] {report.successful} ok, "
                    f"{report.failed} failed, {report.up_to_date} up-to-date"
                )
                for r in report.results:
                    status_val = r.status if isinstance(r.status, str) else r.status.value
                    if status_val == "success":
                        icon = "[green]✓[/green]"
                    elif status_val == "up_to_date":
                        icon = "[yellow]~[/yellow]"
                    else:
                        icon = "[red]✗[/red]"
                    console.print(f"  {icon} {r.branch_name}: {r.message}")
        elif identifier:
            try:
                worktree = wt_manager.get(identifier)
            except WorktreeNotFoundError as e:
                raise click.ClickException(str(e)) from e

            result = sync_service.sync_worktree(str(worktree.path))
            if json_output:
                console.print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
            else:
                console.print(f"[bold]{result.branch_name}:[/bold] {result.message}")
        else:
            raise click.ClickException("Specify a worktree name or use --all")

    @main.command("cleanup")
    @click.option("--force", is_flag=True, help="Actually delete stale worktrees (default is dry-run).")
    @click.option("--days", type=int, default=14, help="Days of inactivity threshold.")
    @click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
    def cleanup_worktrees(force: bool, days: int, json_output: bool) -> None:
        """Remove stale worktrees (dry-run by default).

        Identifies worktrees that haven't been touched in --days days
        and offers to remove them.
        """
        from open_orchestrator.core.cleanup import CleanupConfig, CleanupService

        wt_manager = get_worktree_manager()
        cleanup_config = CleanupConfig(
            stale_threshold_days=days,
        )

        service = CleanupService(cleanup_config)
        worktrees = [wt for wt in wt_manager.list_all() if not wt.is_main]
        worktree_paths = [str(wt.path) for wt in worktrees]

        with console.status("[bold blue]Scanning worktrees...") if not json_output else nullcontext():
            report = service.cleanup(worktree_paths, dry_run=not force, force=force)

        if json_output:
            console.print(json.dumps(report.model_dump(mode="json"), indent=2, default=str))
            return

        if report.stale_worktrees_found == 0:
            console.print("[green]No stale worktrees found.[/green]")
            return

        console.print(f"\n[bold]{'Cleaned' if force else 'Would clean'}:[/bold] {report.stale_worktrees_found} stale worktree(s)")
        for path in report.cleaned_paths:
            console.print(f"  [red]✗[/red] {path}")
        for path in report.skipped_paths:
            console.print(f"  [yellow]~[/yellow] {path}")

        if not force:
            console.print("\n[dim]Dry run. Use --force to actually delete.[/dim]")

    @main.command("version")
    def version_cmd() -> None:
        """Show version."""
        try:
            from importlib.metadata import version

            ver = version("open-orchestrator")
        except Exception:
            logger.debug("Failed to read package version", exc_info=True)
            ver = "dev"
        console.print(f"open-orchestrator {ver}")
