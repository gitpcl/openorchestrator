"""CLI entry point for Claude Orchestrator."""

import subprocess
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from claude_orchestrator.core.worktree import (
    NotAGitRepositoryError,
    WorktreeAlreadyExistsError,
    WorktreeError,
    WorktreeManager,
    WorktreeNotFoundError,
)
from claude_orchestrator.core.status import StatusTracker
from claude_orchestrator.models.status import ClaudeActivityStatus
from claude_orchestrator.core.tmux_manager import (
    TmuxError,
    TmuxLayout,
    TmuxManager,
    TmuxSessionExistsError,
)
from claude_orchestrator.core.tmux_cli import tmux_group
from claude_orchestrator.core.project_detector import ProjectDetector
from claude_orchestrator.core.environment import (
    EnvironmentSetup,
    EnvironmentSetupError,
)

console = Console()


def get_worktree_manager(repo_path: Optional[Path] = None) -> WorktreeManager:
    """
    Get a WorktreeManager instance with error handling.

    Args:
        repo_path: Optional path to the repository.

    Returns:
        WorktreeManager instance.

    Raises:
        click.ClickException: If not in a git repository.
    """
    try:
        return WorktreeManager(repo_path)
    except NotAGitRepositoryError as e:
        raise click.ClickException(str(e)) from e


@click.group()
@click.version_option(package_name="claude-orchestrator")
def main() -> None:
    """Claude Orchestrator - Git Worktree + Claude Code orchestration tool.

    Manage parallel development workflows with git worktrees and tmux sessions.
    """


# Register tmux command group
main.add_command(tmux_group)


