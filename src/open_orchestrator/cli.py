"""CLI entry point for Open Orchestrator."""
from __future__ import annotations

import json
import time
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from open_orchestrator.core.batch import BatchResult

import click
from rich.console import Console
from rich.table import Table

from open_orchestrator.config import AITool, load_config
from open_orchestrator.core.environment import EnvironmentSetup, EnvironmentSetupError
from open_orchestrator.core.project_detector import ProjectDetector
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.tmux_manager import (
    TmuxError,
    TmuxManager,
    TmuxSessionExistsError,
)
from open_orchestrator.core.worktree import (
    NotAGitRepositoryError,
    WorktreeAlreadyExistsError,
    WorktreeError,
    WorktreeManager,
    WorktreeNotFoundError,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

console = Console()


def _print_batch_status(results: list[BatchResult]) -> None:
    """Print compact batch status counts."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    console.print(f"  [dim]{' | '.join(parts)}[/dim]")


def _print_batch_results(results: list[BatchResult], heading: str = "Batch complete") -> None:
    """Print final batch execution summary."""
    from open_orchestrator.core.batch import BatchStatus

    shipped = sum(1 for r in results if r.status == BatchStatus.SHIPPED)
    completed = sum(1 for r in results if r.status == BatchStatus.COMPLETED)
    failed = sum(1 for r in results if r.status == BatchStatus.FAILED)
    console.print(f"\n[bold]{heading}:[/bold] {shipped} shipped, {completed} done, {failed} failed")
    for r in results:
        icon = {BatchStatus.SHIPPED: "[green]✓[/green]", BatchStatus.COMPLETED: "[cyan]●[/cyan]",
                BatchStatus.FAILED: "[red]✗[/red]"}.get(r.status, "[dim]?[/dim]")
        label = getattr(r.task, "id", None) or r.task.description[:50]
        err = f" — {r.error}" if r.error else ""
        console.print(f"  {icon} {label}{err}")


def get_worktree_manager(repo_path: Path | None = None) -> WorktreeManager:
    """Get a WorktreeManager instance with error handling."""
    try:
        return WorktreeManager(repo_path)
    except NotAGitRepositoryError as e:
        raise click.ClickException(str(e)) from e


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Open Orchestrator — multi-agent worktree orchestration.

    Run 'owt' with no arguments to launch the Switchboard.
    """
    if ctx.invoked_subcommand is None:
        from open_orchestrator.core.switchboard import launch_switchboard

        launch_switchboard()


# ─── owt new ────────────────────────────────────────────────────────────────

@main.command("new")
@click.argument("description", nargs=-1)
@click.option("-b", "--base", "base_branch", help="Base branch for the new worktree.")
@click.option("--branch", "explicit_branch", help="Use this branch name instead of auto-generating.")
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid"]),
    default=None,
    help="AI tool to start (auto-detected if not specified).",
)
@click.option("--plan-mode", is_flag=True, help="Start Claude in plan mode.")
@click.option("-t", "--template", "template_name", help="Apply a worktree template.")
@click.option("-a", "--attach", is_flag=True, help="Attach to tmux session after creation.")
@click.option("--prefix", help="Override auto-detected branch prefix (e.g., feat, fix).")
@click.option("-y", "--yes", is_flag=True, help="Skip branch name confirmation.")
@click.option("--headless", is_flag=True, help="Create worktree without tmux session (CI/script use).")
def new_worktree(
    description: tuple[str, ...],
    base_branch: str | None,
    explicit_branch: str | None,
    ai_tool: str | None,
    plan_mode: bool,
    template_name: str | None,
    attach: bool,
    prefix: str | None,
    yes: bool,
    headless: bool,
) -> None:
    """Create a worktree + tmux session + deps + AI agent. One command.

    Automatically generates a branch name from your task description,
    creates the worktree, installs deps, copies .env, starts the AI tool.

    Examples:
        owt new Add user authentication with JWT
        owt new Fix login redirect bug
        owt new "Refactor database queries" --plan-mode
        owt new --branch feat/my-branch
    """
    from open_orchestrator.core.agent_detector import detect_installed_agents
    from open_orchestrator.core.branch_namer import generate_branch_name

    config = load_config()

    # Get description
    if description:
        task_description = " ".join(description)
    elif explicit_branch:
        task_description = ""
    else:
        task_description = click.prompt("What are you working on?")

    # Determine branch name
    if explicit_branch:
        branch = explicit_branch
    else:
        if not task_description.strip():
            raise click.ClickException("Task description cannot be empty")
        try:
            branch = generate_branch_name(task_description, prefix=prefix)
        except ValueError as e:
            raise click.ClickException(f"Could not generate branch name: {e}") from e

    # Check for git ref conflicts
    from git import Repo

    try:
        repo = Repo(search_parent_directories=True)
        existing_refs = {ref.name for ref in repo.refs}
        branch_parts = branch.split("/")
        for i in range(1, len(branch_parts)):
            partial = "/".join(branch_parts[:i])
            if partial in existing_refs:
                console.print(f"[yellow]Branch '{partial}' exists — cannot create '{branch}' (git ref conflict).[/yellow]")
                branch = click.prompt("Enter a different branch name")
                break
    except Exception:
        pass

    # Confirm branch name
    if not yes and not explicit_branch:
        console.print(f"\n[bold]Task:[/bold]   {task_description}")
        console.print(f"[bold]Branch:[/bold] {branch}")
        if not click.confirm("\nProceed?", default=True):
            branch = click.prompt("Enter branch name", default=branch)

    # Resolve template
    tmpl_instructions: str | None = None
    if template_name:
        from open_orchestrator.config import get_builtin_templates

        tmpl = get_builtin_templates().get(template_name)
        if tmpl:
            tmpl_instructions = tmpl.ai_instructions
            if tmpl.ai_tool:
                ai_tool = tmpl.ai_tool.value
            if tmpl.plan_mode:
                plan_mode = True
            if base_branch is None and tmpl.base_branch:
                base_branch = tmpl.base_branch

    # Auto-detect AI tool
    if ai_tool is None:
        installed = detect_installed_agents()
        if len(installed) == 0:
            raise click.ClickException("No AI coding tools found. Install claude, opencode, or droid.")
        elif len(installed) == 1:
            ai_tool = installed[0].value
        else:
            console.print("\n[bold]Detected AI tools:[/bold]")
            tool_names = [t.value for t in installed]
            for i, tool in enumerate(installed, 1):
                console.print(f"  {i}. {tool.value}")
            choice = click.prompt("Select AI tool", type=click.IntRange(1, len(installed)), default=1)
            ai_tool = tool_names[choice - 1]

    ai_tool_enum = AITool(ai_tool)

    # 1. Create worktree
    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.create(branch=branch, base_branch=base_branch)
    except WorktreeAlreadyExistsError as e:
        raise click.ClickException(str(e)) from e
    except WorktreeError as e:
        raise click.ClickException(str(e)) from e

    console.print(f"[green]Worktree created:[/green] {worktree.path}")

    # 2. Set up environment
    try:
        project_config = ProjectDetector().detect(str(worktree.path))
        if project_config:
            with console.status("[bold blue]Setting up environment..."):
                EnvironmentSetup(project_config).setup_worktree(
                    worktree_path=str(worktree.path),
                    source_path=str(wt_manager.git_root),
                    install_deps=config.environment.auto_install_deps,
                    copy_env=config.environment.copy_env_file,
                )
            console.print("[green]Environment ready[/green]")
    except EnvironmentSetupError as e:
        console.print(f"[yellow]Environment setup warning: {e}[/yellow]")

    # 3. Install AI tool hooks for status reporting
    from open_orchestrator.core.hooks import install_hooks

    hooks_installed = install_hooks(worktree.path, worktree.name, ai_tool_enum)
    if hooks_installed:
        console.print(f"[green]Hooks installed:[/green] {ai_tool_enum.value} → owt status")

    # 4. Create tmux session + start AI tool (skip in headless mode)
    tmux_manager = TmuxManager()
    session_info = None
    session_name = None
    if not headless:
        try:
            session_info = tmux_manager.create_worktree_session(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                ai_tool=ai_tool_enum,
                plan_mode=plan_mode,
            )
            console.print(f"[green]tmux session:[/green] {session_info.session_name}")
        except TmuxSessionExistsError:
            session_name = tmux_manager.generate_session_name(worktree.name)
            console.print(f"[yellow]tmux session already exists:[/yellow] {session_name}")
            session_info = tmux_manager.get_session_for_worktree(worktree.name)
        except TmuxError as e:
            console.print(f"[yellow]tmux warning: {e}[/yellow]")

        session_name = session_info.session_name if session_info else session_name
    else:
        console.print("[dim]Headless mode — no tmux session created[/dim]")

    # 5. Initialize status tracking
    try:
        tracker = StatusTracker()
        tracker.initialize_status(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            branch=worktree.branch,
            tmux_session=session_name,
            ai_tool=ai_tool_enum,
        )
    except Exception as e:
        console.print(f"[yellow]Status tracking init failed: {e}[/yellow]")

    # 6. Send task description as initial prompt
    if task_description and session_name:
        time.sleep(2)
        try:
            tmux_manager.send_keys_to_pane(session_name=session_name, keys=task_description)
            console.print(f"[cyan]Sent task:[/cyan] {task_description[:80]}{'...' if len(task_description) > 80 else ''}")
            tracker = StatusTracker()
            tracker.update_task(worktree.name, task_description[:100])
        except Exception as e:
            console.print(f"[yellow]Could not send prompt: {e}[/yellow]")

    # 7. Send template instructions
    if tmpl_instructions and session_name:
        try:
            tmux_manager.send_keys_to_pane(session_name=session_name, keys=tmpl_instructions)
        except Exception:
            pass

    # 8. Attach if requested
    if attach and session_name:
        if tmux_manager.is_inside_tmux():
            tmux_manager.switch_client(session_name)
        else:
            tmux_manager.attach(session_name)


