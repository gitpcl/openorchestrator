"""``owt delete`` — full teardown of a worktree or branch session."""

from __future__ import annotations

import click

from open_orchestrator.commands import worktree as _pkg
from open_orchestrator.commands._shared import console, resolve_session_target


@click.command("delete")
@click.argument("identifier")
@click.option("-f", "--force", is_flag=True, help="Force delete even with uncommitted changes.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
def delete_worktree(identifier: str, force: bool, yes: bool) -> None:
    """Delete a worktree (or branch session) + backend session + status.

    Full teardown: kills the backend session, removes the git worktree
    (or deletes the branch + pops auto-stash for branch-mode sessions),
    and cleans up status tracking.
    """
    wt_manager = _pkg.get_worktree_manager()
    tracker = _pkg.get_status_tracker(wt_manager.git_root)
    resolved = resolve_session_target(identifier, wt_manager, tracker)

    if resolved.worktree is not None and resolved.worktree.is_main:
        raise click.ClickException("Cannot delete the main worktree")

    if not yes:
        console.print("\n[bold]About to delete:[/bold]")
        if resolved.worktree is not None:
            console.print(f"  Branch: {resolved.worktree.branch}")
            console.print(f"  Path:   {resolved.worktree.path}")
        else:
            # Branch-mode session — no worktree on disk.
            console.print(f"  Branch: {resolved.status.branch if resolved.status else resolved.name}")
            console.print("  Path:   [dim](in-place branch — no separate directory)[/dim]")
        if not click.confirm("\nProceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    from open_orchestrator.core.pane_actions import teardown_worktree

    if resolved.is_branch:
        errors = teardown_worktree(
            resolved.name,
            repo_path=str(wt_manager.git_root),
            kill_tmux=True,
            delete_git_worktree=False,
            clean_status=True,
            delete_branch=True,
            pop_stash=True,
            force=force,
        )
    else:
        errors = teardown_worktree(
            resolved.name,
            repo_path=str(wt_manager.git_root),
            kill_tmux=True,
            delete_git_worktree=True,
            clean_status=True,
            force=force,
        )

    git_errors = [e for e in errors if "git worktree" in e or "clean up branch" in e]
    other_errors = [e for e in errors if e not in git_errors]

    if git_errors:
        raise click.ClickException(git_errors[0])
    for err in other_errors:
        console.print(f"[yellow]Warning: {err}[/yellow]")
    if resolved.worktree is not None:
        console.print(f"[green]Deleted worktree:[/green] {resolved.worktree.path}")
    else:
        console.print(f"[green]Deleted branch session:[/green] {resolved.name}")
