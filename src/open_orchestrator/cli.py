"""CLI entry point for Open Orchestrator."""

from __future__ import annotations

import click

from open_orchestrator.commands import (
    agent,
    config_cmd,
    critic_cmd,
    db_cmd,
    doctor,
    dream_cmd,
    maintenance,
    memory_cmd,
    merge_cmds,
    orchestrate_cmds,
    swarm_cmd,
    worktree,
)


@click.group(invoke_without_command=True)
@click.option("--profile", is_flag=True, hidden=True, help="Show import timing breakdown.")
@click.option("--log-format", type=click.Choice(["text", "json"]), default="text", hidden=True, help="Log output format.")
@click.option("--verbose", is_flag=True, hidden=True, help="Enable DEBUG logging.")
@click.option("--json", "json_output", is_flag=True, help="Machine-readable JSON output.")
@click.pass_context
def main(ctx: click.Context, profile: bool, log_format: str, verbose: bool, json_output: bool) -> None:
    """Open Orchestrator — multi-agent worktree orchestration.

    Run 'owt' with no arguments to launch the Switchboard.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output

    if verbose or log_format == "json":
        from open_orchestrator.utils.logging import configure_logging

        configure_logging(verbose=verbose, json_format=log_format == "json")

    if profile:
        import time

        timings: list[tuple[str, float]] = []
        for name in [
            "open_orchestrator.core.tmux_manager",
            "open_orchestrator.core.worktree",
            "open_orchestrator.core.status",
            "open_orchestrator.config",
            "rich.console",
            "rich.table",
        ]:
            t0 = time.perf_counter()
            __import__(name)
            timings.append((name, (time.perf_counter() - t0) * 1000))
        for name, ms in sorted(timings, key=lambda x: -x[1]):
            click.echo(f"  {ms:6.1f}ms  {name}")
        click.echo(f"  {'─' * 30}")
        click.echo(f"  {sum(ms for _, ms in timings):6.1f}ms  total")
        return

    if ctx.invoked_subcommand is None:
        from open_orchestrator.core.switchboard import launch_switchboard

        launch_switchboard()


# Register all command modules
worktree.register(main)
agent.register(main)
merge_cmds.register(main)
orchestrate_cmds.register(main)
maintenance.register(main)
config_cmd.register(main)
db_cmd.register(main)
doctor.register(main)
memory_cmd.register(main)
critic_cmd.register(main)
dream_cmd.register(main)
swarm_cmd.register(main)