# ─── owt list ───────────────────────────────────────────────────────────────

@main.command("list")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show all worktrees including main.")
def list_worktrees(show_all: bool) -> None:
    """List all worktrees with status.

    Quick text list (non-interactive, for scripts/pipes).
    """
    wt_manager = get_worktree_manager()
    worktrees = wt_manager.list_all()

    if not show_all:
        worktrees = [wt for wt in worktrees if not wt.is_main]

    if not worktrees:
        console.print("[dim]No worktrees found.[/dim]")
        return

    tracker = StatusTracker()
    tmux = TmuxManager()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Branch")
    table.add_column("Status")
    table.add_column("Task")
    table.add_column("tmux")

    for wt in worktrees:
        status = tracker.get_status(wt.name)
        status_str = ""
        task_str = ""
        tmux_str = ""

        if status:
            act = status.activity_status
            if act == AIActivityStatus.WORKING:
                status_str = "[green]● working[/green]"
            elif act == AIActivityStatus.IDLE:
                status_str = "[dim]○ idle[/dim]"
            elif act == AIActivityStatus.BLOCKED:
                status_str = "[yellow]⚠ blocked[/yellow]"
            elif act == AIActivityStatus.COMPLETED:
                status_str = "[cyan]✓ done[/cyan]"
            else:
                status_str = f"[dim]{act.value}[/dim]"
            task_str = (status.current_task or "")[:40]
            tmux_str = status.tmux_session or ""
        else:
            session = tmux.get_session_for_worktree(wt.name)
            if session:
                tmux_str = session.session_name

        name = "[bold]" + wt.name + "[/bold]" if wt.is_main else wt.name
        table.add_row(name, wt.branch, status_str, task_str, tmux_str)

    console.print(table)


