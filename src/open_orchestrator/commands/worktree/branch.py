"""``owt branch`` — alias for ``owt new --in-place``."""

from __future__ import annotations

import click

from open_orchestrator.commands.worktree.new import new_worktree


@click.command("branch")
@click.argument("description", nargs=-1)
@click.option("-b", "--base", "base_branch", help="Base branch for the new branch.")
@click.option("--ai-tool", default=None, help="AI tool to start by registered name.")
@click.option("--plan-mode", is_flag=True, help="Start Claude in plan mode.")
@click.option("-a", "--attach", is_flag=True, help="Attach to tmux session after creation.")
@click.option("--prefix", help="Override auto-detected branch prefix (e.g., feat, fix).")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def branch_cmd(
    ctx: click.Context,
    description: tuple[str, ...],
    base_branch: str | None,
    ai_tool: str | None,
    plan_mode: bool,
    attach: bool,
    prefix: str | None,
    yes: bool,
) -> None:
    """Create a branch + tmux session + AI agent in the current checkout.

    Like ``owt new`` but creates a branch in the current repository instead
    of a separate git worktree. Faster and uses no extra disk space, but
    only one branch session can run at a time.

    Examples:
        owt branch Add user authentication
        owt branch "Fix login bug" --plan-mode
    """
    ctx.invoke(
        new_worktree,
        description=description,
        base_branch=base_branch,
        explicit_branch=None,
        ai_tool=ai_tool,
        plan_mode=plan_mode,
        template_name=None,
        attach=attach,
        prefix=prefix,
        yes=yes,
        headless=False,
        branch_mode=True,
    )
