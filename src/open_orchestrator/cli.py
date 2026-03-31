"""CLI entry point for Open Orchestrator."""

from __future__ import annotations

import click

from open_orchestrator.commands import (
    agent,
    config_cmd,
    doctor,
    maintenance,
    merge_cmds,
    orchestrate_cmds,
    worktree,
)


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Open Orchestrator — multi-agent worktree orchestration.

    Run 'owt' with no arguments to launch the Switchboard.
    """
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
doctor.register(main)