# ─── owt switch ─────────────────────────────────────────────────────────────

@main.command("switch")
@click.argument("identifier")
def switch_worktree(identifier: str) -> None:
    """Jump to a worktree's tmux session.

    If inside tmux, switches the current client.
    If outside, attaches to the session.
    """
    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    tmux = TmuxManager()
    session_name = tmux.generate_session_name(worktree.name)

    if not tmux.session_exists(session_name):
        raise click.ClickException(f"No tmux session found for '{worktree.name}'. Run 'owt new' to create one.")

    if tmux.is_inside_tmux():
        tmux.switch_client(session_name)
    else:
        tmux.attach(session_name)


# ─── owt send ───────────────────────────────────────────────────────────────

@main.command("send")
@click.argument("identifier", required=False)
@click.argument("message", nargs=-1, required=True)
@click.option("--pane", "pane_index", type=int, default=0, help="Target pane index.")
@click.option("--all", "send_all", is_flag=True, help="Send to ALL worktrees.")
@click.option("--working", "send_working", is_flag=True, help="Send only to WORKING worktrees.")
def send_to_worktree(
    identifier: str | None, message: tuple[str, ...], pane_index: int,
    send_all: bool, send_working: bool,
) -> None:
    """Send a command/message to a worktree's AI agent.

    Examples:
        owt send auth-jwt "Fix the failing tests"
        owt send --all "Run tests"
        owt send --working "Wrap up and commit"
    """
    msg = " ".join(message)
    tmux = TmuxManager()
    tracker = StatusTracker()

    if send_all or send_working:
        # Broadcast mode
        statuses = tracker.get_all_statuses()
        if send_working:
            statuses = [s for s in statuses if s.activity_status == AIActivityStatus.WORKING]

        sent = 0
        for s in statuses:
            if s.tmux_session and tmux.session_exists(s.tmux_session):
                try:
                    tmux.send_keys_to_pane(s.tmux_session, msg, pane_index=pane_index)
                    tracker.record_command(s.worktree_name, msg)
                    sent += 1
                except TmuxError:
                    console.print(f"[yellow]Failed to send to {s.worktree_name}[/yellow]")
        console.print(f"[green]Broadcast to {sent} worktree(s):[/green] {msg[:80]}")
        return

    if not identifier:
        raise click.ClickException("Specify a worktree name, or use --all / --working")

    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    session_name = tmux.generate_session_name(worktree.name)

    if not tmux.session_exists(session_name):
        raise click.ClickException(f"No tmux session for '{worktree.name}'.")

    try:
        tmux.send_keys_to_pane(session_name, msg, pane_index=pane_index)
        console.print(f"[green]Sent to {worktree.name}:[/green] {msg[:80]}")
    except TmuxError as e:
        raise click.ClickException(str(e)) from e

    try:
        tracker.record_command(worktree.name, msg)
    except Exception:
        pass


# ─── owt merge ──────────────────────────────────────────────────────────────

