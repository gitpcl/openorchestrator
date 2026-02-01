"""CLI entry point for Open Orchestrator."""

import json
import subprocess
from contextlib import nullcontext
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from open_orchestrator.config import AITool, DroidAutoLevel
from open_orchestrator.core.environment import (
    EnvironmentSetup,
    EnvironmentSetupError,
    sync_claude_md,
)
from open_orchestrator.core.project_detector import ProjectDetector
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.tmux_cli import tmux_group
from open_orchestrator.core.tmux_manager import (
    TmuxError,
    TmuxLayout,
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
from open_orchestrator.models.worktree_info import WorktreeInfo

console = Console()


def get_worktree_manager(repo_path: Path | None = None) -> WorktreeManager:
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
@click.version_option(package_name="open-orchestrator")
def main() -> None:
    """Open Orchestrator - Git Worktree + Claude Code orchestration tool.

    Manage parallel development workflows with git worktrees and tmux sessions.
    """


# Register tmux command group
main.add_command(tmux_group)


@main.group("completion")
def completion_group() -> None:
    """Generate shell auto-completion scripts.

    Install completions to enable tab-completion for owt commands.
    """


@completion_group.command("bash")
def completion_bash() -> None:
    """Generate bash completion script.

    To install permanently, add to your ~/.bashrc:

        eval "$(owt completion bash)"

    Or save to a file:

        owt completion bash > ~/.local/share/bash-completion/completions/owt
    """
    click.echo('eval "$(_OWT_COMPLETE=bash_source owt)"')


@completion_group.command("zsh")
def completion_zsh() -> None:
    """Generate zsh completion script.

    To install permanently, add to your ~/.zshrc:

        eval "$(owt completion zsh)"

    Or save to a file in your fpath:

        owt completion zsh > ~/.zfunc/_owt
    """
    click.echo('eval "$(_OWT_COMPLETE=zsh_source owt)"')


@completion_group.command("fish")
def completion_fish() -> None:
    """Generate fish completion script.

    To install permanently:

        owt completion fish > ~/.config/fish/completions/owt.fish

    Or add to your config.fish:

        owt completion fish | source
    """
    click.echo("_OWT_COMPLETE=fish_source owt | source")


@completion_group.command("install")
@click.option(
    "--shell",
    type=click.Choice(["bash", "zsh", "fish", "auto"]),
    default="auto",
    help="Shell type (default: auto-detect).",
)
def completion_install(shell: str) -> None:
    """Show installation instructions for shell completion.

    Detects your shell automatically and provides the appropriate
    installation command.
    """
    import os

    if shell == "auto":
        shell_path = os.environ.get("SHELL", "")
        if "zsh" in shell_path:
            shell = "zsh"
        elif "fish" in shell_path:
            shell = "fish"
        else:
            shell = "bash"

    console.print(f"[bold]Shell completion for {shell}[/bold]")
    console.print()

    if shell == "bash":
        console.print("[cyan]Add to ~/.bashrc:[/cyan]")
        console.print()
        console.print('  eval "$(owt completion bash)"')
        console.print()
        console.print("[cyan]Or save to completion directory:[/cyan]")
        console.print()
        console.print("  owt completion bash > ~/.local/share/bash-completion/completions/owt")
    elif shell == "zsh":
        console.print("[cyan]Add to ~/.zshrc:[/cyan]")
        console.print()
        console.print('  eval "$(owt completion zsh)"')
        console.print()
        console.print("[cyan]Or save to your fpath:[/cyan]")
        console.print()
        console.print("  mkdir -p ~/.zfunc && owt completion zsh > ~/.zfunc/_owt")
        console.print("  # Then add to ~/.zshrc before compinit:")
        console.print("  fpath=(~/.zfunc $fpath)")
    elif shell == "fish":
        console.print("[cyan]Save to completions directory:[/cyan]")
        console.print()
        console.print("  owt completion fish > ~/.config/fish/completions/owt.fish")
        console.print()
        console.print("[cyan]Or add to config.fish:[/cyan]")
        console.print()
        console.print("  owt completion fish | source")

    console.print()
    console.print("[dim]Restart your shell or source your config to enable completions.[/dim]")


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
    help="Auto-start AI tool in the tmux session (default: enabled).",
)
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid"]),
    default="claude",
    help="AI coding tool to start (default: claude).",
)
@click.option(
    "--droid-auto",
    type=click.Choice(["low", "medium", "high"]),
    default=None,
    help="Droid auto mode level (only used with --ai-tool droid).",
)
@click.option(
    "--droid-skip-permissions",
    is_flag=True,
    help="Skip Droid permissions check (use with caution).",
)
@click.option(
    "--opencode-config",
    type=click.Path(exists=True),
    help="Path to OpenCode configuration file.",
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
@click.option(
    "--plan-mode",
    is_flag=True,
    help="Start Claude in plan mode (--permission-mode plan).",
)
@click.option(
    "--sync-claude-md/--no-sync-claude-md",
    default=True,
    help="Sync CLAUDE.md files from main repo (default: enabled).",
)
def create_worktree(
    branch: str,
    base_branch: str | None,
    path: Path | None,
    force: bool,
    tmux: bool,
    claude: bool,
    ai_tool: str,
    droid_auto: str | None,
    droid_skip_permissions: bool,
    opencode_config: str | None,
    layout: str,
    panes: int,
    attach: bool,
    deps: bool,
    env: bool,
    plan_mode: bool,
    sync_claude_md: bool,
) -> None:
    """Create a new worktree for BRANCH with tmux session.

    If BRANCH doesn't exist, it will be created from the base branch
    (or current branch if not specified).

    By default, creates a tmux session, installs dependencies, copies .env,
    and starts Claude Code.

    Example:
        owt create feature/new-feature
        owt create bugfix/fix-123 --base main
        owt create feature/test --no-tmux
        owt create feature/dev --layout three-pane --attach
        owt create feature/quick --no-deps --no-env
        owt create feature/research --plan-mode
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
        console.print("[bold green]Worktree created successfully!")
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
                        with console.status(
                            f"[bold blue]Installing dependencies "
                            f"({project_config.package_manager.value})..."
                        ):
                            try:
                                env_setup.install_dependencies(str(worktree.path))
                                console.print("[green]Dependencies installed[/green]")
                            except EnvironmentSetupError as e:
                                console.print(
                                    f"[yellow]Warning: Could not install dependencies: {e}[/yellow]"
                                )

                    if env:
                        try:
                            env_file = env_setup.setup_env_file(str(worktree.path), Path(main_repo_path))
                            if env_file:
                                console.print("[green].env file copied and adjusted[/green]")
                        except EnvironmentSetupError as e:
                            console.print(f"[yellow]Warning: Could not setup .env: {e}[/yellow]")
                else:
                    if deps:
                        console.print("[yellow]Could not detect project type for dependency installation[/yellow]")

            except Exception as e:
                console.print(f"[yellow]Warning: Environment setup failed: {e}[/yellow]")

        # Sync CLAUDE.md files if enabled
        if sync_claude_md:
            try:
                # Import the function locally to avoid name collision with parameter
                from open_orchestrator.core.environment import (
                    sync_claude_md as do_sync_claude_md,
                )

                copied_files = do_sync_claude_md(str(worktree.path), main_repo_path)
                if copied_files:
                    console.print(f"[green]CLAUDE.md synced ({len(copied_files)} file(s))[/green]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not sync CLAUDE.md: {e}[/yellow]")

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

                # Map strings to enums
                ai_tool_enum = AITool(ai_tool)
                droid_auto_enum = DroidAutoLevel(droid_auto) if droid_auto else None

                with console.status("[bold blue]Creating tmux session..."):
                    tmux_session = tmux_manager.create_worktree_session(
                        worktree_name=worktree.name,
                        worktree_path=str(worktree.path),
                        layout=layout_map[layout],
                        pane_count=panes,
                        auto_start_ai=claude,
                        ai_tool=ai_tool_enum,
                        droid_auto=droid_auto_enum,
                        droid_skip_permissions=droid_skip_permissions,
                        opencode_config=opencode_config,
                        plan_mode=plan_mode,
                    )

                console.print()
                console.print("[bold green]tmux session created!")
                console.print(f"[bold]Session:[/bold] {tmux_session.session_name}")
                console.print(f"[bold]Layout:[/bold]  {layout}")
                console.print(f"[bold]Panes:[/bold]   {tmux_session.pane_count}")

                if claude:
                    tool_name = ai_tool_enum.value.title()
                    mode_info = " (plan mode)" if plan_mode else ""
                    console.print(f"[cyan]{tool_name} started in main pane{mode_info}[/cyan]")

                # Initialize status tracking for the new worktree
                status_tracker = StatusTracker()
                status_tracker.initialize_status(
                    worktree_name=worktree.name,
                    worktree_path=str(worktree.path),
                    branch=worktree.branch,
                    tmux_session=tmux_session.session_name,
                    ai_tool=ai_tool_enum,
                )

                # Auto-detect and link PR if pattern matches
                try:
                    from open_orchestrator.core.pr_linker import PRLinker

                    pr_linker = PRLinker()
                    pr_result = pr_linker.detect_and_link_pr(
                        worktree_name=worktree.name,
                        worktree_path=str(worktree.path),
                        branch=worktree.branch,
                    )

                    if pr_result and pr_result.success:
                        console.print(f"[cyan]Linked to PR #{pr_result.pr_number}[/cyan]")
                except Exception:
                    pass

            except TmuxSessionExistsError:
                console.print("[yellow]tmux session already exists[/yellow]")
            except TmuxError as e:
                console.print(f"[red]Error: Could not create tmux session: {e}[/red]")
                console.print("[yellow]Rolling back worktree creation...[/yellow]")
                try:
                    wt_manager.delete(worktree.name, force=True)
                    console.print("[green]Worktree deleted.[/green]")
                except WorktreeError as we:
                    console.print(f"[red]Failed to delete worktree: {we}[/red]")
                raise click.ClickException(f"Failed to create tmux session: {e}") from e

        console.print()

        if attach and tmux_session and tmux_manager is not None:
            console.print("[dim]Attaching to tmux session...[/dim]")
            # Note: attach() replaces the current process and does not return
            tmux_manager.attach(tmux_session.session_name)
        elif tmux_session:
            console.print(f"[dim]Attach with: owt tmux attach {tmux_session.session_name}[/dim]")
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
        owt list
        owt list --all
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
        owt delete feature/old-feature
        owt delete project-feature-branch
        owt delete feature/test --keep-tmux
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
        console.print("[bold]About to delete worktree:[/bold]")
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
        cd $(owt switch feature/my-feature)
        owt switch feature/my-feature --tmux
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
                hint = (
                    f"[dim]Create one with: owt tmux create "
                    f"{tmux_manager._generate_session_name(worktree.name)} "
                    f"-d {worktree.path}[/dim]"
                )
                console.print(hint)
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
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON (for scripting).",
)
def cleanup_worktrees(threshold_days: int, dry_run: bool, force: bool, yes: bool, as_json: bool) -> None:
    """Clean up stale worktrees that haven't been used recently.

    By default, runs in dry-run mode showing what would be deleted.
    Use --no-dry-run to actually delete stale worktrees.

    Worktrees with uncommitted changes or unpushed commits are protected
    by default. Use --force to override this protection.

    Example:
        owt cleanup                    # Dry run with default 14 days
        owt cleanup --days 7           # Dry run with 7 days threshold
        owt cleanup --no-dry-run -y    # Actually delete stale worktrees
        owt cleanup --force            # Include worktrees with uncommitted changes
        owt cleanup --json             # Output as JSON
    """
    import json as json_module

    from open_orchestrator.core.cleanup import CleanupConfig, CleanupService

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
        if as_json:
            console.print(json_module.dumps({"stale_worktrees": [], "message": "No worktrees to clean up"}))
        else:
            console.print("[yellow]No worktrees to clean up.[/yellow]")
        return

    stale_worktrees = cleanup_service.get_stale_worktrees(worktree_paths, threshold_days)

    if not stale_worktrees:
        if as_json:
            console.print(json_module.dumps({"stale_worktrees": [], "threshold_days": threshold_days}))
        else:
            console.print(f"[green]No stale worktrees found (threshold: {threshold_days} days).[/green]")
        return

    if as_json:
        data = {
            "stale_worktrees": [
                {
                    "path": s.worktree_path,
                    "branch": s.branch_name,
                    "last_accessed": s.last_accessed.isoformat(),
                    "has_uncommitted_changes": s.has_uncommitted_changes,
                    "has_unpushed_commits": s.has_unpushed_commits,
                }
                for s in stale_worktrees
            ],
            "threshold_days": threshold_days,
            "dry_run": dry_run,
        }

        if not dry_run:
            report = cleanup_service.cleanup(
                worktree_paths=worktree_paths,
                dry_run=False,
                threshold_days=threshold_days,
                force=force,
            )
            data["report"] = {
                "worktrees_cleaned": report.worktrees_cleaned,
                "worktrees_skipped": report.worktrees_skipped,
                "errors": report.errors,
            }

        console.print(json_module.dumps(data, indent=2))
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
    console.print("[bold green]Cleanup complete![/bold green]")
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
@click.option(
    "--no-log",
    is_flag=True,
    help="Do not persist this command in status history.",
)
def send_to_worktree(
    identifier: str,
    command: str,
    pane: int,
    window: int,
    no_enter: bool,
    no_log: bool,
) -> None:
    """Send a command to another worktree's tmux session.

    IDENTIFIER is the worktree name, branch, or path.
    COMMAND is the text to send to the worktree's Claude session.

    By default, sends to the main pane (pane 0) where Claude Code runs.
    Commands sent are tracked and visible via `owt status`.

    Example:
        owt send feature/auth "implement login validation"
        owt send my-worktree "run the tests"
        owt send feature/api "fix the bug in user service" --pane 1
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
        msg = (
            f"No tmux session found for worktree '{identifier}'. "
            f"Create one with: owt tmux create "
            f"{tmux_manager._generate_session_name(worktree.name)} -d {worktree.path}"
        )
        raise click.ClickException(msg)

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

        # Record command unless --no-log is specified
        if not no_log:
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
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output results in JSON format.",
)
def sync_worktrees(
    identifier: str | None,
    sync_all: bool,
    strategy: str,
    no_stash: bool,
    json_output: bool,
) -> None:
    """Sync worktree(s) with upstream branch.

    Syncs a single worktree by identifier, or all worktrees with --all.
    By default, uncommitted changes are stashed and restored after sync.

    IDENTIFIER can be:
    - The worktree directory name
    - The branch name
    - The full path to the worktree

    Example:
        owt sync feature/my-feature    # Sync specific worktree
        owt sync --all                 # Sync all worktrees
        owt sync --all --strategy rebase
    """
    from open_orchestrator.core.sync import SyncConfig, SyncService, SyncStatus

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
            if json_output:
                click.echo(json.dumps({"results": [], "summary": {}}))
            else:
                console.print("[yellow]No worktrees to sync.[/yellow]")
            return

        if not json_output:
            console.print(f"[bold]Syncing {len(worktree_paths)} worktree(s)...[/bold]")
            console.print()

        with console.status("[bold blue]Syncing worktrees...") if not json_output else nullcontext():
            report = sync_service.sync_all(worktree_paths)

        if json_output:
            click.echo(json.dumps(report.model_dump(), default=str))
            return

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
        console.print("[bold]Summary:[/bold]")
        console.print(f"  Successful:    {report.successful}")
        console.print(f"  Up to date:    {report.up_to_date}")
        console.print(f"  With conflicts: {report.with_conflicts}")
        console.print(f"  Failed:        {report.failed}")

    elif identifier:
        try:
            worktree = wt_manager.get(identifier)
        except WorktreeNotFoundError as e:
            raise click.ClickException(str(e)) from e

        if not json_output:
            console.print(f"[bold]Syncing '{worktree.branch}'...[/bold]")

        with console.status("[bold blue]Fetching and pulling...") if not json_output else nullcontext():
            result = sync_service.sync_worktree(str(worktree.path))

        if json_output:
            click.echo(json.dumps(result.model_dump(), default=str))
            return

        console.print()

        if result.status == SyncStatus.SUCCESS:
            console.print("[bold green]Sync complete![/bold green]")
            console.print(f"  Pulled: {result.commits_pulled} commit(s)")
        elif result.status == SyncStatus.UP_TO_DATE:
            console.print("[bold blue]Already up to date.[/bold blue]")
            if result.commits_ahead > 0:
                console.print(f"  [dim]{result.commits_ahead} commit(s) ahead of upstream[/dim]")
        elif result.status == SyncStatus.CONFLICTS:
            console.print("[bold red]Merge conflicts detected![/bold red]")
            console.print(f"  {result.message}")
            console.print("[dim]Resolve conflicts and commit manually.[/dim]")
        elif result.status == SyncStatus.NO_UPSTREAM:
            console.print("[bold yellow]No upstream branch configured.[/bold yellow]")
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
    identifier: str | None,
    show_all: bool,
    task: str | None,
    activity_status: str | None,
    notes: str | None,
    as_json: bool,
) -> None:
    """Show or update AI tool activity status across worktrees.

    View what AI tools (Claude, OpenCode, Droid) are working on in each worktree,
    or update the status for a specific worktree.

    Without arguments, shows status for all worktrees.
    With IDENTIFIER, shows or updates status for a specific worktree.

    Example:
        owt status                          # Show all worktree statuses
        owt status feature/auth             # Show status for specific worktree
        owt status feature/auth --set-task "Implementing login"
        owt status feature/auth --set-status working
        owt status --json                   # Output as JSON
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
            status_enum = AIActivityStatus.WORKING
            if activity_status:
                status_enum = AIActivityStatus(activity_status)
            status_tracker.update_task(worktree.name, task, status_enum)
            console.print(f"[green]Task updated:[/green] {task}")

        # Update activity status if provided (without task)
        elif activity_status:
            status_map = {
                "idle": AIActivityStatus.IDLE,
                "working": AIActivityStatus.WORKING,
                "blocked": AIActivityStatus.BLOCKED,
                "waiting": AIActivityStatus.WAITING,
                "completed": AIActivityStatus.COMPLETED,
                "error": AIActivityStatus.ERROR,
            }
            wt_status = status_tracker.get_status(worktree.name)
            if wt_status:
                wt_status.activity_status = status_map[activity_status]
                from datetime import datetime
                wt_status.updated_at = datetime.now()
                status_tracker.set_status(wt_status)
                console.print(f"[green]Status updated:[/green] {activity_status}")

        # Update notes if provided
        if notes:
            status_tracker.set_notes(worktree.name, notes)
            console.print("[green]Notes updated[/green]")

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
            console.print(f"[dim]Initialize with: owt status {identifier} --set-task 'Your task'[/dim]")
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
            console.print("[dim]  - You create a worktree with: owt create <branch>[/dim]")
            console.print("[dim]  - You send a command with: owt send <worktree> 'command'[/dim]")
            console.print("[dim]  - You set status with: owt status <worktree> --set-task 'task'[/dim]")
            return

        console.print()
        console.print("[bold]AI Tool Activity Across Worktrees[/bold]")
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
        console.print("[bold]Summary:[/bold]")
        console.print(
            f"  Working: {summary.active_ai_sessions}  |  "
            f"Idle: {summary.idle_ai_sessions}"
        )
        console.print(f"  Blocked: {summary.blocked_ai_sessions}")
        console.print(f"  Total commands sent: {summary.total_commands_sent}")

        # Token usage summary
        if summary.total_input_tokens > 0 or summary.total_output_tokens > 0:
            console.print()
            console.print("[bold]Token Usage:[/bold]")
            total_tokens = summary.total_input_tokens + summary.total_output_tokens
            console.print(f"  Total: {total_tokens:,} tokens")
            console.print(f"  Input: {summary.total_input_tokens:,}  |  Output: {summary.total_output_tokens:,}")
            console.print(f"  Estimated cost: ${summary.total_estimated_cost_usd:.4f}")

        if summary.most_recent_activity:
            latest = summary.most_recent_activity.strftime('%Y-%m-%d %H:%M')
            console.print(f"  Most recent activity: {latest}")


def _print_worktree_status_detail(wt_status: WorktreeAIStatus, worktree: WorktreeInfo) -> None:
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

    # Token usage
    tokens = wt_status.token_usage
    if tokens.total_tokens > 0:
        console.print()
        console.print("[bold]Token Usage:[/bold]")
        console.print(f"  Total: {tokens.total_tokens:,} tokens")
        console.print(f"  Input: {tokens.input_tokens:,}  |  Output: {tokens.output_tokens:,}")
        if tokens.cache_read_tokens > 0 or tokens.cache_write_tokens > 0:
            console.print(f"  Cache: {tokens.cache_read_tokens:,} read  |  {tokens.cache_write_tokens:,} write")
        console.print(f"  Estimated cost: ${tokens.estimated_cost_usd:.4f}")

    console.print(f"[bold]Updated:[/bold]    {wt_status.updated_at.strftime('%Y-%m-%d %H:%M')}")

    if wt_status.recent_commands:
        console.print()
        console.print(f"[bold]Recent Commands ({len(wt_status.recent_commands)}):[/bold]")

        for cmd in wt_status.recent_commands[-5:]:  # Show last 5
            source = f"[dim]from {cmd.source_worktree}[/dim]" if cmd.source_worktree else "[dim]manual[/dim]"
            cmd_display = cmd.command[:60] + "..." if len(cmd.command) > 60 else cmd.command
            time_str = cmd.timestamp.strftime("%H:%M")
            console.print(f"  [{time_str}] {cmd_display} {source}")


@main.command("copy-session")
@click.argument("source")
@click.argument("target")
@click.option(
    "--overwrite",
    is_flag=True,
    help="Overwrite existing session data in target worktree.",
)
def copy_session(source: str, target: str, overwrite: bool) -> None:
    """Copy Claude session data from one worktree to another.

    This preserves the conversation history and context from the source
    worktree, allowing Claude to continue where it left off in the target.

    SOURCE and TARGET can be worktree names, branch names, or paths.

    Example:
        owt copy-session feature/auth feature/auth-v2
        owt copy-session main-worktree new-feature --overwrite
    """
    from open_orchestrator.core.session import SessionManager
    from open_orchestrator.models.session import SessionCopyStatus

    wt_manager = get_worktree_manager()
    session_manager = SessionManager()

    try:
        source_wt = wt_manager.get(source)
    except WorktreeNotFoundError as e:
        raise click.ClickException(f"Source worktree not found: {e}") from e

    try:
        target_wt = wt_manager.get(target)
    except WorktreeNotFoundError as e:
        raise click.ClickException(f"Target worktree not found: {e}") from e

    with console.status(f"[bold blue]Copying session from '{source_wt.name}' to '{target_wt.name}'..."):
        result = session_manager.copy_session(
            source_worktree_name=source_wt.name,
            source_worktree_path=str(source_wt.path),
            target_worktree_name=target_wt.name,
            target_worktree_path=str(target_wt.path),
            overwrite=overwrite,
        )

    console.print()

    if result.status == SessionCopyStatus.SUCCESS:
        console.print("[bold green]Session copied successfully!")
        console.print(f"  Files copied: {len(result.files_copied)}")
        if result.session_id:
            console.print(f"  Session ID: {result.session_id}")
        console.print()
        console.print("[dim]Resume the session in the target worktree with:[/dim]")
        console.print(f"  [cyan]cd {target_wt.path} && claude --continue[/cyan]")

    elif result.status == SessionCopyStatus.PARTIAL:
        console.print("[bold yellow]Session partially copied.")
        console.print(f"  Files copied: {len(result.files_copied)}")
        console.print(f"  Files skipped: {len(result.files_skipped)}")
        for skipped in result.files_skipped[:5]:
            console.print(f"    [dim]{skipped}[/dim]")

    elif result.status == SessionCopyStatus.NO_SESSION:
        console.print(f"[yellow]{result.message}[/yellow]")
        console.print("[dim]Start a Claude session in the source worktree first.[/dim]")

    else:
        raise click.ClickException(result.message)


@main.command("resume")
@click.argument("identifier")
@click.option(
    "-t",
    "--tmux",
    is_flag=True,
    help="Attach to the worktree's tmux session after resuming.",
)
@click.option(
    "--continue",
    "use_continue",
    is_flag=True,
    default=True,
    help="Use --continue flag (resume most recent session). Default.",
)
@click.option(
    "--session-id",
    help="Specific session ID to resume.",
)
def resume_session(identifier: str, tmux: bool, use_continue: bool, session_id: str | None) -> None:
    """Resume a Claude session in a worktree.

    This command helps you resume Claude where you left off in a worktree.
    By default, it shows the resume command. With --tmux, it attaches to the
    tmux session and sends the resume command.

    IDENTIFIER can be the worktree name, branch name, or path.

    Example:
        owt resume feature/auth              # Show resume command
        owt resume feature/auth --tmux       # Attach and resume
        owt resume feature/auth --session-id abc123
    """
    from open_orchestrator.core.session import SessionManager

    wt_manager = get_worktree_manager()
    session_manager = SessionManager()
    tmux_manager = TmuxManager()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    # Determine the resume command
    if session_id:
        resume_cmd = f"claude --resume {session_id}"
    else:
        resume_cmd = session_manager.get_resume_command(worktree.name, str(worktree.path))

        if not resume_cmd:
            resume_cmd = session_manager.get_continue_command(str(worktree.path))

    if tmux:
        session = tmux_manager.get_session_for_worktree(worktree.name)

        if not session:
            console.print(f"[yellow]No tmux session found for worktree '{identifier}'[/yellow]")
            console.print(f"[dim]Resume manually: cd {worktree.path} && {resume_cmd}[/dim]")
            raise SystemExit(1)

        # Send the resume command to the tmux session
        try:
            tmux_manager.send_keys_to_pane(
                session_name=session.session_name,
                keys=resume_cmd,
                pane_index=0,
                window_index=0,
            )
            console.print(f"[green]Sent resume command to {session.session_name}[/green]")
        except TmuxError as e:
            raise click.ClickException(f"Failed to send resume command: {e}") from e

        # Attach to the session
        console.print("[dim]Attaching to tmux session...[/dim]")

        if tmux_manager.is_inside_tmux():
            tmux_manager.switch_client(session.session_name)
        else:
            tmux_manager.attach(session.session_name)

    else:
        console.print()
        console.print(f"[bold]Resume session for '{worktree.name}'[/bold]")
        console.print()
        console.print("[dim]Run the following command:[/dim]")
        console.print(f"  [cyan]cd {worktree.path} && {resume_cmd}[/cyan]")
        console.print()
        console.print("[dim]Or use --tmux to attach and resume automatically:[/dim]")
        console.print(f"  [dim]owt resume {identifier} --tmux[/dim]")


@main.command("session")
@click.argument("identifier", required=False)
@click.option(
    "-a",
    "--all",
    "show_all",
    is_flag=True,
    help="Show session info for all worktrees.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def show_session_info(identifier: str | None, show_all: bool, as_json: bool) -> None:
    """Show Claude session information for worktrees.

    Displays session data, including whether sessions were copied from
    other worktrees and available resume commands.

    Example:
        owt session                      # Show all sessions
        owt session feature/auth         # Show specific worktree
        owt session --json               # Output as JSON
    """
    import json as json_module

    from open_orchestrator.core.session import SessionManager

    wt_manager = get_worktree_manager()
    session_manager = SessionManager()

    if identifier:
        try:
            worktree = wt_manager.get(identifier)
        except WorktreeNotFoundError as e:
            raise click.ClickException(str(e)) from e

        session = session_manager.get_session(worktree.name)

        if not session:
            session = session_manager.initialize_session(worktree.name, str(worktree.path))

        if as_json:
            console.print(json_module.dumps(session.model_dump(mode="json"), indent=2))
            return

        console.print()
        console.print(f"[bold]Session for '{worktree.name}'[/bold]")
        console.print()
        console.print(f"[bold]Path:[/bold]       {session.worktree_path}")
        console.print(f"[bold]Session ID:[/bold] {session.session_id or '[dim]none[/dim]'}")
        console.print(f"[bold]Has session:[/bold] {'[green]yes[/green]' if session.has_session else '[yellow]no[/yellow]'}")

        if session.is_copied:
            console.print(f"[bold]Copied from:[/bold] {session.copied_from}")
            if session.copied_at:
                console.print(f"[bold]Copied at:[/bold]   {session.copied_at.strftime('%Y-%m-%d %H:%M')}")

        resume_cmd = session_manager.get_resume_command(worktree.name, str(worktree.path))

        if resume_cmd:
            console.print()
            console.print("[bold]Resume command:[/bold]")
            console.print(f"  [cyan]{resume_cmd}[/cyan]")

    else:
        worktrees = wt_manager.list_all()
        sessions = session_manager.get_all_sessions()
        session_map = {s.worktree_name: s for s in sessions}

        if as_json:
            data = [s.model_dump(mode="json") for s in sessions]
            console.print(json_module.dumps(data, indent=2))
            return

        if not sessions and not show_all:
            console.print("[yellow]No session information tracked yet.[/yellow]")
            console.print()
            console.print("[dim]Session tracking begins when:[/dim]")
            console.print("[dim]  - You copy a session: owt copy-session <source> <target>[/dim]")
            console.print("[dim]  - You view session info: owt session <worktree>[/dim]")
            return

        table = Table(title="Claude Sessions", show_header=True, header_style="bold cyan")
        table.add_column("Worktree", style="bold")
        table.add_column("Has Session", justify="center")
        table.add_column("Session ID")
        table.add_column("Copied From")
        table.add_column("Updated")

        for wt in worktrees:
            if wt.is_main and not show_all:
                continue

            session = session_map.get(wt.name)

            if session:
                has_session = "[green]yes[/green]" if session.has_session else "[yellow]no[/yellow]"
                session_id = session.session_id[:12] + "..." if session.session_id and len(session.session_id) > 12 else session.session_id or "[dim]-[/dim]"
                copied_from = session.copied_from or "[dim]-[/dim]"
                updated = session.updated_at.strftime("%m/%d %H:%M")

                table.add_row(wt.name, has_session, session_id, copied_from, updated)
            elif show_all:
                table.add_row(wt.name, "[dim]-[/dim]", "[dim]-[/dim]", "[dim]-[/dim]", "[dim]-[/dim]")

        console.print()
        console.print(table)
        console.print()


@main.group("hooks")
def hooks_group() -> None:
    """Manage status change hooks.

    Hooks can execute shell commands, send notifications,
    or make webhook calls when AI tool status changes.
    """


@hooks_group.command("list")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def list_hooks(as_json: bool) -> None:
    """List all registered hooks.

    Example:
        owt hooks list
        owt hooks list --json
    """
    import json as json_module

    from open_orchestrator.core.hooks import HookService

    hook_service = HookService()
    hooks = hook_service.get_all_hooks()

    if as_json:
        data = [h.model_dump(mode="json") for h in hooks]
        console.print(json_module.dumps(data, indent=2))
        return

    if not hooks:
        console.print("[yellow]No hooks registered.[/yellow]")
        console.print()
        console.print("[dim]Create default hooks with: owt hooks init[/dim]")
        console.print("[dim]Add a hook with: owt hooks add <name> --type <type> --command <cmd>[/dim]")
        return

    table = Table(title="Status Change Hooks", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Action")
    table.add_column("Enabled", justify="center")
    table.add_column("Filter")

    for hook in hooks:
        enabled = "[green]yes[/green]" if hook.enabled else "[dim]no[/dim]"
        filter_info = []
        if hook.filter_worktrees:
            filter_info.append(f"wt:{len(hook.filter_worktrees)}")
        if hook.filter_statuses:
            filter_info.append(f"st:{len(hook.filter_statuses)}")
        filter_str = ", ".join(filter_info) if filter_info else "[dim]-[/dim]"

        table.add_row(
            hook.name,
            hook.hook_type,
            hook.action,
            enabled,
            filter_str,
        )

    console.print()
    console.print(table)
    console.print()


@hooks_group.command("add")
@click.argument("name")
@click.option(
    "--type",
    "hook_type",
    type=click.Choice([
        "on_status_changed",
        "on_task_started",
        "on_task_completed",
        "on_blocked",
        "on_error",
        "on_idle",
    ]),
    required=True,
    help="When to trigger the hook.",
)
@click.option(
    "--action",
    type=click.Choice(["shell", "notification", "webhook", "log"]),
    default="shell",
    help="Action to perform (default: shell).",
)
@click.option(
    "--command",
    "command",
    help="Shell command to execute (for shell action).",
)
@click.option(
    "--webhook-url",
    help="URL to POST to (for webhook action).",
)
@click.option(
    "--title",
    help="Notification title (for notification action).",
)
@click.option(
    "--message",
    help="Notification message template.",
)
@click.option(
    "--timeout",
    type=int,
    default=30,
    help="Timeout in seconds (default: 30).",
)
@click.option(
    "--disabled",
    is_flag=True,
    help="Create hook in disabled state.",
)
def add_hook(
    name: str,
    hook_type: str,
    action: str,
    command: str | None,
    webhook_url: str | None,
    title: str | None,
    message: str | None,
    timeout: int,
    disabled: bool,
) -> None:
    """Add a new status change hook.

    Hooks can execute commands, send notifications, or call webhooks
    when AI tool status changes.

    Template variables available:
    - {worktree}: Worktree name
    - {status}: New status
    - {task}: Current task description

    Example:
        owt hooks add notify-blocked --type on_blocked --action notification --title "Claude Blocked"
        owt hooks add log-changes --type on_status_changed --command "echo $OWT_WORKTREE changed to $OWT_STATUS"
        owt hooks add slack-notify --type on_task_completed --action webhook --webhook-url https://hooks.slack.com/...
    """
    from open_orchestrator.core.hooks import HookService
    from open_orchestrator.models.hooks import HookAction, HookConfig, HookType

    hook_service = HookService()

    # Validate required options based on action
    action_enum = HookAction(action)

    if action_enum == HookAction.SHELL_COMMAND and not command:
        raise click.ClickException("--command is required for shell action")

    if action_enum == HookAction.WEBHOOK and not webhook_url:
        raise click.ClickException("--webhook-url is required for webhook action")

    hook = HookConfig(
        name=name,
        enabled=not disabled,
        hook_type=HookType(hook_type),
        action=action_enum,
        command=command,
        webhook_url=webhook_url,
        notification_title=title,
        notification_message=message,
        timeout_seconds=timeout,
    )

    hook_service.register_hook(hook)
    console.print(f"[green]Hook '{name}' created successfully.[/green]")


@hooks_group.command("remove")
@click.argument("name")
def remove_hook(name: str) -> None:
    """Remove a hook by name.

    Example:
        owt hooks remove notify-blocked
    """
    from open_orchestrator.core.hooks import HookService

    hook_service = HookService()

    if hook_service.unregister_hook(name):
        console.print(f"[green]Hook '{name}' removed.[/green]")
    else:
        raise click.ClickException(f"Hook '{name}' not found")


@hooks_group.command("enable")
@click.argument("name")
def enable_hook(name: str) -> None:
    """Enable a hook.

    Example:
        owt hooks enable notify-blocked
    """
    from open_orchestrator.core.hooks import HookService

    hook_service = HookService()

    if hook_service.enable_hook(name):
        console.print(f"[green]Hook '{name}' enabled.[/green]")
    else:
        raise click.ClickException(f"Hook '{name}' not found")


@hooks_group.command("disable")
@click.argument("name")
def disable_hook(name: str) -> None:
    """Disable a hook.

    Example:
        owt hooks disable notify-blocked
    """
    from open_orchestrator.core.hooks import HookService

    hook_service = HookService()

    if hook_service.disable_hook(name):
        console.print(f"[green]Hook '{name}' disabled.[/green]")
    else:
        raise click.ClickException(f"Hook '{name}' not found")


@hooks_group.command("init")
def init_hooks() -> None:
    """Create default hooks for common scenarios.

    Creates the following hooks (disabled by default for logging):
    - notify-on-blocked: Notification when Claude is blocked
    - notify-on-completed: Notification when task is completed
    - notify-on-error: Notification on errors
    - log-status-changes: Log all status changes

    Example:
        owt hooks init
    """
    from open_orchestrator.core.hooks import HookService

    hook_service = HookService()
    hooks = hook_service.create_default_hooks()

    console.print("[green]Created default hooks:[/green]")
    for hook in hooks:
        status = "[green]enabled[/green]" if hook.enabled else "[dim]disabled[/dim]"
        console.print(f"  - {hook.name} ({hook.hook_type}) {status}")


@hooks_group.command("history")
@click.option(
    "-n",
    "--limit",
    type=int,
    default=20,
    help="Number of entries to show (default: 20).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def show_hook_history(limit: int, as_json: bool) -> None:
    """Show recent hook execution history.

    Example:
        owt hooks history
        owt hooks history -n 50
    """
    import json as json_module

    from open_orchestrator.core.hooks import HookService

    hook_service = HookService()
    history = hook_service.get_history(limit)

    if as_json:
        data = [h.model_dump(mode="json") for h in history]
        console.print(json_module.dumps(data, indent=2))
        return

    if not history:
        console.print("[yellow]No hook execution history.[/yellow]")
        return

    table = Table(title=f"Hook History (last {limit})", show_header=True, header_style="bold cyan")
    table.add_column("Time")
    table.add_column("Hook")
    table.add_column("Worktree")
    table.add_column("Status", justify="center")
    table.add_column("Duration")

    for result in reversed(history):  # Most recent first
        status = "[green]ok[/green]" if result.success else "[red]fail[/red]"
        time_str = result.executed_at.strftime("%H:%M:%S")
        duration = f"{result.duration_ms}ms"

        table.add_row(
            time_str,
            result.hook_name,
            result.worktree_name,
            status,
            duration,
        )

    console.print()
    console.print(table)
    console.print()


@hooks_group.command("test")
@click.argument("name")
@click.option(
    "--worktree",
    default="test-worktree",
    help="Worktree name to use in test context.",
)
def test_hook(name: str, worktree: str) -> None:
    """Test a hook by executing it with sample data.

    Example:
        owt hooks test notify-blocked
        owt hooks test slack-notify --worktree my-feature
    """
    from open_orchestrator.core.hooks import HookService

    hook_service = HookService()
    hook = hook_service.get_hook(name)

    if not hook:
        raise click.ClickException(f"Hook '{name}' not found")

    context = {
        "status": "blocked",
        "old_status": "working",
        "task": "Test task for hook verification",
    }

    console.print(f"[bold]Testing hook '{name}'...[/bold]")
    console.print()

    results = hook_service.trigger_hooks(hook.hook_type, worktree, context)

    if not results:
        console.print("[yellow]No hooks were triggered (hook may be disabled).[/yellow]")
        return

    for result in results:
        if result.success:
            console.print(f"[green]Success:[/green] {result.output or 'No output'}")
        else:
            console.print(f"[red]Failed:[/red] {result.error}")

        console.print(f"  Duration: {result.duration_ms}ms")


@main.group("pr")
def pr_group() -> None:
    """Manage GitHub PR associations with worktrees.

    Link worktrees to Pull Requests for PR-centric workflows,
    status tracking, and cleanup based on merged PRs.
    """


@pr_group.command("link")
@click.argument("identifier")
@click.argument("pr_number", type=int, required=False)
@click.option(
    "--no-check",
    is_flag=True,
    help="Don't check PR status (faster).",
)
def link_pr(identifier: str, pr_number: int | None, no_check: bool) -> None:
    """Link a worktree to a GitHub PR.

    IDENTIFIER is the worktree name, branch, or path.
    PR_NUMBER is optional; if not provided, attempts to detect from branch name.

    Example:
        owt pr link feature/auth 123        # Link to PR #123
        owt pr link feature/auth            # Auto-detect from branch
        owt pr link feature/auth-#456       # Auto-detect #456 from branch
    """
    from open_orchestrator.core.pr_linker import PRLinker

    wt_manager = get_worktree_manager()
    pr_linker = PRLinker()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    with console.status(f"[bold blue]Linking PR for '{worktree.name}'..."):
        result = pr_linker.link_pr(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            branch=worktree.branch,
            pr_number=pr_number,
            check_status=not no_check,
        )

    if result.success:
        console.print(f"[green]{result.message}[/green]")
        console.print(f"  URL: {result.pr_url}")
        if result.auto_detected:
            console.print(f"  [dim](auto-detected from branch name)[/dim]")
    else:
        raise click.ClickException(result.message)


@pr_group.command("unlink")
@click.argument("identifier")
def unlink_pr(identifier: str) -> None:
    """Remove PR link from a worktree.

    Example:
        owt pr unlink feature/auth
    """
    from open_orchestrator.core.pr_linker import PRLinker

    wt_manager = get_worktree_manager()
    pr_linker = PRLinker()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    if pr_linker.unlink_pr(worktree.name):
        console.print(f"[green]Removed PR link for '{worktree.name}'[/green]")
    else:
        console.print(f"[yellow]No PR link found for '{worktree.name}'[/yellow]")


@pr_group.command("info")
@click.argument("identifier")
@click.option(
    "--refresh",
    is_flag=True,
    help="Refresh PR status from GitHub.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def show_pr_info(identifier: str, refresh: bool, as_json: bool) -> None:
    """Show PR information for a worktree.

    Example:
        owt pr info feature/auth
        owt pr info feature/auth --refresh
    """
    import json as json_module

    from open_orchestrator.core.pr_linker import PRLinker

    wt_manager = get_worktree_manager()
    pr_linker = PRLinker()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    if refresh:
        with console.status("[bold blue]Refreshing PR status..."):
            pr_info = pr_linker.refresh_pr_status(worktree.name)
    else:
        pr_info = pr_linker.get_pr(worktree.name)

    if not pr_info:
        console.print(f"[yellow]No PR linked to '{worktree.name}'[/yellow]")
        console.print("[dim]Link a PR with: owt pr link <worktree> <pr-number>[/dim]")
        return

    if as_json:
        console.print(json_module.dumps(pr_info.model_dump(mode="json"), indent=2))
        return

    status_style = {
        "open": "[green]open[/green]",
        "closed": "[red]closed[/red]",
        "merged": "[blue]merged[/blue]",
        "draft": "[yellow]draft[/yellow]",
        "unknown": "[dim]unknown[/dim]",
    }.get(pr_info.status, pr_info.status)

    console.print()
    console.print(f"[bold]PR #{pr_info.pr_number}[/bold]")
    console.print()
    console.print(f"[bold]Repository:[/bold] {pr_info.full_repo}")
    console.print(f"[bold]Branch:[/bold]     {pr_info.branch}")
    console.print(f"[bold]Status:[/bold]     {status_style}")
    if pr_info.title:
        console.print(f"[bold]Title:[/bold]      {pr_info.title}")
    console.print(f"[bold]URL:[/bold]        {pr_info.pr_url}")
    console.print(f"[bold]Linked:[/bold]     {pr_info.linked_at.strftime('%Y-%m-%d %H:%M')}")

    if pr_info.last_checked:
        console.print(f"[bold]Checked:[/bold]    {pr_info.last_checked.strftime('%Y-%m-%d %H:%M')}")


@pr_group.command("list")
@click.option(
    "--status",
    type=click.Choice(["open", "closed", "merged", "draft", "all"]),
    default="all",
    help="Filter by PR status.",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Refresh all PR statuses from GitHub.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def list_prs(status: str, refresh: bool, as_json: bool) -> None:
    """List all PR-linked worktrees.

    Example:
        owt pr list
        owt pr list --status open
        owt pr list --refresh
    """
    import json as json_module

    from open_orchestrator.core.pr_linker import PRLinker
    from open_orchestrator.models.pr_info import PRStatus

    pr_linker = PRLinker()

    if refresh:
        with console.status("[bold blue]Refreshing PR statuses..."):
            pr_linker.refresh_all_statuses()

    prs = pr_linker.get_all_prs()

    # Filter by status
    if status != "all":
        status_enum = PRStatus(status)
        prs = [p for p in prs if p.status == status_enum]

    if as_json:
        data = [p.model_dump(mode="json") for p in prs]
        console.print(json_module.dumps(data, indent=2))
        return

    if not prs:
        console.print("[yellow]No PR links found.[/yellow]")
        console.print("[dim]Link a PR with: owt pr link <worktree> <pr-number>[/dim]")
        return

    table = Table(title="PR-Linked Worktrees", show_header=True, header_style="bold cyan")
    table.add_column("Worktree", style="bold")
    table.add_column("PR")
    table.add_column("Status", justify="center")
    table.add_column("Title")
    table.add_column("Branch")

    for pr in prs:
        status_style = {
            "open": "[green]open[/green]",
            "closed": "[red]closed[/red]",
            "merged": "[blue]merged[/blue]",
            "draft": "[yellow]draft[/yellow]",
            "unknown": "[dim]?[/dim]",
        }.get(pr.status, pr.status)

        title_display = pr.title[:30] + "..." if pr.title and len(pr.title) > 30 else pr.title or "[dim]-[/dim]"

        table.add_row(
            pr.worktree_name,
            f"#{pr.pr_number}",
            status_style,
            title_display,
            pr.branch,
        )

    console.print()
    console.print(table)
    console.print()

    # Summary
    open_count = len([p for p in prs if p.is_open])
    merged_count = len([p for p in prs if p.is_merged])
    console.print(f"[dim]Total: {len(prs)} | Open: {open_count} | Merged: {merged_count}[/dim]")


@pr_group.command("open")
@click.argument("identifier")
def open_pr_browser(identifier: str) -> None:
    """Open the linked PR in a web browser.

    Example:
        owt pr open feature/auth
    """
    from open_orchestrator.core.pr_linker import PRLinker

    wt_manager = get_worktree_manager()
    pr_linker = PRLinker()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    pr_info = pr_linker.get_pr(worktree.name)

    if not pr_info:
        raise click.ClickException(f"No PR linked to '{worktree.name}'")

    if pr_linker.open_pr_in_browser(worktree.name):
        console.print(f"[green]Opened PR #{pr_info.pr_number} in browser[/green]")
    else:
        console.print(f"[yellow]Could not open browser. URL: {pr_info.pr_url}[/yellow]")


@pr_group.command("cleanup")
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    help="Show what would be deleted without actually deleting (default: dry-run).",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def cleanup_merged_prs(dry_run: bool, yes: bool) -> None:
    """Clean up worktrees with merged PRs.

    Lists worktrees whose PRs have been merged and offers to delete them.

    Example:
        owt pr cleanup             # Dry run
        owt pr cleanup --no-dry-run -y
    """
    from open_orchestrator.core.pr_linker import PRLinker

    wt_manager = get_worktree_manager()
    pr_linker = PRLinker()

    # Refresh statuses first
    with console.status("[bold blue]Checking PR statuses..."):
        pr_linker.refresh_all_statuses()

    merged = pr_linker.get_merged_prs()

    if not merged:
        console.print("[green]No worktrees with merged PRs found.[/green]")
        return

    console.print()
    console.print(f"[bold]Found {len(merged)} worktree(s) with merged PRs:[/bold]")
    console.print()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Worktree")
    table.add_column("PR")
    table.add_column("Title")
    table.add_column("Merged")

    for pr in merged:
        title = pr.title[:40] + "..." if pr.title and len(pr.title) > 40 else pr.title or "-"

        table.add_row(
            pr.worktree_name,
            f"#{pr.pr_number}",
            title,
            "[blue]merged[/blue]",
        )

    console.print(table)
    console.print()

    if dry_run:
        console.print("[blue]This is a dry run. No worktrees will be deleted.[/blue]")
        console.print("[dim]Run with --no-dry-run to actually delete these worktrees.[/dim]")
        return

    if not yes and not click.confirm("Delete these worktrees?"):
        console.print("[yellow]Aborted.[/yellow]")
        return

    deleted = 0
    errors = []

    for pr in merged:
        try:
            wt_manager.delete(pr.worktree_name, force=False)
            pr_linker.unlink_pr(pr.worktree_name)
            deleted += 1
            console.print(f"[green]Deleted:[/green] {pr.worktree_name}")
        except WorktreeError as e:
            errors.append(f"{pr.worktree_name}: {e}")

    console.print()
    console.print(f"[bold green]Deleted {deleted} worktree(s)[/bold green]")

    if errors:
        console.print()
        console.print("[bold red]Errors:[/bold red]")
        for error in errors:
            console.print(f"  [red]{error}[/red]")


# =============================================================================
# Process Management Commands (no-tmux mode)
# =============================================================================


@main.group("process")
def process_group() -> None:
    """Manage AI tool processes without tmux.

    Alternative to tmux-based session management for users who prefer
    simpler process handling or don't have tmux installed.
    """


@process_group.command("start")
@click.argument("worktree_name")
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid"]),
    default="claude",
    help="AI coding tool to start (default: claude).",
)
@click.option(
    "--plan-mode",
    is_flag=True,
    help="Start Claude in plan mode.",
)
@click.option(
    "--droid-auto",
    type=click.Choice(["low", "medium", "high"]),
    default=None,
    help="Droid auto mode level.",
)
def process_start(
    worktree_name: str,
    ai_tool: str,
    plan_mode: bool,
    droid_auto: str | None,
) -> None:
    """Start an AI tool process for a worktree.

    Starts the AI tool as a background process without tmux.
    Output is logged to ~/.cache/open-orchestrator/logs/
    """
    from open_orchestrator.core.process_manager import (
        ProcessAlreadyRunningError,
        ProcessError,
        ProcessManager,
    )

    wt_manager = get_worktree_manager()
    process_manager = ProcessManager()

    try:
        worktree = wt_manager.get(worktree_name)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    ai_tool_enum = AITool(ai_tool)
    droid_auto_enum = DroidAutoLevel(droid_auto) if droid_auto else None

    try:
        proc_info = process_manager.start_ai_tool(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            ai_tool=ai_tool_enum,
            plan_mode=plan_mode,
            droid_auto=droid_auto_enum,
        )

        console.print(f"[bold green]Started {ai_tool} for {worktree_name}[/bold green]")
        console.print(f"  PID: {proc_info.pid}")
        if proc_info.log_file:
            console.print(f"  Log: {proc_info.log_file}")

    except ProcessAlreadyRunningError as e:
        raise click.ClickException(str(e)) from e
    except ProcessError as e:
        raise click.ClickException(str(e)) from e


@process_group.command("stop")
@click.argument("worktree_name")
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force kill (SIGKILL instead of SIGTERM).",
)
def process_stop(worktree_name: str, force: bool) -> None:
    """Stop an AI tool process for a worktree."""
    from open_orchestrator.core.process_manager import (
        ProcessError,
        ProcessManager,
        ProcessNotFoundError,
    )

    process_manager = ProcessManager()

    try:
        stopped = process_manager.stop_ai_tool(worktree_name, force=force)
        if stopped:
            console.print(f"[green]Stopped process for {worktree_name}[/green]")
        else:
            console.print(f"[yellow]Process was already stopped[/yellow]")
    except ProcessNotFoundError as e:
        raise click.ClickException(str(e)) from e
    except ProcessError as e:
        raise click.ClickException(str(e)) from e


@process_group.command("list")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output in JSON format.",
)
def process_list(json_output: bool) -> None:
    """List all running AI tool processes."""
    from open_orchestrator.core.process_manager import ProcessManager

    process_manager = ProcessManager()
    processes = process_manager.list_processes()

    if json_output:
        import json

        output = [p.model_dump() for p in processes]
        click.echo(json.dumps(output, default=str))
        return

    if not processes:
        console.print("[yellow]No AI tool processes running.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Worktree")
    table.add_column("AI Tool")
    table.add_column("PID")
    table.add_column("Started")

    for proc in processes:
        started = proc.started_at.strftime("%Y-%m-%d %H:%M")
        table.add_row(
            proc.worktree_name,
            proc.ai_tool,
            str(proc.pid),
            started,
        )

    console.print(table)


@process_group.command("logs")
@click.argument("worktree_name")
@click.option(
    "-f",
    "--follow",
    is_flag=True,
    help="Follow log output (like tail -f).",
)
@click.option(
    "-n",
    "--lines",
    default=50,
    help="Number of lines to show (default: 50).",
)
def process_logs(worktree_name: str, follow: bool, lines: int) -> None:
    """View logs for an AI tool process."""
    from open_orchestrator.core.process_manager import ProcessManager

    process_manager = ProcessManager()
    log_path = process_manager.get_log_path(worktree_name)

    if not log_path:
        raise click.ClickException(f"No log file found for {worktree_name}")

    if not log_path.exists():
        raise click.ClickException(f"Log file not found: {log_path}")

    if follow:
        # Use tail -f for following
        import subprocess

        try:
            subprocess.run(["tail", "-f", str(log_path)])
        except KeyboardInterrupt:
            pass
    else:
        # Show last N lines
        content = log_path.read_text()
        log_lines = content.splitlines()
        for line in log_lines[-lines:]:
            console.print(line)


# =============================================================================
# Token Usage Commands
# =============================================================================


@main.group("tokens")
def tokens_group() -> None:
    """Manage and view token usage across worktrees.

    Track Claude/AI tool token consumption for cost monitoring.
    """


@tokens_group.command("show")
@click.argument("worktree_name", required=False)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output in JSON format.",
)
def tokens_show(worktree_name: str | None, json_output: bool) -> None:
    """Show token usage for a worktree or all worktrees.

    Without WORKTREE_NAME, shows summary for all worktrees.
    """
    status_tracker = StatusTracker()
    wt_manager = get_worktree_manager()

    worktrees = wt_manager.list_all()
    worktree_names = [wt.name for wt in worktrees]

    if worktree_name:
        wt_status = status_tracker.get_status(worktree_name)
        if not wt_status:
            raise click.ClickException(f"No status found for {worktree_name}")

        if json_output:
            click.echo(json.dumps(wt_status.token_usage.model_dump(), default=str))
            return

        tokens = wt_status.token_usage
        console.print(f"[bold]Token usage for '{worktree_name}'[/bold]")
        console.print()
        console.print(f"  Total tokens: {tokens.total_tokens:,}")
        console.print(f"  Input:        {tokens.input_tokens:,}")
        console.print(f"  Output:       {tokens.output_tokens:,}")
        if tokens.cache_read_tokens > 0 or tokens.cache_write_tokens > 0:
            console.print(f"  Cache read:   {tokens.cache_read_tokens:,}")
            console.print(f"  Cache write:  {tokens.cache_write_tokens:,}")
        console.print()
        console.print(f"  Estimated cost: ${tokens.estimated_cost_usd:.4f}")
        console.print(f"  Last updated: {tokens.last_updated.strftime('%Y-%m-%d %H:%M')}")
    else:
        summary = status_tracker.get_summary(worktree_names)

        if json_output:
            output = {
                "total_input_tokens": summary.total_input_tokens,
                "total_output_tokens": summary.total_output_tokens,
                "total_estimated_cost_usd": summary.total_estimated_cost_usd,
                "worktrees": [
                    {
                        "name": s.worktree_name,
                        "input_tokens": s.token_usage.input_tokens,
                        "output_tokens": s.token_usage.output_tokens,
                        "estimated_cost_usd": s.token_usage.estimated_cost_usd,
                    }
                    for s in summary.statuses
                ],
            }
            click.echo(json.dumps(output, default=str))
            return

        console.print("[bold]Token Usage Across Worktrees[/bold]")
        console.print()

        if not summary.statuses:
            console.print("[yellow]No worktrees with status tracking.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Worktree")
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Est. Cost", justify="right")

        for wt_status in summary.statuses:
            tokens = wt_status.token_usage
            table.add_row(
                wt_status.worktree_name,
                f"{tokens.input_tokens:,}",
                f"{tokens.output_tokens:,}",
                f"{tokens.total_tokens:,}",
                f"${tokens.estimated_cost_usd:.4f}",
            )

        console.print(table)
        console.print()
        total = summary.total_input_tokens + summary.total_output_tokens
        console.print(f"[bold]Total:[/bold] {total:,} tokens")
        console.print(f"[bold]Estimated cost:[/bold] ${summary.total_estimated_cost_usd:.4f}")


@tokens_group.command("update")
@click.argument("worktree_name")
@click.option(
    "--input",
    "input_tokens",
    type=int,
    default=0,
    help="Input tokens to add.",
)
@click.option(
    "--output",
    "output_tokens",
    type=int,
    default=0,
    help="Output tokens to add.",
)
@click.option(
    "--cache-read",
    type=int,
    default=0,
    help="Cache read tokens to add.",
)
@click.option(
    "--cache-write",
    type=int,
    default=0,
    help="Cache write tokens to add.",
)
def tokens_update(
    worktree_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
) -> None:
    """Manually update token usage for a worktree.

    Use this to track token usage when parsing Claude output.
    Tokens are added to existing counts.
    """
    status_tracker = StatusTracker()

    result = status_tracker.update_token_usage(
        worktree_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )

    if not result:
        raise click.ClickException(f"No status found for {worktree_name}")

    console.print(f"[green]Token usage updated for {worktree_name}[/green]")
    console.print(f"  Added: +{input_tokens} input, +{output_tokens} output")
    console.print(f"  Total: {result.token_usage.total_tokens:,} tokens")


@tokens_group.command("reset")
@click.argument("worktree_name")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation.",
)
def tokens_reset(worktree_name: str, yes: bool) -> None:
    """Reset token usage to zero for a worktree."""
    status_tracker = StatusTracker()

    wt_status = status_tracker.get_status(worktree_name)
    if not wt_status:
        raise click.ClickException(f"No status found for {worktree_name}")

    if not yes:
        current = wt_status.token_usage.total_tokens
        if not click.confirm(f"Reset {current:,} tokens for {worktree_name}?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    status_tracker.reset_token_usage(worktree_name)
    console.print(f"[green]Token usage reset for {worktree_name}[/green]")


# =============================================================================
# Dashboard Command
# =============================================================================


@main.command("dashboard")
@click.option(
    "-r",
    "--refresh",
    type=float,
    default=2.0,
    help="Refresh rate in seconds (default: 2.0).",
)
@click.option(
    "--no-tokens",
    is_flag=True,
    help="Hide token usage columns.",
)
@click.option(
    "--no-commands",
    is_flag=True,
    help="Hide command count column.",
)
@click.option(
    "-c",
    "--compact",
    is_flag=True,
    help="Compact mode (no summary panel).",
)
def dashboard(
    refresh: float,
    no_tokens: bool,
    no_commands: bool,
    compact: bool,
) -> None:
    """Launch live dashboard to monitor all worktrees.

    Shows real-time status of AI tools across all worktrees with
    automatic updates. Press Ctrl+C to exit.

    Example:
        owt dashboard              # Default 2 second refresh
        owt dashboard -r 1         # 1 second refresh
        owt dashboard --compact    # Compact mode
    """
    from open_orchestrator.core.dashboard import Dashboard, DashboardConfig

    config = DashboardConfig(
        refresh_rate=refresh,
        show_token_usage=not no_tokens,
        show_commands=not no_commands,
        compact=compact,
    )

    dash = Dashboard(config=config)
    dash.run()


if __name__ == "__main__":
    main()
