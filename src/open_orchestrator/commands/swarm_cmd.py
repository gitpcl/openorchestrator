"""Swarm commands: launch, list, stop, broadcast."""

from __future__ import annotations

import click

from open_orchestrator.commands._shared import console
from open_orchestrator.core.swarm import SwarmError, SwarmManager
from open_orchestrator.models.swarm import SwarmRole

# Module-level singleton so multiple CLI invocations in one process share state.
# In practice, each `owt swarm ...` call is a new process, so state is only
# shared within a single invocation (or within tests).
_manager = SwarmManager()


def get_manager() -> SwarmManager:
    """Return the module-level swarm manager (exposed for tests)."""
    return _manager


@click.group("swarm")
def swarm_group() -> None:
    """Launch coordinator + specialized workers for parallel tasks."""


@swarm_group.command("start")
@click.argument("goal")
@click.option(
    "--worktree",
    "-w",
    required=True,
    help="Worktree the swarm runs in.",
)
@click.option(
    "--session",
    "tmux_session",
    default=None,
    help="Tmux session for workers. Defaults to owt-<worktree>.",
)
@click.option(
    "--role",
    "roles",
    multiple=True,
    type=click.Choice([r.value for r in SwarmRole if r != SwarmRole.COORDINATOR]),
    help="Specialist roles to include (repeatable). Defaults to all four.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Build swarm state without starting tmux panes.",
)
def start_swarm(
    goal: str,
    worktree: str,
    tmux_session: str | None,
    roles: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Launch a swarm coordinator with specialized workers.

    Examples:

        owt swarm start "implement JWT auth" -w feature-auth

        owt swarm start "refactor core" -w refactor-core --role researcher --role reviewer

        owt swarm start "add tests" -w test-fix --dry-run
    """
    session_name = tmux_session or f"owt-{worktree}"
    role_enums = tuple(SwarmRole(r) for r in roles) if roles else None

    try:
        state = _manager.start_swarm(
            goal=goal,
            worktree=worktree,
            tmux_session=session_name,
            roles=role_enums,
            dry_run=dry_run,
        )
    except SwarmError as exc:
        console.print(f"[red]Swarm error:[/red] {exc}")
        raise click.Abort()

    console.print(f"[bold green]Started swarm[/bold green] [cyan]{state.swarm_id}[/cyan]")
    console.print(f"  goal: {state.goal}")
    console.print(f"  worktree: {state.worktree}")
    console.print(f"  workers: {len(state.workers)}")
    for worker in state.workers:
        marker = "[yellow]coord[/yellow]" if worker.id == state.coordinator_id else "[dim]worker[/dim]"
        console.print(f"    {marker} [cyan]{worker.role.value:12s}[/cyan] {worker.id}")
    if dry_run:
        console.print("[dim](dry-run: no tmux panes created)[/dim]")


@swarm_group.command("list")
def list_swarms() -> None:
    """List all active swarms."""
    swarms = _manager.list_swarms()
    if not swarms:
        console.print("[dim]No active swarms.[/dim]")
        return
    console.print(f"[bold]{len(swarms)} active swarm(s):[/bold]")
    for state in swarms:
        console.print(
            f"  [cyan]{state.swarm_id}[/cyan] "
            f"worktree=[green]{state.worktree}[/green] "
            f"workers={len(state.workers)} "
            f"goal={state.goal[:60]}"
        )


@swarm_group.command("stop")
@click.argument("swarm_id")
def stop_swarm(swarm_id: str) -> None:
    """Stop a swarm and kill all its worker panes."""
    if _manager.stop_swarm(swarm_id):
        console.print(f"[green]Stopped swarm[/green] {swarm_id}")
    else:
        console.print(f"[yellow]Swarm not found:[/yellow] {swarm_id}")


@swarm_group.command("send")
@click.argument("swarm_id")
@click.argument("message")
@click.option(
    "--no-coordinator",
    is_flag=True,
    help="Exclude the coordinator from the broadcast.",
)
def send_swarm(swarm_id: str, message: str, no_coordinator: bool) -> None:
    """Broadcast a message to all workers in a swarm.

    Example:

        owt swarm send swarm-abc12345 "status check"
    """
    try:
        targets = _manager.broadcast(
            swarm_id,
            message,
            include_coordinator=not no_coordinator,
        )
    except SwarmError as exc:
        console.print(f"[red]Swarm error:[/red] {exc}")
        raise click.Abort()
    console.print(f"[green]Broadcast[/green] to {len(targets)} worker(s) in [cyan]{swarm_id}[/cyan]")


def register(main: click.Group) -> None:
    """Register swarm commands on the main CLI group."""
    main.add_command(swarm_group)