@main.command("create")
@click.argument("branch")
@click.option(
    "-b",
    "--base",
    "base_branch",
    help="Base branch for creating new branches.",
)
@click.option(
    "-p",
    "--path",
    type=click.Path(path_type=Path),
    help="Custom path for the worktree.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force creation even if branch exists elsewhere.",
)
@click.option(
    "--tmux/--no-tmux",
    default=True,
    help="Create a tmux session for the worktree (default: enabled).",
)
@click.option(
    "--claude/--no-claude",
    default=True,
    help="Auto-start Claude Code in the tmux session (default: enabled).",
)
@click.option(
    "-l",
    "--layout",
    type=click.Choice(["main-vertical", "three-pane", "quad", "even-horizontal", "even-vertical"]),
    default="main-vertical",
    help="tmux pane layout for the session.",
)
@click.option(
    "--panes",
    type=int,
    default=2,
    help="Number of panes for the tmux session.",
)
@click.option(
    "-a",
    "--attach",
    is_flag=True,
    help="Attach to tmux session after creation.",
)
@click.option(
    "--deps/--no-deps",
    default=True,
    help="Install dependencies in the new worktree (default: enabled).",
)
@click.option(
    "--env/--no-env",
    default=True,
    help="Copy .env file from main repo (default: enabled).",
)
def create_worktree(
    branch: str,
    base_branch: Optional[str],
    path: Optional[Path],
    force: bool,
    tmux: bool,
    claude: bool,
    layout: str,
    panes: int,
    attach: bool,
    deps: bool,
    env: bool,
) -> None:
    """Create a new worktree for BRANCH with tmux session.

    If BRANCH doesn't exist, it will be created from the base branch
    (or current branch if not specified).

    By default, creates a tmux session, installs dependencies, copies .env,
    and starts Claude Code.

    Example:
        cwt create feature/new-feature
        cwt create bugfix/fix-123 --base main
        cwt create feature/test --no-tmux
        cwt create feature/dev --layout three-pane --attach
        cwt create feature/quick --no-deps --no-env
    """
    wt_manager = get_worktree_manager()
    tmux_manager = TmuxManager() if tmux else None
    main_repo_path = wt_manager.repo.working_dir

    try:
        with console.status(f"[bold blue]Creating worktree for '{branch}'..."):
            worktree = wt_manager.create(
                branch=branch,
                base_branch=base_branch,
                path=path,
                force=force,
            )

        console.print()
        console.print(f"[bold green]Worktree created successfully!")
        console.print()
        console.print(f"[bold]Branch:[/bold]  {worktree.branch}")
        console.print(f"[bold]Path:[/bold]    {worktree.short_path}")
        console.print(f"[bold]Commit:[/bold]  {worktree.head_commit}")

        # Environment setup (deps and .env)
        if deps or env:
            try:
                detector = ProjectDetector()
                project_config = detector.detect(str(worktree.path))

                if project_config:
                    env_setup = EnvironmentSetup(project_config)

                    if deps and project_config.package_manager:
                        console.print()
                        with console.status(f"[bold blue]Installing dependencies ({project_config.package_manager.value})..."):
                            try:
                                env_setup.install_dependencies(str(worktree.path))
                                console.print(f"[green]Dependencies installed[/green]")
                            except EnvironmentSetupError as e:
                                console.print(f"[yellow]Warning: Could not install dependencies: {e}[/yellow]")

                    if env:
                        try:
                            env_file = env_setup.setup_env_file(str(worktree.path), main_repo_path)
                            if env_file:
                                console.print(f"[green].env file copied and adjusted[/green]")
                        except EnvironmentSetupError as e:
                            console.print(f"[yellow]Warning: Could not setup .env: {e}[/yellow]")
                else:
                    if deps:
                        console.print(f"[yellow]Could not detect project type for dependency installation[/yellow]")

            except Exception as e:
                console.print(f"[yellow]Warning: Environment setup failed: {e}[/yellow]")

        # Create tmux session if enabled
        tmux_session = None
        if tmux and tmux_manager:
            try:
                layout_map = {
                    "main-vertical": TmuxLayout.MAIN_VERTICAL,
                    "three-pane": TmuxLayout.THREE_PANE,
                    "quad": TmuxLayout.QUAD,
                    "even-horizontal": TmuxLayout.EVEN_HORIZONTAL,
                    "even-vertical": TmuxLayout.EVEN_VERTICAL,
                }

                with console.status("[bold blue]Creating tmux session..."):
                    tmux_session = tmux_manager.create_worktree_session(
                        worktree_name=worktree.name,
                        worktree_path=str(worktree.path),
                        layout=layout_map[layout],
                        pane_count=panes,
                        auto_start_claude=claude,
                    )

                console.print()
                console.print(f"[bold green]tmux session created!")
                console.print(f"[bold]Session:[/bold] {tmux_session.session_name}")
                console.print(f"[bold]Layout:[/bold]  {layout}")
                console.print(f"[bold]Panes:[/bold]   {tmux_session.pane_count}")

                if claude:
                    console.print("[cyan]Claude Code started in main pane[/cyan]")

                # Initialize status tracking for the new worktree
                status_tracker = StatusTracker()
                status_tracker.initialize_status(
                    worktree_name=worktree.name,
                    worktree_path=str(worktree.path),
                    branch=worktree.branch,
                    tmux_session=tmux_session.session_name,
                )

            except TmuxSessionExistsError:
                console.print("[yellow]tmux session already exists[/yellow]")
            except TmuxError as e:
                console.print(f"[yellow]Warning: Could not create tmux session: {e}[/yellow]")

        console.print()

        if attach and tmux_session:
            console.print("[dim]Attaching to tmux session...[/dim]")
            tmux_manager.attach(tmux_session.session_name)
        elif tmux_session:
            console.print(f"[dim]Attach with: cwt tmux attach {tmux_session.session_name}[/dim]")
        else:
            console.print(f"[dim]cd {worktree.path}[/dim]")

    except WorktreeAlreadyExistsError as e:
        raise click.ClickException(str(e)) from e
    except WorktreeError as e:
        raise click.ClickException(str(e)) from e