@main.command("merge")
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
        console.print(f"\n[yellow]⚠ File overlap warning:[/yellow] {len(overlaps)} file(s) modified by other worktrees:")
        for f_path, wt_names in list(overlaps.items())[:5]:
            console.print(f"  [yellow]{f_path}[/yellow] ← {', '.join(wt_names)}")
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
        console.print(f"\n[red]Merge conflicts:[/red] {e}")
        for conflict in e.conflicts:
            console.print(f"  [yellow]C[/yellow] {conflict}")
        if leave_conflicts:
            console.print(f"\n[bold]{'Rebase' if rebase else 'Merge'} left in-progress.[/bold] Resolve in: {worktree.path}")
            console.print(f"[dim]After resolving: git add <files> && git {'rebase --continue' if rebase else 'commit'}[/dim]")
        else:
            console.print("\n[dim]Re-run with --leave-conflicts to resolve manually.[/dim]")
        raise SystemExit(1)
    except MergeError as e:
        raise click.ClickException(str(e)) from e

    if result.status == MergeStatus.ALREADY_MERGED:
        console.print(f"\n[yellow]{result.message}[/yellow]")
    elif result.status == MergeStatus.SUCCESS:
        console.print(
            f"\n[bold green]Merged![/bold green] {result.source_branch} → {result.target_branch}"
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


# ─── owt ship ──────────────────────────────────────────────────────────────

@main.command("ship")
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
    from git import Repo

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
        console.print(f"  2. Merge {worktree.branch} → {target} ({commits_ahead + (1 if dirty_files else 0)} commit(s))")
        console.print("  3. Delete worktree + tmux session")
        if not click.confirm("\nProceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Step 1: Auto-commit if dirty
    if dirty_files:
        wt_repo = Repo(worktree.path)
        wt_repo.git.add("-A")
        msg = commit_message or f"feat: {worktree.branch.split('/')[-1].replace('-', ' ')}"
        wt_repo.git.commit("-m", msg)
        console.print(f"[green]Committed:[/green] {msg}")

    # Step 1.5: Quality gate (Agno, if available)
    if not yes:
        config = load_config()
        if config.agno.enabled:
            try:
                from open_orchestrator.core.intelligence import AgnoQualityGate

                diff_output = Repo(worktree.path).git.diff(f"{target}...{worktree.branch}", stat=False)
                if diff_output:
                    tracker = StatusTracker()
                    status = tracker.get_status(worktree_name)
                    task_desc = status.current_task if status else None

                    # Gather active worktree context
                    active_wts = [
                        {"name": s.worktree_name, "branch": s.branch, "task": s.current_task or ""}
                        for s in tracker.get_all_statuses()
                        if s.worktree_name != worktree_name
                        and s.activity_status in (AIActivityStatus.WORKING, AIActivityStatus.IDLE)
                    ]

                    with console.status("[bold blue]Running quality gate..."):
                        gate = AgnoQualityGate(config.agno, repo_path=str(wt_manager.git_root))
                        verdict = gate.review(diff_output, task_desc, active_wts)

                    if verdict.passed:
                        console.print(f"[green]Quality gate passed[/green] (score: {verdict.score:.1f}): {verdict.summary}")
                    else:
                        console.print(f"\n[yellow]Quality gate flagged issues[/yellow] (score: {verdict.score:.1f})")
                        console.print(f"  {verdict.summary}")
                        for issue in verdict.issues[:5]:
                            console.print(f"  [yellow]•[/yellow] {issue}")
                        if verdict.cross_worktree_conflicts:
                            console.print("  [yellow]Cross-worktree conflicts:[/yellow]")
                            for conflict in verdict.cross_worktree_conflicts[:3]:
                                console.print(f"    [yellow]⚠[/yellow] {conflict}")
                        if not click.confirm("\nShip anyway?"):
                            console.print("[yellow]Aborted.[/yellow]")
                            return
            except ImportError:
                pass
            except Exception as e:
                console.print(f"[dim]Quality gate skipped: {e}[/dim]")

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
        console.print(f"\n[red]Merge conflicts:[/red] {e}")
        for conflict in e.conflicts:
            console.print(f"  [yellow]C[/yellow] {conflict}")
        if leave_conflicts:
            console.print(f"\n[bold]{'Rebase' if rebase else 'Merge'} left in-progress.[/bold] Resolve in: {worktree.path}")
            console.print(f"[dim]After resolving: git add <files> && git {'rebase --continue' if rebase else 'commit'}[/dim]")
        else:
            console.print("\n[dim]Re-run with --leave-conflicts to resolve manually.[/dim]")
        raise SystemExit(1)
    except MergeError as e:
        raise click.ClickException(str(e)) from e

    if result.status == MergeStatus.SUCCESS:
        console.print(
            f"\n[bold green]Shipped![/bold green] {result.source_branch} → {result.target_branch}"
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


# ─── owt delete ─────────────────────────────────────────────────────────────

@main.command("delete")
@click.argument("identifier")
@click.option("-f", "--force", is_flag=True, help="Force delete even with uncommitted changes.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
def delete_worktree(identifier: str, force: bool, yes: bool) -> None:
    """Delete a worktree + tmux session + status.

    Full teardown: kills the tmux session, removes the git worktree,
    and cleans up status tracking.
    """
    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    if worktree.is_main:
        raise click.ClickException("Cannot delete the main worktree")

    if not yes:
        console.print("\n[bold]About to delete:[/bold]")
        console.print(f"  Branch: {worktree.branch}")
        console.print(f"  Path:   {worktree.path}")
        if not click.confirm("\nProceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    from open_orchestrator.core.pane_actions import teardown_worktree

    errors = teardown_worktree(
        worktree.name,
        repo_path=str(wt_manager.git_root),
        kill_tmux=True,
        delete_git_worktree=True,
        clean_status=True,
        force=force,
    )

    git_errors = [e for e in errors if "git worktree" in e]
    other_errors = [e for e in errors if "git worktree" not in e]

    if git_errors:
        raise click.ClickException(git_errors[0])
    for err in other_errors:
        console.print(f"[yellow]Warning: {err}[/yellow]")
    console.print(f"[green]Deleted worktree:[/green] {worktree.path}")


# ─── owt sync ───────────────────────────────────────────────────────────────

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


# ─── owt cleanup ────────────────────────────────────────────────────────────

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


# ─── owt wait ──────────────────────────────────────────────────────────────

@main.command("wait")
@click.argument("worktree_name")
@click.option("--timeout", type=int, default=600, help="Max wait time in seconds (default 600).")
@click.option("--poll", type=int, default=10, help="Poll interval in seconds.")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def wait_for_worktree(worktree_name: str, timeout: int, poll: int, json_output: bool) -> None:
    """Wait for a worktree's agent to finish (reach WAITING/COMPLETED).

    Polls status until the agent is done or timeout is reached.
    Useful for CI/CD pipelines and scripted workflows.

    Examples:
        owt wait auth-jwt
        owt wait my-feature --timeout 1200
        owt wait my-feature --json
    """
    tracker = StatusTracker()
    elapsed = 0
    status: WorktreeAIStatus | None = None
    terminal_states = {AIActivityStatus.COMPLETED, AIActivityStatus.ERROR}

    while elapsed < timeout:
        tracker.reload()
        status = tracker.get_status(worktree_name)
        if not status:
            raise click.ClickException(f"No status found for '{worktree_name}'")

        if status.activity_status in terminal_states:
            if json_output:
                console.print(json.dumps({
                    "worktree": worktree_name,
                    "status": status.activity_status.value,
                    "elapsed": elapsed,
                    "task": status.current_task,
                }, indent=2))
            else:
                console.print(f"[green]{worktree_name}:[/green] {status.activity_status.value} ({elapsed}s)")
            if status.activity_status == AIActivityStatus.ERROR:
                raise SystemExit(1)
            return

        if not json_output:
            console.print(f"[dim]{worktree_name}: {status.activity_status.value} ({elapsed}s / {timeout}s)[/dim]", end="\r")

        time.sleep(poll)
        elapsed += poll

    last_status = status.activity_status.value if status else "unknown"
    raise click.ClickException(f"Timeout after {timeout}s — {worktree_name} still {last_status}")


# ─── owt plan ──────────────────────────────────────────────────────────────

@main.command("plan")
@click.argument("goal", nargs=-1, required=True)
@click.option("-o", "--output", "output_path", help="Output path for plan TOML (default: plan.toml).")
@click.option("--execute", is_flag=True, help="Execute the plan immediately after generation (batch mode).")
@click.option("--start", is_flag=True, help="Start orchestrator after plan generation (feature branch mode).")
@click.option("--branch", "orch_branch", help="Feature branch name for orchestrator (used with --start).")
@click.option("--edit", is_flag=True, help="Open plan in $EDITOR before executing.")
@click.option("--auto-ship", is_flag=True, help="Auto-ship completed tasks during execution.")
@click.option("--max-concurrent", type=int, default=3, help="Max parallel tasks.")
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid"]),
    default=None,
    help="AI tool to generate the plan (auto-detected if not specified).",
)
def plan_goal(
    goal: tuple[str, ...],
    output_path: str | None,
    execute: bool,
    start: bool,
    orch_branch: str | None,
    edit: bool,
    auto_ship: bool,
    max_concurrent: int,
    ai_tool: str | None,
) -> None:
    """AI-powered task decomposition into a dependency-aware DAG.

    Decomposes a feature goal into parallel tasks with dependency ordering,
    generates a TOML batch file, and optionally executes it.

    Examples:
        owt plan Build JWT auth with refresh tokens
        owt plan "Add rate limiting" --execute
        owt plan "Add auth" --start --branch feat/auth-v2
        owt plan "Fix auth bugs" --execute --auto-ship
    """
    from open_orchestrator.core.batch import (
        load_batch_config,
        plan_tasks,
    )

    goal_text = " ".join(goal)
    wt_manager = get_worktree_manager()

    # Auto-detect AI tool if not specified
    if ai_tool is None:
        from open_orchestrator.core.agent_detector import detect_installed_agents

        installed = detect_installed_agents()
        if not installed:
            raise click.ClickException("No AI coding tools found. Install claude, opencode, or droid.")
        ai_tool = installed[0].value

    # 1. Generate plan
    console.print(f"[bold blue]Planning tasks with {ai_tool}...[/bold blue]")
    try:
        plan_path = plan_tasks(
            goal=goal_text,
            repo_path=str(wt_manager.git_root),
            ai_tool=ai_tool,
            output_path=output_path,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Planning cancelled.[/yellow]")
        return
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e

    # 2. Show task summary
    config = load_batch_config(str(plan_path))
    console.print(f"\n[bold]Plan:[/bold] {goal_text}")
    console.print(f"[bold]Tasks:[/bold] {len(config.tasks)}  [bold]File:[/bold] {plan_path}\n")

    for t in config.tasks:
        deps = f" [dim]← {', '.join(t.depends_on)}[/dim]" if t.depends_on else ""
        console.print(f"  [cyan]{t.id}[/cyan]: {t.description[:70]}{deps}")

    # 3. Optionally open in editor
    if edit:
        import os
        import subprocess

        editor = os.environ.get("EDITOR", "vim")
        subprocess.run([editor, str(plan_path)], check=False)
        # Reload after editing
        config = load_batch_config(str(plan_path))
        console.print(f"\n[green]Reloaded {len(config.tasks)} task(s) after edit[/green]")

    # 4. Optionally start orchestrator (--start)
    if start:
        from open_orchestrator.core.branch_namer import generate_branch_name
        from open_orchestrator.core.orchestrator import (
            Orchestrator,
        )
        from open_orchestrator.core.orchestrator import (
            OrchestratorState as _OrchestratorState,
        )

        feature_branch = orch_branch
        if not feature_branch:
            try:
                feature_branch = f"orchestrator/{generate_branch_name(goal_text, prefix='').lstrip('/')}"
            except ValueError:
                feature_branch = "orchestrator/plan"

        console.print("\n[bold]Starting orchestrator[/bold]")
        console.print(f"  Feature branch: {feature_branch}")
        console.print(f"  Max concurrent: {max_concurrent}")

        orch = Orchestrator.from_plan(
            plan_path=plan_path,
            goal=goal_text,
            feature_branch=feature_branch,
            repo_path=str(wt_manager.git_root),
            max_concurrent=max_concurrent,
        )

        def _orch_status(state: _OrchestratorState) -> None:
            counts: dict[str, int] = {}
            for t in state.tasks:
                counts[t.status] = counts.get(t.status, 0) + 1
            parts = [f"{v} {k}" for k, v in sorted(counts.items())]
            console.print(f"  [dim]{' | '.join(parts)}[/dim]")

        try:
            final = orch.run(on_status=_orch_status)
            shipped = sum(1 for t in final.tasks if t.status == "shipped")
            failed = sum(1 for t in final.tasks if t.status == "failed")
            console.print(
                f"\n[bold green]Orchestration complete![/bold green] "
                f"{shipped} shipped, {failed} failed → {feature_branch}"
            )
            if shipped > 0:
                console.print(f"[dim]Ready for review. Open PR: {feature_branch} → main[/dim]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Orchestrator paused. Resume with: owt orchestrate --resume[/yellow]")
        return

    # 4b. Optionally execute (batch mode)
    if execute:
        import subprocess

        batch_cmd = ["owt", "batch", str(plan_path)]
        if auto_ship:
            batch_cmd.append("--auto-ship")
        batch_cmd.extend(["--max-concurrent", str(max_concurrent)])

        # Run batch in a background tmux session so the terminal isn't blocked
        batch_session = "owt-batch"
        subprocess.run(
            ["tmux", "kill-session", "-t", batch_session],
            capture_output=True, check=False,
        )
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", batch_session, *batch_cmd],
            check=False,
        )
        console.print(f"\n[green]Batch launched in tmux session '{batch_session}'[/green]")
        console.print("[dim]Use 'owt' for switchboard, or: tmux attach -t owt-batch[/dim]")
    else:
        console.print(
            f"\n[dim]Plan saved to {plan_path}. Use --start to orchestrate,"
            f" --execute for batch, or: owt batch {plan_path}[/dim]"
        )


# ─── owt batch ─────────────────────────────────────────────────────────────

@main.command("batch")
@click.argument("tasks_file", type=click.Path(exists=True))
@click.option("--auto-ship", is_flag=True, help="Auto-ship completed tasks.")
@click.option("--max-concurrent", type=int, default=3, help="Max parallel tasks.")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def batch_run(tasks_file: str, auto_ship: bool, max_concurrent: int, json_output: bool) -> None:
    """Run a batch of tasks from a TOML file.

    Karpathy-style autopilot: creates worktrees, starts agents,
    monitors progress, and optionally auto-ships completed work.

    TOML format:
        [batch]
        max_concurrent = 3

        [[tasks]]
        description = "Add user authentication"

        [[tasks]]
        description = "Fix login redirect bug"

    Examples:
        owt batch tasks.toml
        owt batch tasks.toml --auto-ship
    """
    from open_orchestrator.core.batch import BatchRunner, load_batch_config

    config = load_batch_config(tasks_file)
    if auto_ship:
        config.auto_ship = True
    if max_concurrent:
        config.max_concurrent = max_concurrent

    console.print(f"[bold]Batch: {len(config.tasks)} task(s), max {config.max_concurrent} concurrent[/bold]")

    wt_manager = get_worktree_manager()
    runner = BatchRunner(config, str(wt_manager.git_root))

    status_cb = None if json_output else _print_batch_status

    try:
        results = runner.run(on_status=status_cb)
    except KeyboardInterrupt:
        console.print("\n[yellow]Batch interrupted. Worktrees left running.[/yellow]")
        return

    if json_output:
        output = [
            {"task": r.task.description, "worktree": r.worktree_name,
             "status": r.status.value, "error": r.error}
            for r in results
        ]
        console.print(json.dumps(output, indent=2))
    else:
        _print_batch_results(results)


# ─── owt orchestrate ───────────────────────────────────────────────────

@main.command("orchestrate")
@click.argument("plan_file", type=click.Path(exists=True), required=False)
@click.option("--branch", "feature_branch", help="Feature branch name (required for new orchestration).")
@click.option("--resume", is_flag=True, help="Resume from saved state.")
@click.option("--stop", "stop_orch", is_flag=True, help="Graceful stop (worktrees kept).")
@click.option("--status", "show_status", is_flag=True, help="Show orchestrator progress.")
@click.option("--max-concurrent", type=int, default=3, help="Max parallel tasks.")
def orchestrate(
    plan_file: str | None,
    feature_branch: str | None,
    resume: bool,
    stop_orch: bool,
    show_status: bool,
    max_concurrent: int,
) -> None:
    """Drive a plan end-to-end with the orchestrator agent.

    Creates worktrees, coordinates agents, merges completed tasks into
    a feature branch for review, and persists state for stop/resume.

    Examples:
        owt orchestrate plan.toml --branch feat/auth-v2
        owt orchestrate --resume
        owt orchestrate --stop
        owt orchestrate --status
    """
    from open_orchestrator.core.orchestrator import Orchestrator, OrchestratorState

    wt_manager = get_worktree_manager()
    repo_path = str(wt_manager.git_root)

    if show_status:
        state_path = Orchestrator._state_path(repo_path)
        if not state_path.exists():
            console.print("[dim]No orchestrator state found.[/dim]")
            return
        state = OrchestratorState.model_validate_json(state_path.read_text())
        table = Table(title=f"Orchestrator: {state.goal}", show_header=True, header_style="bold")
        table.add_column("Task")
        table.add_column("Status")
        table.add_column("Worktree")
        table.add_column("Branch")
        for t in state.tasks:
            icon = {"pending": "[dim]○[/dim]", "running": "[green]●[/green]",
                    "completed": "[cyan]✓[/cyan]", "shipped": "[bold green]✓[/bold green]",
                    "failed": "[red]✗[/red]"}.get(t.status, "?")
            table.add_row(t.id, f"{icon} {t.status}", t.worktree_name or "", t.branch or "")
        console.print(table)
        console.print(f"\n[dim]Feature branch: {state.feature_branch} | Updated: {state.updated_at}[/dim]")
        return

    if stop_orch:
        try:
            orch = Orchestrator.resume(repo_path)
            orch.stop()
            console.print("[green]Orchestrator stopped.[/green] Worktrees kept. Resume with: owt orchestrate --resume")
        except FileNotFoundError:
            console.print("[yellow]No running orchestrator found.[/yellow]")
        return

    if resume:
        try:
            orch = Orchestrator.resume(repo_path)
        except FileNotFoundError:
            raise click.ClickException("No orchestrator state found. Start with: owt orchestrate <plan.toml> --branch <name>")
        console.print(f"[bold]Resuming orchestrator[/bold]: {orch.state.goal}")
        console.print(f"  Feature branch: {orch.state.feature_branch}")
    elif plan_file:
        if not feature_branch:
            from open_orchestrator.core.batch import load_batch_config
            from open_orchestrator.core.branch_namer import generate_branch_name

            config = load_batch_config(plan_file)
            goal = config.tasks[0].description if config.tasks else "plan"
            try:
                feature_branch = f"orchestrator/{generate_branch_name(goal, prefix='').lstrip('/')}"
            except ValueError:
                feature_branch = "orchestrator/plan"

        goal_text = feature_branch.split("/")[-1].replace("-", " ")
        orch = Orchestrator.from_plan(
            plan_path=plan_file,
            goal=goal_text,
            feature_branch=feature_branch,
            repo_path=repo_path,
            max_concurrent=max_concurrent,
        )
        console.print("[bold]Starting orchestrator[/bold]")
        console.print(f"  Plan: {plan_file}")
        console.print(f"  Feature branch: {feature_branch}")
    else:
        raise click.ClickException("Provide a plan file, or use --resume / --status / --stop")

    def _orch_status(state: OrchestratorState) -> None:
        counts: dict[str, int] = {}
        for t in state.tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        console.print(f"  [dim]{' | '.join(parts)}[/dim]")

    try:
        final = orch.run(on_status=_orch_status)
        shipped = sum(1 for t in final.tasks if t.status == "shipped")
        failed = sum(1 for t in final.tasks if t.status == "failed")
        console.print(
            f"\n[bold green]Orchestration complete![/bold green] "
            f"{shipped} shipped, {failed} failed → {final.feature_branch}"
        )
        if shipped > 0:
            console.print(f"[dim]Ready for review. Open PR: {final.feature_branch} → main[/dim]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Orchestrator paused. Resume with: owt orchestrate --resume[/yellow]")


# ─── owt queue ─────────────────────────────────────────────────────────────

@main.command("queue")
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
        overlap_str = f"[yellow]⚠ {overlaps}[/yellow]" if overlaps else "[green]0[/green]"
        table.add_row(str(i), name, str(commits), overlap_str)

    console.print(table)

    if auto_ship:
        if not yes:
            if not click.confirm(f"\nShip {len(order)} worktree(s) in this order?"):
                console.print("[yellow]Aborted.[/yellow]")
                return

        from open_orchestrator.core.merge import MergeConflictError, MergeStatus

        for name, _, _ in order:
            console.print(f"\n[bold]Shipping {name}...[/bold]")
            try:
                result = merge_manager.merge(
                    worktree_name=name,
                    base_branch=base_branch,
                    delete_worktree=True,
                )
                if result.status == MergeStatus.SUCCESS:
                    # Clean up tmux + status
                    tmux = TmuxManager()
                    session_name = tmux.generate_session_name(name)
                    try:
                        if tmux.session_exists(session_name):
                            tmux.kill_session(session_name)
                    except TmuxError:
                        pass
                    StatusTracker().remove_status(name)
                    console.print(f"  [green]✓ Shipped {name}[/green]")
                else:
                    console.print(f"  [yellow]{result.message}[/yellow]")
            except MergeConflictError as e:
                console.print(f"  [red]✗ Conflicts in {name}: {e}[/red]")
                console.print("  [dim]Skipping remaining — resolve conflicts first.[/dim]")
                break
            except Exception as e:
                console.print(f"  [red]✗ Error: {e}[/red]")
                break


# ─── owt note ──────────────────────────────────────────────────────────────

@main.command("note")
@click.argument("message", nargs=-1, required=True)
@click.option("--clear", is_flag=True, help="Clear all shared notes.")
def shared_note(message: tuple[str, ...], clear: bool) -> None:
    """Share context across all active agent sessions.

    Notes are injected into each worktree's CLAUDE.md so agents
    stay aware of cross-cutting changes.

    Examples:
        owt note "The users table now has a verified_at column"
        owt note "API endpoint changed from /api/v1 to /api/v2"
        owt note --clear
    """
    from open_orchestrator.core.environment import inject_shared_notes

    tracker = StatusTracker()
    tracker.reload()

    if clear:
        tracker.clear_shared_notes()
        console.print("[green]Shared notes cleared.[/green]")
        return

    note_text = " ".join(message)
    tracker.add_shared_note(note_text)

    # Inject into all active worktrees' CLAUDE.md
    notes = tracker.get_shared_notes()
    injected = 0
    for s in tracker.get_all_statuses():
        try:
            inject_shared_notes(s.worktree_path, notes)
            injected += 1
        except Exception:
            pass

    console.print(f"[green]Note shared with {injected} worktree(s):[/green] {note_text[:80]}")


# ─── owt hook (internal) ───────────────────────────────────────────────────

@main.command("hook", hidden=True)
@click.option("--event", required=True, type=click.Choice(["working", "waiting", "blocked"]))
@click.option("--worktree", required=True)
def hook_event(event: str, worktree: str) -> None:
    """Handle AI tool hook events (internal use by installed hooks).

    This command is called by hooks installed in .claude/settings.local.json
    or .factory/settings.json. It updates the worktree's status in the
    shared status store so the switchboard reflects real-time state.
    """
    from datetime import datetime

    status_map = {
        "working": AIActivityStatus.WORKING,
        "waiting": AIActivityStatus.WAITING,
        "blocked": AIActivityStatus.BLOCKED,
    }

    try:
        tracker = StatusTracker()
        wt_status = tracker.get_status(worktree)
        if wt_status:
            wt_status.activity_status = status_map[event]
            wt_status.updated_at = datetime.now()
            tracker.set_status(wt_status)
    except Exception:
        pass  # Hooks must never block the AI tool


# ─── owt version ────────────────────────────────────────────────────────────

@main.command("version")
def version_cmd() -> None:
    """Show version."""
    try:
        from importlib.metadata import version

        ver = version("open-orchestrator")
    except Exception:
        ver = "dev"
    console.print(f"open-orchestrator {ver}")
