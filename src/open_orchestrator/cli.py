"""CLI entry point for Open Orchestrator."""

import json
import time
from contextlib import nullcontext
from pathlib import Path

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
from open_orchestrator.models.status import AIActivityStatus

console = Console()


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

    # 3. Create tmux session + start AI tool
    tmux_manager = TmuxManager()
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
        session_info = None

    # 4. Initialize status tracking
    session_name = session_info.session_name if session_info else None
    try:
        tracker = StatusTracker()
        tracker.initialize_status(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            branch=worktree.branch,
            tmux_session=session_name,
            ai_tool=ai_tool_enum,
        )
    except Exception:
        pass

    # 5. Send task description as initial prompt
    if task_description and session_name:
        time.sleep(2)
        try:
            tmux_manager.send_keys_to_pane(session_name=session_name, keys=task_description)
            console.print(f"[cyan]Sent task:[/cyan] {task_description[:80]}{'...' if len(task_description) > 80 else ''}")
            tracker = StatusTracker()
            tracker.update_task(worktree.name, task_description[:100])
        except Exception as e:
            console.print(f"[yellow]Could not send prompt: {e}[/yellow]")

    # 6. Send template instructions
    if tmpl_instructions and session_name:
        try:
            tmux_manager.send_keys_to_pane(session_name=session_name, keys=tmpl_instructions)
        except Exception:
            pass

    # 7. Attach if requested
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
@click.argument("identifier")
@click.argument("message", nargs=-1, required=True)
@click.option("--pane", "pane_index", type=int, default=0, help="Target pane index.")
def send_to_worktree(identifier: str, message: tuple[str, ...], pane_index: int) -> None:
    """Send a command/message to a worktree's AI agent.

    Examples:
        owt send auth-jwt "Fix the failing tests"
        owt send my-feature "Let's try a different approach"
    """
    msg = " ".join(message)

    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    tmux = TmuxManager()
    session_name = tmux.generate_session_name(worktree.name)

    if not tmux.session_exists(session_name):
        raise click.ClickException(f"No tmux session for '{worktree.name}'.")

    try:
        tmux.send_keys_to_pane(session_name, msg, pane_index=pane_index)
        console.print(f"[green]Sent to {worktree.name}:[/green] {msg[:80]}")
    except TmuxError as e:
        raise click.ClickException(str(e)) from e

    # Update status
    try:
        tracker = StatusTracker()
        tracker.record_command(worktree.name, msg)
    except Exception:
        pass


# ─── owt merge ──────────────────────────────────────────────────────────────

@main.command("merge")
@click.argument("worktree_name")
@click.option("--base", "base_branch", help="Target branch to merge into.")
@click.option("--keep", is_flag=True, help="Keep the worktree after merging.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation.")
def merge_worktree(worktree_name: str, base_branch: str | None, keep: bool, yes: bool) -> None:
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
            )
    except MergeConflictError as e:
        console.print(f"\n[red]Merge conflicts:[/red] {e}")
        for conflict in e.conflicts:
            console.print(f"  [yellow]C[/yellow] {conflict}")
        console.print(f"\n[dim]Resolve in: {worktree.path}[/dim]")
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
            # Kill tmux session
            tmux = TmuxManager()
            session_name = tmux.generate_session_name(worktree.name)
            try:
                if tmux.session_exists(session_name):
                    tmux.kill_session(session_name)
            except TmuxError:
                pass

            # Clean up status
            try:
                StatusTracker().remove_status(worktree.name)
            except Exception:
                pass

            console.print("  [green]Cleaned up worktree + session[/green]")
    else:
        console.print(f"\n[red]{result.message}[/red]")


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

    # 1. Kill tmux session
    tmux = TmuxManager()
    session_name = tmux.generate_session_name(worktree.name)
    try:
        if tmux.session_exists(session_name):
            tmux.kill_session(session_name)
            console.print(f"[green]Killed tmux session:[/green] {session_name}")
    except TmuxError as e:
        console.print(f"[yellow]tmux warning: {e}[/yellow]")

    # 2. Delete worktree
    try:
        wt_manager.delete(identifier, force=force)
        console.print(f"[green]Deleted worktree:[/green] {worktree.path}")
    except WorktreeError as e:
        raise click.ClickException(str(e)) from e

    # 3. Clean up status
    try:
        StatusTracker().remove_status(worktree.name)
    except Exception:
        pass


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
        report = service.cleanup(worktree_paths, dry_run=not force)

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
    else:
        # Also clean up tmux sessions and status for cleaned worktrees
        tmux = TmuxManager()
        tracker = StatusTracker()
        for path in report.cleaned_paths:
            name = Path(path).name
            session_name = tmux.generate_session_name(name)
            try:
                if tmux.session_exists(session_name):
                    tmux.kill_session(session_name)
            except TmuxError:
                pass
            try:
                tracker.remove_status(name)
            except Exception:
                pass


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
