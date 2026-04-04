"""Merge lifecycle commands: merge, ship, queue."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from open_orchestrator.commands._shared import console, get_status_tracker, get_worktree_manager
from open_orchestrator.config import load_config
from open_orchestrator.core.worktree import WorktreeNotFoundError
from open_orchestrator.models.status import AIActivityStatus


def _print_merge_conflicts(e: Exception, *, worktree_path: object, rebase: bool, leave_conflicts: bool) -> None:
    """Print merge conflict details and resolution hints."""
    console.print(f"\n[red]Merge conflicts:[/red] {e}")
    for conflict in e.conflicts:  # type: ignore[attr-defined]
        console.print(f"  [yellow]C[/yellow] {conflict}")
    if leave_conflicts:
        console.print(f"\n[bold]{'Rebase' if rebase else 'Merge'} left in-progress.[/bold] Resolve in: {worktree_path}")
        console.print(f"[dim]After resolving: git add <files> && git {'rebase --continue' if rebase else 'commit'}[/dim]")
    else:
        console.print("\n[dim]Re-run with --leave-conflicts to resolve manually.[/dim]")


def _auto_commit_dirty_files(worktree_path: str | Path, branch: str, commit_message: str | None) -> str:
    """Commit uncommitted changes in a worktree. Returns the commit message used."""
    from git import Repo

    wt_repo = Repo(worktree_path)
    wt_repo.git.add("-A")
    msg = commit_message or f"feat: {branch.split('/')[-1].replace('-', ' ')}"
    wt_repo.git.commit("-m", msg)
    console.print(f"[green]Committed:[/green] {msg}")
    return msg


def _run_quality_gate(worktree_name: str, worktree_path: str | Path, branch: str, target: str) -> bool:
    """Run Agno quality gate if enabled. Returns True to continue, False to abort."""
    from git import Repo

    config = load_config()
    if not config.agno.enabled:
        return True

    try:
        from open_orchestrator.core.intelligence import AgnoQualityGate

        diff_output = Repo(worktree_path).git.diff(f"{target}...{branch}", stat=False)
        if not diff_output:
            return True

        wt_manager = get_worktree_manager()
        tracker = get_status_tracker(wt_manager.git_root)
        status = tracker.get_status(worktree_name)
        task_desc = status.current_task if status else None

        active_wts = [
            {"name": s.worktree_name, "branch": s.branch, "task": s.current_task or ""}
            for s in tracker.get_all_statuses()
            if s.worktree_name != worktree_name and s.activity_status in (AIActivityStatus.WORKING, AIActivityStatus.IDLE)
        ]

        with console.status("[bold blue]Running quality gate..."):
            gate = AgnoQualityGate(config.agno, repo_path=str(wt_manager.git_root))
            verdict = gate.review(diff_output, task_desc, active_wts)

        if verdict.passed:
            console.print(f"[green]Quality gate passed[/green] (score: {verdict.score:.1f}): {verdict.summary}")
            return True

        console.print(f"\n[yellow]Quality gate flagged issues[/yellow] (score: {verdict.score:.1f})")
        console.print(f"  {verdict.summary}")
        for issue in verdict.issues[:5]:
            console.print(f"  [yellow]\u2022[/yellow] {issue}")
        if verdict.cross_worktree_conflicts:
            console.print("  [yellow]Cross-worktree conflicts:[/yellow]")
            for conflict in verdict.cross_worktree_conflicts[:3]:
                console.print(f"    [yellow]\u26a0[/yellow] {conflict}")
        return click.confirm("\nShip anyway?")
    except ImportError:
        return True
    except Exception as e:
        console.print(f"[dim]Quality gate skipped: {e}[/dim]")
        return True


# ---------------------------------------------------------------------------
# Commands (top-level to reduce C901 nesting complexity)
# ---------------------------------------------------------------------------


@click.command("merge")
@click.argument("worktree_name")
@click.option("--base", "base_branch", help="Target branch to merge into.")
@click.option("--keep", is_flag=True, help="Keep the worktree after merging.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@click.option("--leave-conflicts", is_flag=True, help="Leave merge in-progress for manual resolution.")
@click.option("--strategy", type=click.Choice(["ours", "theirs"]), help="Conflict resolution strategy (-X).")
@click.option("--rebase", is_flag=True, help="Rebase onto base before merging (linear history).")
def merge_worktree(
    worktree_name: str,
    base_branch: str | None,
    keep: bool,
    yes: bool,
    leave_conflicts: bool,
    strategy: str | None,
    rebase: bool,
) -> None:
    """Merge a worktree branch into its base and clean up.

    Two-phase merge with conflict detection. After success,
    deletes the worktree + tmux session unless --keep is set.
    """
    from open_orchestrator.core.merge import MergeConflictError, MergeError, MergeManager, MergeStatus

    try:
        merge_manager = MergeManager()
    except Exception as e:
        raise click.ClickException(str(e)) from e

    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(worktree_name)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    target = base_branch
    if not target:
        try:
            target = merge_manager.get_base_branch(worktree.branch)
        except MergeError as e:
            raise click.ClickException(str(e)) from e

    commits_ahead = merge_manager.count_commits_ahead(worktree.branch, target)

    # Conflict Guard: warn about file overlaps
    overlaps = merge_manager.check_file_overlaps(worktree_name, target)
    if overlaps:
        console.print(f"\n[yellow]\u26a0 File overlap warning:[/yellow] {len(overlaps)} file(s) modified by other worktrees:")
        for f_path, wt_names in list(overlaps.items())[:5]:
            console.print(f"  [yellow]{f_path}[/yellow] \u2190 {', '.join(wt_names)}")
        if len(overlaps) > 5:
            console.print(f"  ... and {len(overlaps) - 5} more")

    if not yes:
        console.print("\n[bold]Merge plan:[/bold]")
        console.print(f"  Source: {worktree.branch} ({commits_ahead} commit(s) ahead)")
        console.print(f"  Target: {target}")
        console.print(f"  Cleanup: {'keep worktree' if keep else 'delete worktree + session'}")
        if not click.confirm("\nProceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    try:
        with console.status("[bold blue]Merging..."):
            result = merge_manager.merge(
                worktree_name=worktree_name,
                base_branch=base_branch,
                delete_worktree=not keep,
                leave_conflicts=leave_conflicts,
                strategy=strategy,
                rebase=rebase,
            )
    except MergeConflictError as e:
        _print_merge_conflicts(e, worktree_path=worktree.path, rebase=rebase, leave_conflicts=leave_conflicts)
        raise SystemExit(1)
    except MergeError as e:
        raise click.ClickException(str(e)) from e

    if result.status == MergeStatus.ALREADY_MERGED:
        console.print(f"\n[yellow]{result.message}[/yellow]")
    elif result.status == MergeStatus.SUCCESS:
        console.print(
            f"\n[bold green]Merged![/bold green] {result.source_branch} \u2192 {result.target_branch}"
            f" ({result.commits_merged} commits)"
        )

        if result.worktree_cleaned and not keep:
            from open_orchestrator.core.pane_actions import teardown_worktree

            # Worktree already deleted by merge_manager; only kill tmux + clean status
            teardown_worktree(
                worktree.name,
                kill_tmux=True,
                delete_git_worktree=False,
                clean_status=True,
            )
            console.print("  [green]Cleaned up worktree + session[/green]")
    else:
        console.print(f"\n[red]{result.message}[/red]")


@click.command("ship")
@click.argument("worktree_name")
@click.option("--base", "base_branch", help="Target branch to merge into.")
@click.option("-m", "--message", "commit_message", help="Commit message for uncommitted changes.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
@click.option("--leave-conflicts", is_flag=True, help="Leave merge in-progress for manual resolution.")
@click.option("--strategy", type=click.Choice(["ours", "theirs"]), help="Conflict resolution strategy (-X).")
@click.option("--rebase", is_flag=True, help="Rebase onto base before merging (linear history).")
def ship_worktree(
    worktree_name: str,
    base_branch: str | None,
    commit_message: str | None,
    yes: bool,
    leave_conflicts: bool,
    strategy: str | None,
    rebase: bool,
) -> None:
    """Commit, merge, and clean up a worktree in one shot.

    Auto-commits any uncommitted changes, merges the branch into
    its base (main/master), then tears down the worktree + tmux
    session + status tracking.

    Examples:
        owt ship auth-jwt
        owt ship my-feature --base develop
        owt ship my-feature -m "feat: add auth flow"
    """
    from open_orchestrator.core.merge import MergeConflictError, MergeError, MergeManager, MergeStatus

    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(worktree_name)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    if worktree.is_main:
        raise click.ClickException("Cannot ship the main worktree")

    try:
        merge_manager = MergeManager()
    except Exception as e:
        raise click.ClickException(str(e)) from e

    target = base_branch
    if not target:
        try:
            target = merge_manager.get_base_branch(worktree.branch)
        except MergeError as e:
            raise click.ClickException(str(e)) from e

    # Check for uncommitted changes
    dirty_files = merge_manager.check_uncommitted_changes(worktree_name)

    if not yes:
        console.print("\n[bold]Ship plan:[/bold]")
        if dirty_files:
            console.print(f"  1. Commit {len(dirty_files)} uncommitted file(s)")
        else:
            console.print("  1. [dim]No uncommitted changes[/dim]")
        commits_ahead = merge_manager.count_commits_ahead(worktree.branch, target)
        console.print(f"  2. Merge {worktree.branch} \u2192 {target} ({commits_ahead + (1 if dirty_files else 0)} commit(s))")
        console.print("  3. Delete worktree + tmux session")
        if not click.confirm("\nProceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Step 1: Auto-commit if dirty
    if dirty_files:
        _auto_commit_dirty_files(worktree.path, worktree.branch, commit_message)

    # Step 1.5: Quality gate (Agno, if available)
    if not yes and not _run_quality_gate(worktree_name, worktree.path, worktree.branch, target):
        console.print("[yellow]Aborted.[/yellow]")
        return

    # Step 2: Kill tmux session BEFORE merge (avoids issues with agent holding locks)
    from open_orchestrator.core.pane_actions import teardown_worktree

    tmux_errs = teardown_worktree(
        worktree.name,
        kill_tmux=True,
        delete_git_worktree=False,
        clean_status=False,
    )
    if not tmux_errs:
        console.print(f"[green]Killed tmux session:[/green] owt-{worktree.name}")
    else:
        for err in tmux_errs:
            console.print(f"[yellow]tmux warning: {err}[/yellow]")

    # Step 3: Merge
    try:
        with console.status("[bold blue]Merging..."):
            result = merge_manager.merge(
                worktree_name=worktree_name,
                base_branch=target,
                delete_worktree=True,
                leave_conflicts=leave_conflicts,
                strategy=strategy,
                rebase=rebase,
            )
    except MergeConflictError as e:
        _print_merge_conflicts(e, worktree_path=worktree.path, rebase=rebase, leave_conflicts=leave_conflicts)
        raise SystemExit(1)
    except MergeError as e:
        raise click.ClickException(str(e)) from e

    if result.status == MergeStatus.SUCCESS:
        console.print(
            f"\n[bold green]Shipped![/bold green] {result.source_branch} \u2192 {result.target_branch}"
            f" ({result.commits_merged} commits)"
        )
    elif result.status == MergeStatus.ALREADY_MERGED:
        console.print(f"\n[yellow]{result.message}[/yellow]")

    # Step 4: Clean up git worktree (if not already removed by merge) + status
    teardown_worktree(
        worktree.name,
        repo_path=str(wt_manager.git_root),
        kill_tmux=False,  # already killed in Step 2
        delete_git_worktree=result.status == MergeStatus.ALREADY_MERGED,
        clean_status=True,
    )

    console.print("  [green]Cleaned up worktree + session + status[/green]")


@click.command("queue")
@click.option("--base", "base_branch", help="Target branch for merge.")
@click.option("--ship", "auto_ship", is_flag=True, help="Ship all completed worktrees in optimal order.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
def merge_queue(base_branch: str | None, auto_ship: bool, yes: bool) -> None:
    """Show optimal merge order for completed worktrees.

    Analyzes commit counts and file overlaps to determine the best
    merge sequence — smallest changes first to minimize conflicts.

    Examples:
        owt queue                 # Show merge order
        owt queue --ship          # Ship all in order
        owt queue --ship --yes    # Ship all without confirmation
    """
    from open_orchestrator.core.merge import MergeManager

    try:
        merge_manager = MergeManager()
    except Exception as e:
        raise click.ClickException(str(e)) from e

    order = merge_manager.plan_merge_order(base_branch)

    if not order:
        console.print("[dim]No completed/waiting worktrees ready to merge.[/dim]")
        return

    table = Table(title="Merge Queue", show_header=True, header_style="bold")
    table.add_column("#", width=3)
    table.add_column("Worktree")
    table.add_column("Commits", justify="right")
    table.add_column("Overlaps", justify="right")

    for i, (name, commits, overlaps) in enumerate(order, 1):
        overlap_str = f"[yellow]\u26a0 {overlaps}[/yellow]" if overlaps else "[green]0[/green]"
        table.add_row(str(i), name, str(commits), overlap_str)

    console.print(table)

    if auto_ship:
        if not yes and not click.confirm(f"\nShip {len(order)} worktree(s) in this order?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

        from open_orchestrator.core.merge import MergeConflictError, MergeStatus
        from open_orchestrator.core.pane_actions import teardown_worktree

        for name, _, _ in order:
            console.print(f"\n[bold]Shipping {name}...[/bold]")
            try:
                result = merge_manager.merge(
                    worktree_name=name,
                    base_branch=base_branch,
                    delete_worktree=True,
                )
                if result.status == MergeStatus.SUCCESS:
                    teardown_worktree(
                        name,
                        kill_tmux=True,
                        delete_git_worktree=False,
                        clean_status=True,
                    )
                    console.print(f"  [green]\u2713 Shipped {name}[/green]")
                else:
                    console.print(f"  [yellow]{result.message}[/yellow]")
            except MergeConflictError as e:
                console.print(f"  [red]\u2717 Conflicts in {name}: {e}[/red]")
                console.print("  [dim]Skipping remaining \u2014 resolve conflicts first.[/dim]")
                break
            except Exception as e:
                console.print(f"  [red]\u2717 Error: {e}[/red]")
                break


def register(main: click.Group) -> None:
    """Register merge commands on the main CLI group."""
    main.add_command(merge_worktree)
    main.add_command(ship_worktree)
    main.add_command(merge_queue)