@main.command("list")
@click.option(
    "-a",
    "--all",
    "show_all",
    is_flag=True,
    help="Show all worktrees including the main one.",
)
def list_worktrees(show_all: bool) -> None:
    """List all worktrees for this repository.

    By default, shows only non-main worktrees. Use --all to include
    the main repository worktree.

    Example:
        cwt list
        cwt list --all
    """
    manager = get_worktree_manager()

    worktrees = manager.list_all()

    if not show_all:
        worktrees = [wt for wt in worktrees if not wt.is_main]

    if not worktrees:
        console.print("[yellow]No worktrees found.[/yellow]")
        if not show_all:
            console.print("[dim]Use --all to show the main worktree.[/dim]")
        return

    table = Table(title="Git Worktrees", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Branch", style="green")
    table.add_column("Commit", style="dim")
    table.add_column("Path")
    table.add_column("Status", justify="center")

    for wt in worktrees:
        status = ""
        if wt.is_main:
            status = "[blue]main[/blue]"
        elif wt.is_detached:
            status = "[yellow]detached[/yellow]"
        else:
            status = "[green]active[/green]"

        table.add_row(
            wt.name,
            wt.branch,
            wt.head_commit,
            wt.short_path,
            status,
        )

    console.print()
    console.print(table)
    console.print()


@main.command("delete")
@click.argument("identifier")
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force deletion even with uncommitted changes.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.option(
    "--keep-tmux",
    is_flag=True,
    help="Keep the associated tmux session (by default it's killed).",
)
def delete_worktree(identifier: str, force: bool, yes: bool, keep_tmux: bool) -> None:
    """Delete a worktree by name, branch, or path.

    IDENTIFIER can be:
    - The worktree directory name
    - The branch name
    - The full path to the worktree

    By default, also kills the associated tmux session.

    Example:
        cwt delete feature/old-feature
        cwt delete project-feature-branch
        cwt delete feature/test --keep-tmux
    """
    wt_manager = get_worktree_manager()
    tmux_manager = TmuxManager()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    # Check for associated tmux session
    tmux_session = tmux_manager.get_session_for_worktree(worktree.name)

    if not yes:
        console.print()
        console.print(f"[bold]About to delete worktree:[/bold]")
        console.print(f"  Branch: {worktree.branch}")
        console.print(f"  Path:   {worktree.path}")

        if tmux_session and not keep_tmux:
            console.print(f"  tmux:   {tmux_session.session_name} [yellow](will be killed)[/yellow]")

        console.print()

        if not click.confirm("Are you sure you want to delete this worktree?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    try:
        # Kill tmux session first if it exists and --keep-tmux not specified
        if tmux_session and not keep_tmux:
            try:
                with console.status(f"[bold red]Killing tmux session '{tmux_session.session_name}'..."):
                    tmux_manager.kill_session(tmux_session.session_name)
                console.print(f"[green]Killed tmux session:[/green] {tmux_session.session_name}")
            except TmuxError as e:
                console.print(f"[yellow]Warning: Could not kill tmux session: {e}[/yellow]")

        with console.status(f"[bold red]Deleting worktree '{identifier}'..."):
            deleted_path = wt_manager.delete(identifier, force=force)

        # Clean up status tracking
        status_tracker = StatusTracker()
        status_tracker.remove_status(worktree.name)

        console.print()
        console.print(f"[bold green]Worktree deleted:[/bold green] {deleted_path}")

    except WorktreeError as e:
        raise click.ClickException(str(e)) from e


@main.command("switch")
@click.argument("identifier")
@click.option(
    "-t",
    "--tmux",
    is_flag=True,
    help="Attach to the worktree's tmux session instead of printing path.",
)
def switch_worktree(identifier: str, tmux: bool) -> None:
    """Switch to a worktree directory or attach to its tmux session.

    By default, prints the worktree path for use with cd.
    Use --tmux to attach to the worktree's tmux session instead.

    Example:
        cd $(cwt switch feature/my-feature)
        cwt switch feature/my-feature --tmux
    """
    wt_manager = get_worktree_manager()

    try:
        worktree = wt_manager.get(identifier)

        if tmux:
            tmux_manager = TmuxManager()
            session = tmux_manager.get_session_for_worktree(worktree.name)

            if session:
                if tmux_manager.is_inside_tmux():
                    tmux_manager.switch_client(session.session_name)
                else:
                    tmux_manager.attach(session.session_name)
            else:
                console.print(f"[yellow]No tmux session found for worktree '{identifier}'[/yellow]")
                console.print(f"[dim]Create one with: cwt tmux create {tmux_manager._generate_session_name(worktree.name)} -d {worktree.path}[/dim]")
                raise SystemExit(1)
        else:
            click.echo(worktree.path)

    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e
    except TmuxError as e:
        raise click.ClickException(str(e)) from e


@main.command("cleanup")
@click.option(
    "-d",
    "--days",
    "threshold_days",
    type=int,
    default=14,
    help="Days of inactivity before a worktree is considered stale (default: 14).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    help="Show what would be deleted without actually deleting (default: dry-run).",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force cleanup even for worktrees with uncommitted changes.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt (only used with --no-dry-run).",
)
def cleanup_worktrees(threshold_days: int, dry_run: bool, force: bool, yes: bool) -> None:
    """Clean up stale worktrees that haven't been used recently.

    By default, runs in dry-run mode showing what would be deleted.
    Use --no-dry-run to actually delete stale worktrees.

    Worktrees with uncommitted changes or unpushed commits are protected
    by default. Use --force to override this protection.

    Example:
        cwt cleanup                    # Dry run with default 14 days
        cwt cleanup --days 7           # Dry run with 7 days threshold
        cwt cleanup --no-dry-run -y    # Actually delete stale worktrees
        cwt cleanup --force            # Include worktrees with uncommitted changes
    """
    from claude_orchestrator.core.cleanup import CleanupConfig, CleanupService

    wt_manager = get_worktree_manager()

    config = CleanupConfig(
        stale_threshold_days=threshold_days,
        protect_uncommitted=not force,
        protect_unpushed=not force,
    )
    cleanup_service = CleanupService(config=config)

    worktrees = wt_manager.list_all()
    worktree_paths = [str(wt.path) for wt in worktrees if not wt.is_main]

    if not worktree_paths:
        console.print("[yellow]No worktrees to clean up.[/yellow]")
        return

    stale_worktrees = cleanup_service.get_stale_worktrees(worktree_paths, threshold_days)

    if not stale_worktrees:
        console.print(f"[green]No stale worktrees found (threshold: {threshold_days} days).[/green]")
        return

    console.print()
    console.print(f"[bold]Found {len(stale_worktrees)} stale worktree(s):[/bold]")
    console.print()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Path")
    table.add_column("Branch")
    table.add_column("Last Accessed")
    table.add_column("Status")

    for stats in stale_worktrees:
        status_parts = []
        if stats.has_uncommitted_changes:
            status_parts.append("[yellow]uncommitted[/yellow]")
        if stats.has_unpushed_commits:
            status_parts.append("[yellow]unpushed[/yellow]")
        status = ", ".join(status_parts) if status_parts else "[green]clean[/green]"

        table.add_row(
            stats.worktree_path,
            stats.branch_name,
            stats.last_accessed.strftime("%Y-%m-%d %H:%M"),
            status,
        )

    console.print(table)
    console.print()

    if dry_run:
        console.print("[blue]This is a dry run. No worktrees will be deleted.[/blue]")
        console.print("[dim]Run with --no-dry-run to actually delete stale worktrees.[/dim]")
        return

    if not yes and not force:
        protected_count = sum(
            1 for s in stale_worktrees
            if s.has_uncommitted_changes or s.has_unpushed_commits
        )
        if protected_count > 0:
            console.print(f"[yellow]{protected_count} worktree(s) will be skipped (uncommitted/unpushed).[/yellow]")

        if not click.confirm("Proceed with cleanup?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    report = cleanup_service.cleanup(
        worktree_paths=worktree_paths,
        dry_run=False,
        threshold_days=threshold_days,
        force=force,
    )

    console.print()
    console.print(f"[bold green]Cleanup complete![/bold green]")
    console.print(f"  Cleaned:  {report.worktrees_cleaned}")
    console.print(f"  Skipped:  {report.worktrees_skipped}")

    if report.errors:
        console.print()
        console.print("[bold red]Errors:[/bold red]")
        for error in report.errors:
            console.print(f"  [red]{error}[/red]")


@main.command("send")
@click.argument("identifier")
@click.argument("command")
@click.option(
    "-p",
    "--pane",
    type=int,
    default=0,
    help="Target pane index (default: 0, the main pane with Claude).",
)
@click.option(
    "-w",
    "--window",
    type=int,
    default=0,
    help="Target window index (default: 0).",
)
@click.option(
    "--no-enter",
    is_flag=True,
    help="Don't press Enter after sending the command.",
)
def send_to_worktree(
    identifier: str,
    command: str,
    pane: int,
    window: int,
    no_enter: bool,
) -> None:
    """Send a command to another worktree's tmux session.

    IDENTIFIER is the worktree name, branch, or path.
    COMMAND is the text to send to the worktree's Claude session.

    By default, sends to the main pane (pane 0) where Claude Code runs.
    Commands sent are tracked and visible via `cwt status`.

    Example:
        cwt send feature/auth "implement login validation"
        cwt send my-worktree "run the tests"
        cwt send feature/api "fix the bug in user service" --pane 1
    """
    wt_manager = get_worktree_manager()
    tmux_manager = TmuxManager()
    status_tracker = StatusTracker()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    session = tmux_manager.get_session_for_worktree(worktree.name)

    if not session:
        raise click.ClickException(
            f"No tmux session found for worktree '{identifier}'. "
            f"Create one with: cwt tmux create {tmux_manager._generate_session_name(worktree.name)} -d {worktree.path}"
        )

    # Get the source worktree (the one sending the command)
    source_worktree = status_tracker.get_current_worktree_name()

    try:
        if no_enter:
            # Send without Enter - use raw tmux command
            subprocess.run(
                ["tmux", "send-keys", "-t", f"{session.session_name}:{window}.{pane}", command],
                check=True
            )
        else:
            tmux_manager.send_keys_to_pane(
                session_name=session.session_name,
                keys=command,
                pane_index=pane,
                window_index=window
            )

        # Track the command in the status system
        wt_status = status_tracker.get_status(worktree.name)
        if not wt_status:
            # Initialize status if it doesn't exist
            wt_status = status_tracker.initialize_status(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                branch=worktree.branch,
                tmux_session=session.session_name,
            )

        status_tracker.record_command(
            target_worktree=worktree.name,
            command=command,
            source_worktree=source_worktree,
            pane_index=pane,
            window_index=window,
        )

        console.print(f"[green]Sent to {session.session_name}:[/green] {command[:50]}{'...' if len(command) > 50 else ''}")

    except TmuxError as e:
        raise click.ClickException(str(e)) from e
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Failed to send command: {e}") from e


@main.command("sync")
@click.argument("identifier", required=False)
@click.option(
    "-a",
    "--all",
    "sync_all",
    is_flag=True,
    help="Sync all worktrees.",
)
@click.option(
    "--strategy",
    type=click.Choice(["merge", "rebase"]),
    default="merge",
    help="Git pull strategy (default: merge).",
)
@click.option(
    "--no-stash",
    is_flag=True,
    help="Don't auto-stash uncommitted changes before syncing.",
)
def sync_worktrees(
    identifier: Optional[str],
    sync_all: bool,
    strategy: str,
    no_stash: bool,
) -> None:
    """Sync worktree(s) with upstream branch.

    Syncs a single worktree by identifier, or all worktrees with --all.
    By default, uncommitted changes are stashed and restored after sync.

    IDENTIFIER can be:
    - The worktree directory name
    - The branch name
    - The full path to the worktree

    Example:
        cwt sync feature/my-feature    # Sync specific worktree
        cwt sync --all                 # Sync all worktrees
        cwt sync --all --strategy rebase
    """
    from claude_orchestrator.core.sync import SyncConfig, SyncService, SyncStatus

    wt_manager = get_worktree_manager()

    config = SyncConfig(
        strategy=strategy,
        auto_stash=not no_stash,
    )
    sync_service = SyncService(config=config)

    if sync_all:
        worktrees = wt_manager.list_all()
        worktree_paths = [str(wt.path) for wt in worktrees if not wt.is_main]

        if not worktree_paths:
            console.print("[yellow]No worktrees to sync.[/yellow]")
            return

        console.print(f"[bold]Syncing {len(worktree_paths)} worktree(s)...[/bold]")
        console.print()

        with console.status("[bold blue]Syncing worktrees..."):
            report = sync_service.sync_all(worktree_paths)

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Worktree")
        table.add_column("Branch")
        table.add_column("Status")
        table.add_column("Details")

        for result in report.results:
            status_style = {
                SyncStatus.SUCCESS: "[green]synced[/green]",
                SyncStatus.UP_TO_DATE: "[blue]up to date[/blue]",
                SyncStatus.CONFLICTS: "[red]conflicts[/red]",
                SyncStatus.NO_UPSTREAM: "[yellow]no upstream[/yellow]",
                SyncStatus.ERROR: "[red]error[/red]",
                SyncStatus.UNCOMMITTED_CHANGES: "[yellow]uncommitted[/yellow]",
            }.get(result.status, str(result.status))

            table.add_row(
                Path(result.worktree_path).name,
                result.branch_name,
                status_style,
                result.message,
            )

        console.print(table)
        console.print()
        console.print(f"[bold]Summary:[/bold]")
        console.print(f"  Successful:    {report.successful}")
        console.print(f"  Up to date:    {report.up_to_date}")
        console.print(f"  With conflicts: {report.with_conflicts}")
        console.print(f"  Failed:        {report.failed}")

    elif identifier:
        try:
            worktree = wt_manager.get(identifier)
        except WorktreeNotFoundError as e:
            raise click.ClickException(str(e)) from e

        console.print(f"[bold]Syncing '{worktree.branch}'...[/bold]")

        with console.status("[bold blue]Fetching and pulling..."):
            result = sync_service.sync_worktree(str(worktree.path))

        console.print()

        if result.status == SyncStatus.SUCCESS:
            console.print(f"[bold green]Sync complete![/bold green]")
            console.print(f"  Pulled: {result.commits_pulled} commit(s)")
        elif result.status == SyncStatus.UP_TO_DATE:
            console.print(f"[bold blue]Already up to date.[/bold blue]")
            if result.commits_ahead > 0:
                console.print(f"  [dim]{result.commits_ahead} commit(s) ahead of upstream[/dim]")
        elif result.status == SyncStatus.CONFLICTS:
            console.print(f"[bold red]Merge conflicts detected![/bold red]")
            console.print(f"  {result.message}")
            console.print("[dim]Resolve conflicts and commit manually.[/dim]")
        elif result.status == SyncStatus.NO_UPSTREAM:
            console.print(f"[bold yellow]No upstream branch configured.[/bold yellow]")
            console.print("[dim]Set upstream with: git branch --set-upstream-to=origin/<branch>[/dim]")
        else:
            console.print(f"[bold red]Sync failed:[/bold red] {result.message}")

    else:
        raise click.ClickException("Provide a worktree identifier or use --all")


@main.command("status")
@click.argument("identifier", required=False)
@click.option(
    "-a",
    "--all",
    "show_all",
    is_flag=True,
    help="Show status for all worktrees.",
)
@click.option(
    "--set-task",
    "task",
    help="Set the current task for this worktree.",
)
@click.option(
    "--set-status",
    "activity_status",
    type=click.Choice(["idle", "working", "blocked", "waiting", "completed", "error"]),
    help="Set the activity status for this worktree.",
)
@click.option(
    "--notes",
    help="Set notes for this worktree.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def show_status(
    identifier: Optional[str],
    show_all: bool,
    task: Optional[str],
    activity_status: Optional[str],
    notes: Optional[str],
    as_json: bool,
) -> None:
    """Show or update Claude activity status across worktrees.

    View what Claude is working on in each worktree, or update the status
    for a specific worktree.

    Without arguments, shows status for all worktrees.
    With IDENTIFIER, shows or updates status for a specific worktree.

    Example:
        cwt status                          # Show all worktree statuses
        cwt status feature/auth             # Show status for specific worktree
        cwt status feature/auth --set-task "Implementing login"
        cwt status feature/auth --set-status working
        cwt status --json                   # Output as JSON
    """
    import json as json_module

    wt_manager = get_worktree_manager()
    status_tracker = StatusTracker()
    tmux_manager = TmuxManager()

    # Get all worktrees for reference
    worktrees = wt_manager.list_all()
    worktree_names = [wt.name for wt in worktrees]

    # Clean up orphaned status entries
    status_tracker.cleanup_orphans(worktree_names)

    # If setting status for a specific worktree
    if identifier and (task or activity_status or notes):
        try:
            worktree = wt_manager.get(identifier)
        except WorktreeNotFoundError as e:
            raise click.ClickException(str(e)) from e

        # Initialize status if it doesn't exist
        wt_status = status_tracker.get_status(worktree.name)
        if not wt_status:
            session = tmux_manager.get_session_for_worktree(worktree.name)
            wt_status = status_tracker.initialize_status(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                branch=worktree.branch,
                tmux_session=session.session_name if session else None,
            )

        # Update task if provided
        if task:
            status_enum = ClaudeActivityStatus.WORKING
            if activity_status:
                status_enum = ClaudeActivityStatus(activity_status)
            status_tracker.update_task(worktree.name, task, status_enum)
            console.print(f"[green]Task updated:[/green] {task}")

        # Update activity status if provided (without task)
        elif activity_status:
            status_map = {
                "idle": ClaudeActivityStatus.IDLE,
                "working": ClaudeActivityStatus.WORKING,
                "blocked": ClaudeActivityStatus.BLOCKED,
                "waiting": ClaudeActivityStatus.WAITING,
                "completed": ClaudeActivityStatus.COMPLETED,
                "error": ClaudeActivityStatus.ERROR,
            }
            wt_status = status_tracker.get_status(worktree.name)
            if wt_status:
                wt_status.activity_status = status_map[activity_status]
                from datetime import datetime
                wt_status.updated_at = datetime.now()
                status_tracker._store.set_status(wt_status)
                status_tracker._save_store()
                console.print(f"[green]Status updated:[/green] {activity_status}")

        # Update notes if provided
        if notes:
            status_tracker.set_notes(worktree.name, notes)
            console.print(f"[green]Notes updated[/green]")

        return

    # Show status for all or specific worktree
    if identifier:
        try:
            worktree = wt_manager.get(identifier)
        except WorktreeNotFoundError as e:
            raise click.ClickException(str(e)) from e

        wt_status = status_tracker.get_status(worktree.name)

        if as_json:
            if wt_status:
                console.print(json_module.dumps(wt_status.model_dump(mode="json"), indent=2))
            else:
                console.print("{}")
            return

        if not wt_status:
            console.print(f"[yellow]No status tracked for '{identifier}'[/yellow]")
            console.print(f"[dim]Initialize with: cwt status {identifier} --set-task 'Your task'[/dim]")
            return

        _print_worktree_status_detail(wt_status, worktree)

    else:
        # Show summary of all worktrees
        summary = status_tracker.get_summary(worktree_names)

        if as_json:
            console.print(json_module.dumps(summary.model_dump(mode="json"), indent=2))
            return

        if not summary.statuses:
            console.print("[yellow]No status information tracked yet.[/yellow]")
            console.print()
            console.print("[dim]Status tracking begins when:[/dim]")
            console.print("[dim]  - You create a worktree with: cwt create <branch>[/dim]")
            console.print("[dim]  - You send a command with: cwt send <worktree> 'command'[/dim]")
            console.print("[dim]  - You set status with: cwt status <worktree> --set-task 'task'[/dim]")
            return

        console.print()
        console.print("[bold]Claude Activity Across Worktrees[/bold]")
        console.print()

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Worktree", style="bold")
        table.add_column("Branch", style="green")
        table.add_column("Status", justify="center")
        table.add_column("Current Task")
        table.add_column("Commands", justify="center")
        table.add_column("Last Update")

        for wt_status in summary.statuses:
            status_style = {
                "idle": "[dim]idle[/dim]",
                "working": "[green]working[/green]",
                "blocked": "[red]blocked[/red]",
                "waiting": "[yellow]waiting[/yellow]",
                "completed": "[blue]completed[/blue]",
                "error": "[red]error[/red]",
                "unknown": "[dim]unknown[/dim]",
            }.get(wt_status.activity_status, wt_status.activity_status)

            task_display = wt_status.current_task or "[dim]-[/dim]"
            if len(task_display) > 40:
                task_display = task_display[:37] + "..."

            last_update = wt_status.updated_at.strftime("%H:%M %b %d") if wt_status.updated_at else "-"

            table.add_row(
                wt_status.worktree_name,
                wt_status.branch,
                status_style,
                task_display,
                str(len(wt_status.recent_commands)),
                last_update,
            )

        console.print(table)
        console.print()

        # Summary stats
        console.print(f"[bold]Summary:[/bold]")
        console.print(f"  Working: {summary.active_claudes}  |  Idle: {summary.idle_claudes}  |  Blocked: {summary.blocked_claudes}")
        console.print(f"  Total commands sent: {summary.total_commands_sent}")

        if summary.most_recent_activity:
            console.print(f"  Most recent activity: {summary.most_recent_activity.strftime('%Y-%m-%d %H:%M')}")


def _print_worktree_status_detail(wt_status, worktree) -> None:
    """Print detailed status for a single worktree."""
    status_style = {
        "idle": "[dim]idle[/dim]",
        "working": "[green]working[/green]",
        "blocked": "[red]blocked[/red]",
        "waiting": "[yellow]waiting[/yellow]",
        "completed": "[blue]completed[/blue]",
        "error": "[red]error[/red]",
        "unknown": "[dim]unknown[/dim]",
    }.get(wt_status.activity_status, wt_status.activity_status)

    console.print()
    console.print(f"[bold]Status for '{worktree.name}'[/bold]")
    console.print()
    console.print(f"[bold]Branch:[/bold]     {wt_status.branch}")
    console.print(f"[bold]Path:[/bold]       {wt_status.worktree_path}")
    console.print(f"[bold]tmux:[/bold]       {wt_status.tmux_session or '[dim]none[/dim]'}")
    console.print(f"[bold]Status:[/bold]     {status_style}")
    console.print(f"[bold]Task:[/bold]       {wt_status.current_task or '[dim]none[/dim]'}")

    if wt_status.notes:
        console.print(f"[bold]Notes:[/bold]      {wt_status.notes}")

    if wt_status.last_task_update:
        console.print(f"[bold]Task set:[/bold]   {wt_status.last_task_update.strftime('%Y-%m-%d %H:%M')}")

    console.print(f"[bold]Updated:[/bold]    {wt_status.updated_at.strftime('%Y-%m-%d %H:%M')}")

    if wt_status.recent_commands:
        console.print()
        console.print(f"[bold]Recent Commands ({len(wt_status.recent_commands)}):[/bold]")

        for cmd in wt_status.recent_commands[-5:]:  # Show last 5
            source = f"[dim]from {cmd.source_worktree}[/dim]" if cmd.source_worktree else "[dim]manual[/dim]"
            cmd_display = cmd.command[:60] + "..." if len(cmd.command) > 60 else cmd.command
            time_str = cmd.timestamp.strftime("%H:%M")
            console.print(f"  [{time_str}] {cmd_display} {source}")


if __name__ == "__main__":
    main()
