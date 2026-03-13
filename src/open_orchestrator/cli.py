"""CLI entry point for Open Orchestrator."""

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from open_orchestrator.config import AITool, DroidAutoLevel, load_config
from open_orchestrator.core.ab_launcher import ABLauncher, ABLauncherError, ToolNotInstalledError
from open_orchestrator.core.environment import (
    EnvironmentSetup,
    EnvironmentSetupError,
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
from open_orchestrator.core.workspace import (
    WorkspaceFullError,
    WorkspaceManager,
    WorkspaceNotFoundError,
)
from open_orchestrator.core.worktree import (
    NotAGitRepositoryError,
    WorktreeAlreadyExistsError,
    WorktreeError,
    WorktreeManager,
    WorktreeNotFoundError,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus
from open_orchestrator.models.workspace import WorkspaceLayout
from open_orchestrator.models.worktree_info import WorktreeInfo

if TYPE_CHECKING:
    from open_orchestrator.models.status import HealthReport

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


def is_interactive_terminal() -> bool:
    """
    Check if the current terminal is interactive.

    Uses multiple checks for robust detection:
    - sys.stdout.isatty() - Standard TTY check
    - TERM environment variable - Must not be 'dumb' or empty
    - CI environment variables - Detects CI/CD environments (CI, GITHUB_ACTIONS, etc.)

    Returns:
        True if running in an interactive terminal, False otherwise
    """
    # Check if stdout is a TTY
    if not sys.stdout.isatty():
        return False

    # Check TERM environment variable
    term = os.environ.get("TERM", "")
    if not term or term == "dumb":
        return False

    # Check for CI/CD environments
    ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS", "TRAVIS", "CIRCLECI"]
    for var in ci_vars:
        if os.environ.get(var):
            return False

    return True


@click.group(invoke_without_command=True)
@click.version_option(package_name="open-orchestrator")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Open Orchestrator - Git Worktree + AI agent orchestration tool.

    \b
    Common Commands:
      new (n)      Create worktree from task description
      list (ls)    List all worktrees
      status (st)  Show AI activity across worktrees
      merge (m)    Merge worktree branch and clean up
      close (x)    Remove pane + delete worktree atomically
      create       Create worktree from branch name (power-user)
      delete (rm)  Delete a worktree

    \b
    Advanced:
      send         Send command to a worktree's AI agent
      sync         Sync worktrees with upstream
      dashboard    Launch live TUI dashboard
      agent        Manage autonomous agents
      pane         On-demand pane management
      workspace    Workspace management
      hooks        Status change hooks
      pr           GitHub PR linking
      process      Non-tmux process management
      tokens       Token usage tracking
      tmux         Direct tmux session management

    Run without arguments for interactive TUI mode.
    """
    # Only handle no-subcommand case when invoked without a subcommand
    if ctx.invoked_subcommand is None:
        if is_interactive_terminal():
            # Launch TUI mode
            console.print("[cyan]Launching interactive TUI mode...[/cyan]")
            try:
                from open_orchestrator.tui import launch_tui

                launch_tui()
            except ImportError as e:
                console.print(
                    f"[yellow]Warning: Could not import TUI module: {e}[/yellow]"
                )
                console.print(
                    "[yellow]Falling back to help output.[/yellow]"
                )
                click.echo(ctx.get_help())
            except Exception as e:
                console.print(
                    f"[yellow]Warning: TUI initialization failed: {e}[/yellow]"
                )
                console.print(
                    "[yellow]Falling back to help output.[/yellow]"
                )
                click.echo(ctx.get_help())
        else:
            # Non-interactive terminal - show help
            console.print(
                "[yellow]Non-interactive terminal detected. "
                "Showing help output.[/yellow]"
            )
            click.echo(ctx.get_help())


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


# =============================================================================
# Skill Management Commands
# =============================================================================


@main.group("skill")
def skill_group() -> None:
    """Manage Claude Code skill installation.

    Install, uninstall, and check the status of the Open Orchestrator
    skill for Claude Code.
    """


@skill_group.command("install")
@click.option(
    "--symlink/--copy",
    default=True,
    help="Create symlink (default) or copy the skill file.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Overwrite existing skill installation.",
)
def skill_install(symlink: bool, force: bool) -> None:
    """Install the Open Orchestrator skill to ~/.claude/skills/.

    By default, creates a symlink to the skill file in the package,
    which automatically stays up-to-date when you upgrade the package.

    Use --copy to create an independent copy instead.

    Example:
        owt skill install              # Create symlink (recommended)
        owt skill install --copy       # Create copy
        owt skill install --force      # Overwrite existing
    """
    from open_orchestrator.core.skill_installer import (
        SkillInstaller,
        SkillInstallError,
        SkillNotFoundError,
    )

    installer = SkillInstaller()

    try:
        target_path = installer.install(symlink=symlink, force=force)
        console.print()
        console.print(f"[green]✓[/green] Created {installer.target_dir}/")

        if symlink:
            source_path = installer.get_source_path()
            console.print(f"[green]✓[/green] Linked {installer.SKILL_FILE} → {source_path}")
        else:
            console.print(f"[green]✓[/green] Copied {installer.SKILL_FILE} to {target_path}")

        console.print("[green]✓[/green] Skill installed successfully!")
        console.print()
        console.print("[dim]Restart Claude Code to use the skill.[/dim]")

    except SkillNotFoundError as e:
        raise click.ClickException(str(e)) from e
    except SkillInstallError as e:
        raise click.ClickException(str(e)) from e


@skill_group.command("uninstall")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def skill_uninstall(yes: bool) -> None:
    """Remove the Open Orchestrator skill from ~/.claude/skills/.

    Example:
        owt skill uninstall
        owt skill uninstall -y
    """
    from open_orchestrator.core.skill_installer import SkillInstaller, SkillInstallError

    installer = SkillInstaller()

    if not installer.is_installed():
        console.print("[yellow]Skill is not installed.[/yellow]")
        return

    if not yes:
        console.print(f"[bold]About to remove:[/bold] {installer.target_file}")
        console.print()
        if not click.confirm("Are you sure?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    try:
        installer.uninstall()
        console.print()
        console.print(f"[green]✓[/green] Removed {installer.target_dir}/")
        console.print("[green]✓[/green] Skill uninstalled successfully!")

    except SkillInstallError as e:
        raise click.ClickException(str(e)) from e


@skill_group.command("status")
def skill_status() -> None:
    """Check if skill is installed and up-to-date.

    Shows installation status, location, and whether the skill
    matches the current package version.

    Example:
        owt skill status
    """
    from open_orchestrator.core.skill_installer import SkillInstaller, SkillNotFoundError

    installer = SkillInstaller()

    console.print()
    console.print("[bold]Open Orchestrator Skill[/bold]")
    console.print()

    if not installer.is_installed():
        console.print("  [bold]Status:[/bold]   [yellow]Not installed[/yellow]")
        console.print()
        console.print("[dim]Install with: owt skill install[/dim]")
        return

    install_type = "symlink" if installer.is_symlink() else "copy"
    console.print(f"  [bold]Status:[/bold]   [green]Installed[/green] ({install_type})")

    try:
        source_path = installer.get_source_path()
        console.print(f"  [bold]Source:[/bold]   {source_path}")
    except SkillNotFoundError:
        console.print("  [bold]Source:[/bold]   [red]Not found in package[/red]")

    console.print(f"  [bold]Target:[/bold]   {installer.target_file}")

    if installer.is_symlink():
        symlink_target = installer.get_symlink_target()
        if symlink_target:
            console.print(f"  [bold]Links to:[/bold] {symlink_target}")

    try:
        up_to_date = installer.is_up_to_date()
        if up_to_date:
            console.print("  [bold]Up-to-date:[/bold] [green]✓[/green]")
        else:
            console.print("  [bold]Up-to-date:[/bold] [yellow]✗ (run 'owt skill install --force' to update)[/yellow]")
    except SkillNotFoundError:
        console.print("  [bold]Up-to-date:[/bold] [red]Cannot verify (source missing)[/red]")


@main.group("template")
def template_group() -> None:
    """Manage worktree templates for common workflows.

    Templates provide pre-configured settings for different types of work:
    bugfix, feature, research, security-audit, refactor, hotfix, experiment, docs.
    """


@template_group.command("list")
@click.option(
    "--tags",
    help="Filter templates by tags (comma-separated)",
)
@click.option(
    "--builtin-only",
    is_flag=True,
    help="Show only built-in templates",
)
def template_list(tags: str | None, builtin_only: bool) -> None:
    """List all available worktree templates."""
    from open_orchestrator.config import get_builtin_templates, list_all_templates, load_config

    config = load_config()

    if builtin_only:
        templates = get_builtin_templates()
    else:
        templates = list_all_templates(config)

    if not templates:
        console.print("[yellow]No templates available[/yellow]")
        return

    # Filter by tags if specified
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        templates = {name: tmpl for name, tmpl in templates.items() if any(tag in tmpl.tags for tag in tag_list)}

    if not templates:
        console.print(f"[yellow]No templates found with tags: {tags}[/yellow]")
        return

    table = Table(title="Worktree Templates", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("AI Tool", style="magenta")
    table.add_column("Tags", style="dim")

    for name, template in sorted(templates.items()):
        ai_tool = template.ai_tool.value if template.ai_tool else "default"
        tags_str = ", ".join(template.tags) if template.tags else "-"
        table.add_row(name, template.description, ai_tool, tags_str)

    console.print(table)
    console.print("\n[dim]Use 'owt template show <name>' to see template details[/dim]")


@template_group.command("show")
@click.argument("name")
def template_show(name: str) -> None:
    """Show detailed information about a template."""
    from open_orchestrator.config import load_config

    config = load_config()
    template = config.get_template(name)

    if not template:
        console.print(f"[red]✗[/red] Template not found: {name}")
        console.print("\nAvailable templates:")
        console.print("  Run 'owt template list' to see all templates")
        raise click.Abort()

    console.print(f"\n[bold cyan]{template.name}[/bold cyan]")
    console.print(f"[dim]{template.description}[/dim]\n")

    console.print("[bold]Configuration:[/bold]")
    if template.base_branch:
        console.print(f"  Base branch: {template.base_branch}")
    if template.ai_tool:
        console.print(f"  AI tool: {template.ai_tool.value}")
    if template.tmux_layout:
        console.print(f"  tmux layout: {template.tmux_layout}")
    if template.plan_mode:
        console.print("  Plan mode: [green]enabled[/green]")
    if template.install_deps is not None:
        status = "[green]yes[/green]" if template.install_deps else "[red]no[/red]"
        console.print(f"  Install dependencies: {status}")

    if template.ai_instructions:
        console.print("\n[bold]AI Instructions:[/bold]")
        console.print(f"[dim]{template.ai_instructions}[/dim]")

    if template.auto_commands:
        console.print("\n[bold]Auto Commands:[/bold]")
        for cmd in template.auto_commands:
            console.print(f"  • {cmd}")

    if template.tags:
        console.print(f"\n[bold]Tags:[/bold] {', '.join(template.tags)}")

    console.print(f"\n[dim]Usage: owt create <branch> --template {name}[/dim]")


@main.group("workspace")
def workspace_group() -> None:
    """Manage unified workspaces for multi-pane development.

    Workspaces allow you to see multiple worktrees in a single tmux session
    with split panes, similar to Claude Code Agent Teams.
    """


@workspace_group.command("list")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def workspace_list(output_json: bool) -> None:
    """List all workspaces."""
    workspace_manager = WorkspaceManager()
    workspaces = workspace_manager.list_workspaces()

    if output_json:
        import json

        data = [
            {
                "name": ws.name,
                "layout": ws.layout.value,
                "panes": len(ws.panes),
                "max_panes": ws.max_panes,
                "available": ws.available_panes,
                "worktrees": [p.worktree_name for p in ws.panes if p.worktree_name],
            }
            for ws in workspaces
        ]
        console.print(json.dumps(data, indent=2))
        return

    if not workspaces:
        console.print("[dim]No workspaces found.[/dim]")
        console.print("\nCreate a worktree to automatically create a workspace:")
        console.print("  owt create feature/new-feature")
        return

    table = Table(title="Workspaces")
    table.add_column("Name", style="cyan")
    table.add_column("Layout", style="blue")
    table.add_column("Panes", justify="center")
    table.add_column("Available", justify="center")
    table.add_column("Worktrees")

    for ws in workspaces:
        worktree_names = [p.worktree_name for p in ws.panes if p.worktree_name]
        worktrees_str = ", ".join(worktree_names) if worktree_names else "[dim]main only[/dim]"

        panes_str = f"{len(ws.panes)} / {ws.max_panes}"
        available_str = f"[green]{ws.available_panes}[/green]" if ws.available_panes > 0 else "[red]0 (full)[/red]"

        table.add_row(ws.name, ws.layout.value, panes_str, available_str, worktrees_str)

    console.print(table)


@workspace_group.command("show")
@click.argument("name")
def workspace_show(name: str) -> None:
    """Show detailed workspace information."""
    workspace_manager = WorkspaceManager()

    try:
        workspace = workspace_manager.get_workspace(name)
    except WorkspaceNotFoundError:
        console.print(f"[red]✗[/red] Workspace not found: {name}")
        console.print("\nAvailable workspaces:")
        console.print("  Run 'owt workspace list' to see all workspaces")
        raise click.Abort()

    console.print(f"\n[bold cyan]{workspace.name}[/bold cyan]")
    console.print(f"Layout: {workspace.layout.value}")
    console.print(f"Capacity: {len(workspace.panes)} / {workspace.max_panes} panes")

    if workspace.panes:
        console.print("\n[bold]Panes:[/bold]")
        for pane in workspace.panes:
            if pane.is_main:
                console.print(f"  [{pane.pane_index}] [cyan]main[/cyan] (orchestration center)")
            else:
                console.print(f"  [{pane.pane_index}] {pane.worktree_name} [dim]({pane.worktree_path})[/dim]")

    console.print(f"\n[dim]Attach: tmux attach -t {workspace.name}[/dim]")


@workspace_group.command("attach")
@click.argument("name")
def workspace_attach(name: str) -> None:
    """Attach to a workspace tmux session."""
    workspace_manager = WorkspaceManager()

    try:
        workspace = workspace_manager.get_workspace(name)
    except WorkspaceNotFoundError:
        console.print(f"[red]✗[/red] Workspace not found: {name}")
        raise click.Abort()

    import subprocess

    try:
        subprocess.run(["tmux", "attach", "-t", workspace.name], check=True)
    except subprocess.CalledProcessError:
        console.print(f"[red]✗[/red] Failed to attach to workspace: {workspace.name}")
        console.print("[dim]The tmux session may no longer exist.[/dim]")
        raise click.Abort()


@workspace_group.command("destroy")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation")
def workspace_destroy(name: str, yes: bool) -> None:
    """Destroy a workspace and its tmux session.

    WARNING: This will close all panes and end all AI sessions in the workspace.
    Worktrees will not be deleted, only the workspace view.
    """
    workspace_manager = WorkspaceManager()

    try:
        workspace = workspace_manager.get_workspace(name)
    except WorkspaceNotFoundError:
        console.print(f"[red]✗[/red] Workspace not found: {name}")
        raise click.Abort()

    # Confirm destruction
    if not yes:
        worktree_count = len([p for p in workspace.panes if not p.is_main])
        console.print(f"\n[bold yellow]Warning:[/bold yellow] This will destroy workspace '{name}'")
        console.print(f"This workspace contains {worktree_count} worktree pane(s).")
        console.print("\n[dim]Worktrees will NOT be deleted, only the workspace view.[/dim]")

        if not click.confirm("Are you sure you want to continue?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    # Clean up processes for all panes in workspace
    from open_orchestrator.core.process_manager import ProcessManager

    try:
        process_manager = ProcessManager()
        for pane in workspace.panes:
            if pane.worktree_name and process_manager.has_process(pane.worktree_name):
                console.print(f"[yellow]Stopping AI process for {pane.worktree_name}...[/yellow]")
                process_manager.stop_ai_tool(pane.worktree_name, force=False)
                console.print(f"[green]✓[/green] Stopped AI process for {pane.worktree_name}")
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] Could not stop all AI processes: {e}")

    # Kill tmux session
    tmux_manager = TmuxManager()
    try:
        tmux_manager.kill_session(workspace.name)
        console.print(f"[green]✓[/green] Killed tmux session: {workspace.name}")
    except TmuxError as e:
        console.print(f"[yellow]Warning:[/yellow] Could not kill tmux session: {e}")

    # Remove workspace from store
    workspace_manager.delete_workspace(name)
    console.print(f"[green]✓[/green] Workspace destroyed: {name}")


# ── Pane commands (on-demand workspace mode) ─────────────────────────────


@main.group("pane")
def pane_group() -> None:
    """On-demand pane management for workspace mode.

    Add or remove worktree panes dynamically. These commands are used by the
    tmux keybindings (prefix+n, prefix+X) and can also be called directly.
    """


@pane_group.command("add")
@click.option("--from-popup", "popup_file", type=click.Path(exists=True), help="JSON file from owt-popup picker.")
@click.option("--branch", "-b", help="Branch name for the new worktree.")
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid"]),
    default="claude",
    help="AI tool to start in the pane.",
)
@click.option("--template", "template_name", help="Worktree template to apply.")
@click.option("--workspace", "workspace_name", help="Workspace name (auto-detected from tmux env if omitted).")
@click.option("--repo", "repo_path", type=click.Path(exists=True), help="Repository path (auto-detected if omitted).")
@click.option("--plan-mode", is_flag=True, help="Start Claude in plan mode.")
def pane_add(
    popup_file: str | None,
    branch: str | None,
    ai_tool: str,
    template_name: str | None,
    workspace_name: str | None,
    repo_path: str | None,
    plan_mode: bool,
) -> None:
    """Add a new worktree pane to the current workspace.

    Can be invoked via the tmux popup (prefix+n) or directly:

        owt pane add --branch feature/x --ai-tool claude --workspace owt-proj --repo /path
    """
    from open_orchestrator.core.pane_actions import PaneActionError, create_pane, read_popup_result

    # Read popup result if provided
    if popup_file:
        popup_data = read_popup_result(popup_file)
        branch = popup_data.get("branch", branch)
        ai_tool = popup_data.get("ai_tool", ai_tool)
        template_name = popup_data.get("template", template_name)

    if not branch:
        console.print("[red]Error: --branch is required (or use --from-popup).[/red]")
        raise SystemExit(1)

    # Auto-detect workspace and repo from tmux environment
    if not workspace_name:
        workspace_name = os.environ.get("OWT_WORKSPACE")
    if not repo_path:
        repo_path = os.environ.get("OWT_REPO")

    if not workspace_name or not repo_path:
        console.print("[red]Error: --workspace and --repo required (or set OWT_WORKSPACE/OWT_REPO).[/red]")
        raise SystemExit(1)

    try:
        result = create_pane(
            workspace_name=workspace_name,
            repo_path=repo_path,
            branch=branch,
            ai_tool=AITool(ai_tool),
            template_name=template_name,
            plan_mode=plan_mode,
        )
        console.print(f"[green]✓[/green] Pane added: {result.worktree_name} (pane {result.pane_index})")
    except PaneActionError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)


@pane_group.command("remove")
@click.option("--pane-id", type=int, help="tmux pane index to remove.")
@click.option("--worktree", "worktree_name", help="Worktree name to remove.")
@click.option("--workspace", "workspace_name", help="Workspace name.")
@click.option("--keep-worktree", is_flag=True, help="Keep the git worktree (only close the pane).")
def pane_remove(
    pane_id: int | None,
    worktree_name: str | None,
    workspace_name: str | None,
    keep_worktree: bool,
) -> None:
    """Remove a pane from the workspace and optionally delete its worktree.

    Can be invoked via the tmux keybinding (prefix+X) or directly:

        owt pane remove --worktree feature/x --workspace owt-proj
    """
    from open_orchestrator.core.pane_actions import PaneActionError, remove_pane

    if not workspace_name:
        workspace_name = os.environ.get("OWT_WORKSPACE")

    if not workspace_name:
        console.print("[red]Error: --workspace required (or set OWT_WORKSPACE).[/red]")
        raise SystemExit(1)

    # Resolve worktree name from pane_id if needed
    if pane_id is not None and not worktree_name:
        workspace_manager = WorkspaceManager()
        try:
            workspace = workspace_manager.get_workspace(workspace_name)
            for pane in workspace.panes:
                if pane.pane_index == pane_id:
                    worktree_name = pane.worktree_name
                    break
        except WorkspaceNotFoundError:
            pass

    if not worktree_name:
        console.print("[red]Error: --worktree or --pane-id required.[/red]")
        raise SystemExit(1)

    repo_path = os.environ.get("OWT_REPO")

    try:
        remove_pane(
            workspace_name=workspace_name,
            worktree_name=worktree_name,
            repo_path=repo_path,
            keep_worktree=keep_worktree,
            pane_index=pane_id,
        )
        console.print(f"[green]✓[/green] Pane removed: {worktree_name}")
    except PaneActionError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)


@main.command("create")
@click.argument("branch")
@click.option(
    "-b",
    "--base",
    "base_branch",
    help="Base branch for creating new branches.",
)
@click.option(
    "-t",
    "--template",
    "template_name",
    help="Apply a worktree template (use 'owt template list' to see available templates).",
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
    type=click.Choice(["claude", "opencode", "droid", "codex", "gemini-cli", "aider", "amp", "kilo-code"]),
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
    type=click.Choice(["single", "main-focus", "main-vertical", "three-pane", "quad", "even-horizontal", "even-vertical"]),
    default="single",
    help="tmux pane layout for the session (default: single for on-demand workspace mode).",
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
    "--auto-optimize",
    is_flag=True,
    help="Automatically select cost-effective AI tool based on task (use with --template).",
)
@click.option(
    "--sync-claude-md/--no-sync-claude-md",
    default=True,
    help="Sync CLAUDE.md files from main repo (default: enabled).",
)
@click.option(
    "--separate-session",
    is_flag=True,
    help="Create separate tmux session instead of adding to workspace (opt-out of default unified mode).",
)
@click.option(
    "--ab",
    "ab_tools",
    nargs=2,
    type=click.Choice(["claude", "opencode", "droid"]),
    help="A/B comparison mode: create two worktrees with different AI tools (e.g., --ab claude opencode).",
)
@click.option(
    "--prompt",
    "ab_prompt",
    help="Initial prompt to send to both agents (only used with --ab).",
)
def create_worktree(
    branch: str,
    base_branch: str | None,
    template_name: str | None,
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
    auto_optimize: bool,
    sync_claude_md: bool,
    separate_session: bool,
    ab_tools: tuple[str, str] | None,
    ab_prompt: str | None,
) -> None:
    """Create a new worktree for BRANCH with unified workspace mode.

    If BRANCH doesn't exist, it will be created from the base branch
    (or current branch if not specified).

    By default:
    - Adds worktree as a new pane in your workspace (unified view)
    - Installs dependencies and copies .env
    - Starts Claude Code in the new pane
    - Uses main-focus layout (1/3 left main + 3 horizontal right)

    Use --separate-session to create a standalone tmux session instead.

    Use --ab to create A/B comparison with two AI tools side-by-side.

    Examples:
        owt create feature/new-feature
        owt create bugfix/fix-123 --template bugfix
        owt create feature/api --base main
        owt create feature/standalone --separate-session
        owt create feature/research --plan-mode
        owt create feature/test --ab claude opencode
        owt create feature/test --ab claude opencode --prompt "Implement auth"
    """
    # Handle A/B comparison mode
    if ab_tools:
        try:
            tool_a_enum = AITool(ab_tools[0])
            tool_b_enum = AITool(ab_tools[1])

            ab_launcher = ABLauncher()

            with console.status(f"[bold blue]Launching A/B comparison for '{branch}'..."):
                ab_workspace = ab_launcher.launch(
                    branch=branch,
                    tool_a=tool_a_enum,
                    tool_b=tool_b_enum,
                    base_branch=base_branch,
                    initial_prompt=ab_prompt,
                )

            console.print()
            console.print("[bold green]A/B workspace created successfully!")
            console.print()
            console.print(f"[bold]Branch:[/bold]       {ab_workspace.branch}")
            console.print(f"[bold]Worktree A:[/bold]   {ab_workspace.worktree_a} ({ab_workspace.tool_a.value})")
            console.print(f"[bold]Worktree B:[/bold]   {ab_workspace.worktree_b} ({ab_workspace.tool_b.value})")
            console.print(f"[bold]Session:[/bold]      {ab_workspace.tmux_session}")

            if ab_workspace.initial_prompt:
                console.print(f"[bold]Prompt:[/bold]       {ab_workspace.initial_prompt}")

            console.print()
            console.print("[dim]Attach to the session:[/dim]")
            console.print(f"  tmux attach -t {ab_workspace.tmux_session}")
            console.print()
            console.print("[dim]Both agents are running side-by-side. Compare their outputs and choose the best result![/dim]")

            return

        except ToolNotInstalledError as e:
            raise click.ClickException(str(e)) from e
        except ABLauncherError as e:
            raise click.ClickException(f"Failed to launch A/B comparison: {e}") from e

    # Normal worktree creation (non-A/B mode)
    config = load_config()
    wt_manager = get_worktree_manager()
    tmux_manager = TmuxManager() if tmux else None
    main_repo_path = wt_manager.repo.working_dir

    # NEW: Check active worktree count and warn about resource usage
    active_worktrees = wt_manager.list_all()
    active_count = len([wt for wt in active_worktrees if not wt.is_main])

    if active_count >= 5:
        console.print()
        console.print(
            f"[yellow]Warning:[/yellow] You have {active_count} active worktree(s). "
            f"Running many parallel worktrees can consume significant memory (~500MB-1GB each). "
            f"Consider running 'owt cleanup --all' to clean up unused worktrees."
        )
        console.print()

        if active_count >= 8:
            if not click.confirm("Continue anyway?"):
                console.print("[yellow]Aborted.[/yellow]")
                raise click.Abort()

    # Apply template configuration if specified
    template_config = {}
    ai_instructions = None
    auto_commands = []

    if template_name:
        try:
            template_config = wt_manager.get_template_config(template_name)

            # Template overrides CLI arguments
            if "base_branch" in template_config and not base_branch:
                base_branch = template_config["base_branch"]
            if "ai_tool" in template_config:
                ai_tool = template_config["ai_tool"].value
            if "tmux_layout" in template_config:
                layout = template_config["tmux_layout"]
            if "plan_mode" in template_config:
                plan_mode = template_config["plan_mode"]
            if "install_deps" in template_config:
                deps = template_config["install_deps"]
            if "ai_instructions" in template_config:
                ai_instructions = template_config["ai_instructions"]
            if "auto_commands" in template_config:
                auto_commands = template_config["auto_commands"]

            console.print(f"[dim]Using template: {template_name}[/dim]")
        except WorktreeError as e:
            console.print(f"[red]✗[/red] {e}")
            raise click.Abort() from e

    # Auto-optimize AI tool selection
    if auto_optimize and ai_instructions:
        tracker = StatusTracker()
        recommendation = tracker.recommend_ai_tool(
            task_description=ai_instructions or branch,
            prefer_quality=False,
        )

        recommended_tool = recommendation["recommended_tool"]
        reasoning = recommendation["reasoning"]

        # Show cost comparison
        console.print()
        console.print("[bold cyan]💰 Cost Optimization:[/bold cyan]")
        console.print(f"  Recommended: [green]{recommended_tool}[/green]")
        console.print(f"  Reason: [dim]{reasoning}[/dim]")

        # Map recommendation to AI tool enum value
        tool_mapping = {
            "claude-opus": "claude",
            "claude-sonnet": "claude",
            "claude-haiku": "claude",
            "gpt-4o": "opencode",  # Assuming opencode can use different models
            "gpt-4o-mini": "opencode",
        }

        ai_tool = tool_mapping.get(recommended_tool, "claude")
        console.print(f"  Using: [green]{ai_tool}[/green]")
    elif auto_optimize:
        console.print("[yellow]⚠[/yellow] [dim]--auto-optimize requires a template with AI instructions[/dim]")

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
                        with console.status(f"[bold blue]Installing dependencies ({project_config.package_manager.value})..."):
                            try:
                                env_setup.install_dependencies(str(worktree.path))
                                console.print("[green]Dependencies installed[/green]")
                            except EnvironmentSetupError as e:
                                console.print(f"[yellow]Warning: Could not install dependencies: {e}[/yellow]")

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

        # Create tmux session if enabled (workspace mode or separate session)
        tmux_session = None
        workspace_name = None
        if tmux and tmux_manager:
            try:
                layout_map = {
                    "single": TmuxLayout.SINGLE,
                    "main-vertical": TmuxLayout.MAIN_VERTICAL,
                    "main-focus": TmuxLayout.MAIN_FOCUS,
                    "three-pane": TmuxLayout.THREE_PANE,
                    "quad": TmuxLayout.QUAD,
                    "even-horizontal": TmuxLayout.EVEN_HORIZONTAL,
                    "even-vertical": TmuxLayout.EVEN_VERTICAL,
                }

                # Map strings to enums
                ai_tool_enum = AITool(ai_tool)
                droid_auto_enum = DroidAutoLevel(droid_auto) if droid_auto else None

                # Workspace mode (default) vs separate session
                if not separate_session:
                    # WORKSPACE MODE: Add pane to existing workspace or create new one
                    workspace_manager = WorkspaceManager()

                    # Get or create default workspace for this project
                    workspace_name = f"owt-{wt_manager.project_name}"

                    # Check if workspace exists and its tmux session is still alive
                    workspace_needs_creation = False
                    try:
                        workspace = workspace_manager.get_workspace(workspace_name)
                        # Verify the tmux session still exists (might be stale after reboot/kill)
                        if tmux_manager and not tmux_manager.session_exists(workspace_name):
                            console.print(f"[yellow]Workspace '{workspace_name}' metadata exists but tmux session is gone. Recreating...[/yellow]")
                            workspace_manager.delete_workspace(workspace_name)
                            workspace_needs_creation = True
                    except WorkspaceNotFoundError:
                        workspace_needs_creation = True

                    if workspace_needs_creation:
                        # Create new workspace with TUI sidebar (dmux-style)
                        with console.status(f"[bold blue]Creating workspace '{workspace_name}'..."):
                            # Create tmux session with TUI in pane 0
                            session_info = tmux_manager.create_tui_session(
                                workspace_name=workspace_name,
                                repo_path=main_repo_path,
                            )

                            # Register workspace
                            workspace = workspace_manager.create_workspace(
                                name=workspace_name,
                                session_id=session_info.session_id,
                                layout=WorkspaceLayout.MAIN_FOCUS,
                                max_panes=10,
                            )

                            # Also install keybindings as fallback
                            try:
                                tmux_manager.install_keybindings(
                                    session_name=workspace_name,
                                    workspace_name=workspace_name,
                                    repo_path=main_repo_path,
                                )
                            except TmuxError:
                                pass  # TUI handles keys directly, keybindings are optional

                        console.print(f"[green]✓[/green] Created workspace: {workspace_name}")
                        console.print("[dim]TUI sidebar running — press n to add agents.[/dim]")

                    # Check if workspace has space (on_demand mode auto-expands)
                    if workspace.is_full and not workspace.on_demand:
                        raise WorkspaceFullError(
                            f"Workspace '{workspace_name}' is full ({workspace.max_panes} panes). "
                            f"Use --separate-session to create a new tmux session instead."
                        )

                    # Add worktree pane to workspace
                    with console.status("[bold blue]Adding pane to workspace..."):
                        pane_index = tmux_manager.add_worktree_pane(
                            session_name=workspace_name,
                            worktree_path=str(worktree.path),
                            worktree_name=worktree.name,
                            ai_tool=ai_tool_enum,
                            plan_mode=plan_mode,
                            droid_auto=droid_auto_enum,
                            droid_skip_permissions=droid_skip_permissions,
                            opencode_config=opencode_config,
                        )

                        # Register pane in workspace
                        workspace_manager.add_worktree_pane(
                            workspace_name=workspace_name,
                            pane_index=pane_index,
                            worktree_name=worktree.name,
                            worktree_path=worktree.path,
                        )

                    console.print()
                    console.print("[bold green]✓ Pane added to workspace!")
                    console.print(f"[bold]Workspace:[/bold] {workspace_name}")
                    console.print(f"[bold]Pane:[/bold]      {pane_index}")
                    console.print(f"[bold]Total:[/bold]     {len(workspace.panes) + 1} / {workspace.max_panes} panes")

                    # Create a mock session info for compatibility
                    class MockSessionInfo:
                        def __init__(self, name, panes):
                            self.session_name = name
                            self.pane_count = panes

                    tmux_session = MockSessionInfo(workspace_name, len(workspace.panes) + 1)

                else:
                    # SEPARATE SESSION MODE: Create standalone tmux session (old behavior)
                    with console.status("[bold blue]Creating separate tmux session..."):
                        tmux_session = tmux_manager.create_worktree_session(
                            worktree_name=worktree.name,
                            worktree_path=str(worktree.path),
                            layout=layout_map.get(layout, TmuxLayout.MAIN_VERTICAL),
                            pane_count=panes,
                            auto_start_ai=claude,
                            ai_tool=ai_tool_enum,
                            droid_auto=droid_auto_enum,
                            droid_skip_permissions=droid_skip_permissions,
                            opencode_config=opencode_config,
                            plan_mode=plan_mode,
                            mouse_mode=config.tmux.mouse_mode,
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

                # Send AI instructions from template if provided
                if ai_instructions and claude:
                    try:
                        console.print(f"\n[dim]Sending template instructions to {ai_tool_enum.value}...[/dim]")
                        tmux_manager.send_keys_to_session(
                            session_name=tmux_session.session_name,
                            keys=ai_instructions,
                            pane_index=0,
                        )
                        status_tracker.update_task(worktree.name, ai_instructions[:100])
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not send AI instructions: {e}[/yellow]")

                # Run auto commands from template
                if auto_commands:
                    console.print(f"\n[dim]Running {len(auto_commands)} auto command(s)...[/dim]")
                    for cmd in auto_commands:
                        try:
                            # Run commands in a separate pane if available
                            target_pane = 1 if tmux_session.pane_count > 1 else 0
                            tmux_manager.send_keys_to_session(
                                session_name=tmux_session.session_name,
                                keys=cmd,
                                pane_index=target_pane,
                            )
                        except Exception as e:
                            console.print(f"[yellow]Warning: Could not run command '{cmd}': {e}[/yellow]")

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
                except Exception:  # noqa: S110
                    pass  # PR auto-linking is best-effort, failures are silent

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


@main.command("new")
@click.argument("description", nargs=-1)
@click.option(
    "-b",
    "--base",
    "base_branch",
    help="Base branch for the new worktree.",
)
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid", "codex", "gemini-cli", "aider", "amp", "kilo-code"]),
    default=None,
    help="AI tool to start (auto-detected if not specified).",
)
@click.option(
    "--plan-mode",
    is_flag=True,
    help="Start Claude in plan mode.",
)
@click.option(
    "-t",
    "--template",
    "template_name",
    help="Apply a worktree template.",
)
@click.option(
    "-a",
    "--attach",
    is_flag=True,
    help="Attach to tmux session after creation.",
)
@click.option(
    "--prefix",
    help="Override auto-detected branch prefix (e.g., feat, fix, refactor).",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip branch name confirmation.",
)
def new_worktree(
    description: tuple[str, ...],
    base_branch: str | None,
    ai_tool: str | None,
    plan_mode: bool,
    template_name: str | None,
    attach: bool,
    prefix: str | None,
    yes: bool,
) -> None:
    """Create a worktree from a task description (prompt-first workflow).

    Automatically generates a branch name from your task description and
    creates a worktree with the AI agent ready to work.

    The task description is also sent as the initial prompt to the AI agent.

    Examples:
        owt new Add user authentication with JWT
        owt new Fix login redirect bug
        owt new "Refactor database queries for performance"
        owt new Add dark mode --ai-tool claude --plan-mode
    """
    from open_orchestrator.core.agent_detector import detect_installed_agents
    from open_orchestrator.core.branch_namer import generate_branch_name

    # Get description from args or prompt interactively
    if description:
        task_description = " ".join(description)
    else:
        task_description = click.prompt("What are you working on?")

    if not task_description.strip():
        raise click.ClickException("Task description cannot be empty")

    # Generate branch name
    try:
        branch = generate_branch_name(task_description, prefix=prefix)
    except ValueError as e:
        raise click.ClickException(f"Could not generate branch name: {e}") from e

    # Check for git ref conflicts (e.g., branch "test" blocks "test/task")
    from git import Repo
    try:
        repo = Repo(search_parent_directories=True)
        existing_refs = {ref.name for ref in repo.refs}
        # Check if any existing ref is a prefix of our branch or vice versa
        branch_parts = branch.split("/")
        has_conflict = False
        for i in range(1, len(branch_parts)):
            partial = "/".join(branch_parts[:i])
            if partial in existing_refs:
                console.print(f"[yellow]Branch '{partial}' already exists — cannot create '{branch}' (git ref conflict).[/yellow]")
                has_conflict = True
                break
        # Also check if our branch is a prefix of existing refs
        for ref in existing_refs:
            if ref.startswith(branch + "/"):
                console.print(f"[yellow]Branch '{ref}' already exists — cannot create '{branch}' (git ref conflict).[/yellow]")
                has_conflict = True
                break
        if has_conflict:
            branch = click.prompt("Enter a different branch name")
    except Exception:
        pass  # If we can't check, let git handle it later

    # Confirm branch name (unless -y)
    if not yes:
        console.print(f"\n[bold]Task:[/bold]   {task_description}")
        console.print(f"[bold]Branch:[/bold] {branch}")
        if not click.confirm("\nProceed?", default=True):
            # Let user override
            branch = click.prompt("Enter branch name", default=branch)

    # Auto-detect AI tool if not specified
    if ai_tool is None:
        installed = detect_installed_agents()
        if len(installed) == 0:
            raise click.ClickException(
                "No AI coding tools found. Install one of: claude, opencode, droid, codex, gemini-cli, aider"
            )
        elif len(installed) == 1:
            ai_tool = installed[0].value
        else:
            # Show picker for multiple tools
            console.print("\n[bold]Detected AI tools:[/bold]")
            tool_names = [t.value for t in installed]
            for i, tool in enumerate(installed, 1):
                console.print(f"  {i}. {tool.value}")

            choice = click.prompt(
                "Select AI tool",
                type=click.IntRange(1, len(installed)),
                default=1,
            )
            ai_tool = tool_names[choice - 1]
    else:
        # Validate the chosen tool is installed
        tool_enum = AITool(ai_tool)
        if not AITool.is_installed(tool_enum):
            console.print(f"[yellow]Warning: {ai_tool} may not be installed[/yellow]")
            console.print(f"[dim]{AITool.get_install_hint(tool_enum)}[/dim]")

    # Delegate to create_worktree via click context
    # We invoke the create command programmatically with the right options
    ctx = click.get_current_context()
    ctx.invoke(
        create_worktree,
        branch=branch,
        base_branch=base_branch,
        template_name=template_name,
        path=None,
        force=False,
        tmux=True,
        claude=True,
        ai_tool=ai_tool,
        droid_auto=None,
        droid_skip_permissions=False,
        opencode_config=None,
        layout="single",
        panes=2,
        attach=attach,
        deps=True,
        env=True,
        plan_mode=plan_mode,
        auto_optimize=False,
        sync_claude_md=True,
        separate_session=False,
        ab_tools=None,
        ab_prompt=None,
    )

    # Send the task description as initial prompt to the AI agent
    # The workspace session should exist now
    try:
        wt_manager = get_worktree_manager()
        worktree = wt_manager.get(branch)
        workspace_name = f"owt-{wt_manager.project_name}"

        tmux_manager = TmuxManager()

        # Find the pane for this worktree
        workspace_manager = WorkspaceManager()
        try:
            workspace = workspace_manager.get_workspace(workspace_name)
            target_pane = workspace.get_pane_by_worktree(worktree.name)
            pane_index = target_pane.pane_index if target_pane else 0
        except WorkspaceNotFoundError:
            pane_index = 0

        # Wait a moment for the AI tool to initialize, then send the prompt
        import time
        time.sleep(2)

        tmux_manager.send_keys_to_pane(
            session_name=workspace_name,
            keys=task_description,
            pane_index=pane_index,
        )
        console.print(f"\n[cyan]Sent task to AI agent:[/cyan] {task_description[:80]}{'...' if len(task_description) > 80 else ''}")

        # Update status tracker with the task
        status_tracker = StatusTracker()
        status_tracker.update_task(worktree.name, task_description[:100])

    except Exception as e:
        console.print(f"[yellow]Warning: Could not send initial prompt: {e}[/yellow]")
        console.print(f"[dim]You can manually send it: owt send {branch} \"{task_description}\"[/dim]")


@main.command("merge")
@click.argument("worktree_name")
@click.option(
    "--base",
    "base_branch",
    help="Target branch to merge into (auto-detected if not specified).",
)
@click.option(
    "--keep",
    is_flag=True,
    help="Keep the worktree after merging (by default it's deleted).",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output result as JSON.",
)
def merge_worktree(
    worktree_name: str,
    base_branch: str | None,
    keep: bool,
    yes: bool,
    json_output: bool,
) -> None:
    """Merge a worktree branch into its base branch and clean up.

    Two-phase merge:
    1. Merges base into worktree branch (catches conflicts early)
    2. Merges worktree branch into base (fast-forward if possible)

    After successful merge, automatically deletes the worktree and pane
    unless --keep is specified.

    Examples:
        owt merge feature/auth
        owt merge my-feature --base develop
        owt merge feature/api --keep
        owt merge feature/api --json
    """
    from open_orchestrator.core.merge import MergeConflictError, MergeError, MergeManager, MergeStatus

    try:
        merge_manager = MergeManager()
    except Exception as e:
        raise click.ClickException(str(e)) from e

    # Resolve worktree info for confirmation
    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(worktree_name)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    # Determine base branch
    target = base_branch
    if not target:
        try:
            target = merge_manager.get_base_branch(worktree.branch)
        except MergeError as e:
            raise click.ClickException(str(e)) from e

    # Count commits
    commits_ahead = merge_manager.count_commits_ahead(worktree.branch, target)

    if not json_output and not yes:
        console.print()
        console.print("[bold]Merge plan:[/bold]")
        console.print(f"  Source: {worktree.branch} ({commits_ahead} commit(s) ahead)")
        console.print(f"  Target: {target}")
        console.print(f"  Cleanup: {'keep worktree' if keep else 'delete worktree + pane'}")
        console.print()

        if not click.confirm("Proceed with merge?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Execute merge
    try:
        with console.status("[bold blue]Merging...") if not json_output else nullcontext():
            result = merge_manager.merge(
                worktree_name=worktree_name,
                base_branch=base_branch,
                delete_worktree=not keep,
            )
    except MergeConflictError as e:
        if json_output:
            import json as json_mod
            console.print(json_mod.dumps({
                "status": "conflicts",
                "source_branch": worktree.branch,
                "target_branch": target,
                "conflicts": e.conflicts,
                "message": str(e),
            }, indent=2))
        else:
            console.print(f"\n[red]Merge conflicts detected:[/red] {e}")
            console.print()
            for conflict in e.conflicts:
                console.print(f"  [yellow]C[/yellow] {conflict}")
            console.print()
            console.print("[dim]Resolve conflicts in the worktree, then try again.[/dim]")
            console.print(f"[dim]  cd {worktree.path}[/dim]")
        raise SystemExit(1)
    except MergeError as e:
        raise click.ClickException(str(e)) from e

    if json_output:
        import json as json_mod
        console.print(json_mod.dumps(result.to_dict(), indent=2))
        return

    # Display result
    if result.status == MergeStatus.ALREADY_MERGED:
        console.print(f"\n[yellow]{result.message}[/yellow]")
    elif result.status == MergeStatus.SUCCESS:
        console.print(f"\n[bold green]Merged successfully!")
        console.print(f"  {result.source_branch} → {result.target_branch} ({result.commits_merged} commit(s))")
        if result.worktree_cleaned:
            # Also clean up pane and status tracking
            try:
                status_tracker = StatusTracker()
                status_tracker.remove_status(worktree.name)
            except Exception:
                pass

            try:
                workspace_name = f"owt-{wt_manager.project_name}"
                workspace_manager = WorkspaceManager()
                workspace = workspace_manager.get_workspace(workspace_name)
                target_pane = workspace.get_pane_by_worktree(worktree.name)
                if target_pane:
                    tmux_manager = TmuxManager()
                    try:
                        tmux_manager.remove_pane(workspace_name, target_pane.pane_index)
                    except TmuxError:
                        pass
                    workspace_manager.remove_worktree_pane(workspace_name, worktree.name)
            except (WorkspaceNotFoundError, Exception):
                pass

            console.print("  [green]Worktree cleaned up[/green]")
    else:
        console.print(f"\n[red]{result.message}[/red]")


@main.command("close")
@click.argument("worktree_name")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Force close even with uncommitted changes.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output result as JSON.",
)
def close_worktree(
    worktree_name: str,
    yes: bool,
    force: bool,
    json_output: bool,
) -> None:
    """Close a worktree: remove its pane and delete the worktree atomically.

    Combines pane removal + worktree deletion + status cleanup into a single
    command. This is the complement to 'owt new'.

    Examples:
        owt close feature/auth
        owt close my-feature -y
        owt close feature/api --force
    """
    wt_manager = get_worktree_manager()

    try:
        worktree = wt_manager.get(worktree_name)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    if not yes and not json_output:
        console.print()
        console.print("[bold]About to close:[/bold]")
        console.print(f"  Branch:    {worktree.branch}")
        console.print(f"  Path:      {worktree.short_path}")
        console.print(f"  Actions:   Remove pane + delete worktree + cleanup status")
        console.print()

        if not click.confirm("Proceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    errors: list[str] = []

    # 1. Remove pane from workspace
    workspace_name = f"owt-{wt_manager.project_name}"
    try:
        workspace_manager = WorkspaceManager()
        workspace = workspace_manager.get_workspace(workspace_name)
        target_pane = workspace.get_pane_by_worktree(worktree.name)
        if target_pane:
            tmux_manager = TmuxManager()
            try:
                tmux_manager.remove_pane(workspace_name, target_pane.pane_index)
            except TmuxError as e:
                errors.append(f"Pane removal: {e}")
            workspace_manager.remove_worktree_pane(workspace_name, worktree.name)
    except WorkspaceNotFoundError:
        # Not in workspace mode — try standalone session
        tmux_manager = TmuxManager()
        session = tmux_manager.get_session_for_worktree(worktree.name)
        if session:
            try:
                tmux_manager.kill_session(session.session_name)
            except TmuxError as e:
                errors.append(f"Session kill: {e}")

    # 2. Stop any AI tool processes
    try:
        from open_orchestrator.core.process_manager import ProcessManager
        process_manager = ProcessManager()
        if process_manager.get_process(worktree.name):
            process_manager.stop_ai_tool(worktree.name, force=force)
    except Exception:
        pass  # Best-effort

    # 3. Delete worktree
    try:
        wt_manager.delete(worktree.name, force=force)
    except WorktreeError as e:
        errors.append(f"Worktree deletion: {e}")

    # 4. Clean up status tracking
    try:
        status_tracker = StatusTracker()
        status_tracker.remove_status(worktree.name)
    except Exception:
        pass  # Best-effort

    if json_output:
        import json as json_mod
        console.print(json_mod.dumps({
            "status": "error" if errors else "success",
            "worktree": worktree.name,
            "branch": worktree.branch,
            "errors": errors,
        }, indent=2))
        return

    if errors:
        console.print(f"\n[yellow]Closed with warnings:[/yellow]")
        for err in errors:
            console.print(f"  [yellow]![/yellow] {err}")
    else:
        console.print(f"\n[bold green]Closed:[/bold green] {worktree.name} ({worktree.branch})")


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
        # Check and clean up any ProcessManager processes first
        from open_orchestrator.core.process_manager import ProcessManager

        try:
            process_manager = ProcessManager()
            if process_manager.get_process(worktree.name):
                console.print(f"[yellow]Stopping AI tool process for {worktree.name}...[/yellow]")
                process_manager.stop_ai_tool(worktree.name, force=force)
                console.print("[green]✓[/green] Stopped AI tool process")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not stop AI process: {e}[/yellow]")

        # Kill tmux session if it exists and --keep-tmux not specified
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
                    f"{tmux_manager.generate_session_name(worktree.name)} "
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
@click.option(
    "--processes",
    is_flag=True,
    help="Also clean up orphaned AI tool processes.",
)
@click.option(
    "--sessions",
    is_flag=True,
    help="Also clean up orphaned tmux sessions.",
)
@click.option(
    "--all",
    "cleanup_all",
    is_flag=True,
    help="Clean up everything (worktrees + processes + sessions).",
)
def cleanup_worktrees(
    threshold_days: int,
    dry_run: bool,
    force: bool,
    yes: bool,
    as_json: bool,
    processes: bool,
    sessions: bool,
    cleanup_all: bool,
) -> None:
    """Clean up stale worktrees, orphaned processes, and tmux sessions.

    By default, runs in dry-run mode showing what would be deleted.
    Use --no-dry-run to actually delete stale worktrees.

    Worktrees with uncommitted changes or unpushed commits are protected
    by default. Use --force to override this protection.

    Example:
        owt cleanup                        # Dry run with default 14 days
        owt cleanup --days 7               # Dry run with 7 days threshold
        owt cleanup --no-dry-run -y        # Actually delete stale worktrees
        owt cleanup --force                # Include worktrees with uncommitted changes
        owt cleanup --processes            # Also clean up orphaned processes
        owt cleanup --sessions             # Also clean up orphaned tmux sessions
        owt cleanup --all                  # Clean up everything
        owt cleanup --json                 # Output as JSON
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
            click.echo(json_module.dumps({"stale_worktrees": [], "message": "No worktrees to clean up"}))
        else:
            console.print("[yellow]No worktrees to clean up.[/yellow]")
        return

    stale_worktrees = cleanup_service.get_stale_worktrees(worktree_paths, threshold_days)

    if not stale_worktrees:
        if as_json:
            click.echo(json_module.dumps({"stale_worktrees": [], "threshold_days": threshold_days}))
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

        click.echo(json_module.dumps(data, indent=2))
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
        protected_count = sum(1 for s in stale_worktrees if s.has_uncommitted_changes or s.has_unpushed_commits)
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

    # NEW: Orphaned process cleanup
    if processes or cleanup_all:
        console.print()
        console.print("[bold]Cleaning up orphaned processes...[/bold]")
        proc_report = cleanup_service.cleanup_orphaned_processes(dry_run=dry_run)

        if as_json:
            console.print(json_module.dumps({
                "orphaned_processes": proc_report.orphaned_processes,
                "processes_killed": proc_report.processes_killed,
                "dry_run": dry_run,
            }, indent=2))
        else:
            if proc_report.orphaned_processes:
                console.print(f"[yellow]Found {len(proc_report.orphaned_processes)} orphaned process(es):[/yellow]")
                for proc_name in proc_report.orphaned_processes:
                    console.print(f"  - {proc_name}")
                if not dry_run:
                    console.print(f"[green]Killed {proc_report.processes_killed} process(es)[/green]")
            else:
                console.print("[green]No orphaned processes found[/green]")

            if proc_report.errors:
                console.print("[bold red]Errors:[/bold red]")
                for error in proc_report.errors:
                    console.print(f"  [red]{error}[/red]")

    # NEW: Orphaned session cleanup
    if sessions or cleanup_all:
        console.print()
        console.print("[bold]Cleaning up orphaned tmux sessions...[/bold]")
        session_report = cleanup_service.cleanup_orphaned_tmux_sessions(dry_run=dry_run)

        if as_json:
            console.print(json_module.dumps({
                "orphaned_sessions": session_report.orphaned_sessions,
                "sessions_killed": session_report.sessions_killed,
                "dry_run": dry_run,
            }, indent=2))
        else:
            if session_report.orphaned_sessions:
                console.print(f"[yellow]Found {len(session_report.orphaned_sessions)} orphaned session(s):[/yellow]")
                for session_name in session_report.orphaned_sessions:
                    console.print(f"  - {session_name}")
                if not dry_run:
                    console.print(f"[green]Killed {session_report.sessions_killed} session(s)[/green]")
            else:
                console.print("[green]No orphaned sessions found[/green]")

            if session_report.errors:
                console.print("[bold red]Errors:[/bold red]")
                for error in session_report.errors:
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
@click.option(
    "--autonomous",
    is_flag=True,
    help="Execute autonomously without user interaction (auto-handles prompts).",
)
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid"]),
    default="claude",
    help="AI tool to use for autonomous execution (default: claude).",
)
def send_to_worktree(
    identifier: str,
    command: str,
    pane: int,
    window: int,
    no_enter: bool,
    no_log: bool,
    autonomous: bool,
    ai_tool: str,
) -> None:
    """Send a command to another worktree's tmux session.

    IDENTIFIER is the worktree name, branch, or path.
    COMMAND is the text to send to the worktree's Claude session.

    By default, sends to the main pane (pane 0) where Claude Code runs.
    Commands sent are tracked and visible via `owt status`.

    With --autonomous, starts an autonomous agent that handles the task
    independently without requiring user interaction.

    Example:
        owt send feature/auth "implement login validation"
        owt send my-worktree "run the tests"
        owt send feature/api "fix the bug in user service" --pane 1
        owt send my-feature "implement auth" --autonomous
    """
    wt_manager = get_worktree_manager()
    status_tracker = StatusTracker()

    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    # Handle autonomous mode
    if autonomous:
        from open_orchestrator.core.auto_agent import AutoAgent, AutoAgentError

        # Set up log directory
        log_dir = Path.home() / ".cache" / "open-orchestrator" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"{worktree.name}_{timestamp}.log"

        # Parse AI tool
        tool_enum = AITool(ai_tool)

        try:
            console.print(f"[cyan]Starting autonomous execution for {worktree.name}...[/cyan]")

            # Create and start agent
            agent = AutoAgent(
                worktree_path=worktree.path,
                task=command,
                ai_tool=tool_enum,
                log_file=log_file,
            )

            # Initialize status tracking if needed
            wt_status = status_tracker.get_status(worktree.name)
            if not wt_status:
                status_tracker.initialize_status(
                    worktree_name=worktree.name,
                    worktree_path=str(worktree.path),
                    branch=worktree.branch,
                    ai_tool=tool_enum,
                )

            # Start agent
            agent.start()

            # Update status
            status_tracker.update_task(worktree.name, command, AIActivityStatus.WORKING)

            console.print(f"[green]✓[/green] Autonomous agent started for {worktree.name}")
            console.print(f"Log file: {log_file}")
            return

        except AutoAgentError as e:
            raise click.ClickException(f"Failed to start autonomous agent: {e}") from e

    # Standard tmux mode
    tmux_manager = TmuxManager()
    session = tmux_manager.get_session_for_worktree(worktree.name)

    # Resolve target session and pane — either a dedicated session or a workspace pane
    target_session_name: str | None = None
    target_pane = pane
    target_window = window

    if session:
        target_session_name = session.session_name
    else:
        # Check if the worktree is a pane in a workspace
        workspace_name = f"owt-{wt_manager.project_name}"
        try:
            workspace_manager = WorkspaceManager()
            workspace = workspace_manager.get_workspace(workspace_name)
            workspace_pane = workspace.get_pane_by_worktree(worktree.name)
            if workspace_pane:
                target_session_name = workspace_name
                target_pane = workspace_pane.pane_index
                target_window = 0
        except WorkspaceNotFoundError:
            pass

    if not target_session_name:
        msg = (
            f"No tmux session or workspace pane found for worktree '{identifier}'. "
            f"Create one with: owt tmux create "
            f"{tmux_manager.generate_session_name(worktree.name)} -d {worktree.path}"
        )
        raise click.ClickException(msg)

    # Get the source worktree (the one sending the command)
    source_worktree = status_tracker.get_current_worktree_name()

    try:
        if no_enter:
            # Send without Enter - use raw tmux command
            subprocess.run(["tmux", "send-keys", "-t", f"{target_session_name}:{target_window}.{target_pane}", command], check=True)
        else:
            tmux_manager.send_keys_to_pane(session_name=target_session_name, keys=command, pane_index=target_pane, window_index=target_window)

        # Track the command in the status system
        wt_status = status_tracker.get_status(worktree.name)
        if not wt_status:
            # Initialize status if it doesn't exist
            wt_status = status_tracker.initialize_status(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                branch=worktree.branch,
                tmux_session=target_session_name,
            )

        # Record command unless --no-log is specified
        if not no_log:
            status_tracker.record_command(
                target_worktree=worktree.name,
                command=command,
                source_worktree=source_worktree,
                pane_index=target_pane,
                window_index=target_window,
            )

        console.print(f"[green]Sent to {target_session_name}:{target_window}.{target_pane}:[/green] {command[:50]}{'...' if len(command) > 50 else ''}")

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
        console.print(f"  Working: {summary.active_ai_sessions}  |  Idle: {summary.idle_ai_sessions}")
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
            latest = summary.most_recent_activity.strftime("%Y-%m-%d %H:%M")
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


@main.command("health")
@click.argument("worktree", required=False)
@click.option(
    "-a",
    "--all",
    is_flag=True,
    help="Check health of all worktrees",
)
@click.option(
    "--stuck-threshold",
    type=int,
    default=30,
    help="Minutes before task is considered stuck (default: 30)",
)
@click.option(
    "--token-threshold",
    type=int,
    default=100_000,
    help="Token count threshold for high usage warning (default: 100,000)",
)
@click.option(
    "--cost-threshold",
    type=float,
    default=10.0,
    help="Cost threshold in USD (default: 10.0)",
)
@click.option(
    "--stale-days",
    type=int,
    default=7,
    help="Days of inactivity before stale warning (default: 7)",
)
@click.option(
    "--idle-hours",
    type=int,
    default=24,
    help="Hours of idle before warning (default: 24)",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON",
)
def health_check(
    worktree: str | None,
    all: bool,
    stuck_threshold: int,
    token_threshold: int,
    cost_threshold: float,
    stale_days: int,
    idle_hours: int,
    output_json: bool,
) -> None:
    """Check health of worktrees and detect issues.

    Monitors for:
    - Stuck tasks (same task for too long)
    - High token usage (possible infinite loops)
    - High cost sessions
    - Repeated errors
    - Stale worktrees (no activity)
    - Idle AI sessions

    Example:
        owt health feature/api
        owt health --all
        owt health --all --stuck-threshold 60
    """
    tracker = StatusTracker()

    if all:
        # Check all worktrees
        summary = tracker.check_all_health(
            stuck_threshold_minutes=stuck_threshold,
            high_token_threshold=token_threshold,
            high_cost_threshold_usd=cost_threshold,
            stale_threshold_days=stale_days,
            idle_threshold_hours=idle_hours,
        )

        if output_json:
            console.print_json(data=summary.model_dump(mode="json"))
            return

        # Display summary
        console.print()
        console.print("[bold]Health Summary[/bold]")
        console.print(f"Total worktrees: {summary.total_worktrees}")
        console.print(f"[green]Healthy: {summary.healthy_worktrees}[/green]")
        if summary.worktrees_with_warnings:
            console.print(f"[yellow]With warnings: {summary.worktrees_with_warnings}[/yellow]")
        if summary.worktrees_with_critical_issues:
            console.print(f"[red]Critical issues: {summary.worktrees_with_critical_issues}[/red]")

        # Show unhealthy worktrees
        unhealthy = [r for r in summary.reports if not r.healthy or r.issues]
        if unhealthy:
            console.print()
            for report in unhealthy:
                _display_health_report(report, console)
        else:
            console.print("\n[green]✓ All worktrees are healthy![/green]")

    else:
        # Check specific worktree or current
        if not worktree:
            worktree = tracker.get_current_worktree_name()
            if not worktree:
                console.print("[red]✗[/red] Not in a tracked worktree. Specify worktree name or use --all")
                raise click.Abort()

        report = tracker.check_health(
            worktree_name=worktree,
            stuck_threshold_minutes=stuck_threshold,
            high_token_threshold=token_threshold,
            high_cost_threshold_usd=cost_threshold,
            stale_threshold_days=stale_days,
            idle_threshold_hours=idle_hours,
        )

        if output_json:
            console.print_json(data=report.model_dump(mode="json"))
            return

        _display_health_report(report, console)


def _display_health_report(report: "HealthReport", console: Console) -> None:
    """Display a health report in a formatted way."""

    # Header
    status_icon = "[green]✓[/green]" if report.healthy else "[red]✗[/red]"
    console.print(f"\n{status_icon} [bold]{report.worktree_name}[/bold]")

    if not report.issues:
        console.print("[dim]No issues detected[/dim]")
        return

    # Group issues by severity
    if report.critical_issues:
        console.print("\n[bold red]Critical Issues:[/bold red]")
        for issue in report.critical_issues:
            console.print(f"  [red]✗[/red] {issue.message}")
            if issue.recommendation:
                console.print(f"    [dim]→ {issue.recommendation}[/dim]")

    if report.warning_issues:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for issue in report.warning_issues:
            console.print(f"  [yellow]⚠[/yellow] {issue.message}")
            if issue.recommendation:
                console.print(f"    [dim]→ {issue.recommendation}[/dim]")

    if report.info_issues:
        console.print("\n[bold cyan]Info:[/bold cyan]")
        for issue in report.info_issues:
            console.print(f"  [cyan]ℹ[/cyan] {issue.message}")
            if issue.recommendation:
                console.print(f"    [dim]→ {issue.recommendation}[/dim]")


@main.command("cost")
@click.argument("worktree", required=False)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON",
)
def cost_comparison(worktree: str | None, output_json: bool) -> None:
    """Show cost comparison across AI tools for a worktree.

    Displays current cost, alternative costs, and potential savings.

    Example:
        owt cost
        owt cost feature/api
        owt cost --json
    """
    tracker = StatusTracker()
    result = tracker.show_cost_comparison(worktree)

    if "error" in result:
        console.print(f"[red]✗[/red] {result['error']}")
        raise click.Abort()

    if output_json:
        console.print_json(data=result)
        return

    # Display formatted output
    console.print()
    console.print(f"[bold]Cost Analysis: {result['worktree']}[/bold]")
    console.print()
    console.print(f"Current AI tool: [cyan]{result['current_tool']}[/cyan]")
    console.print(f"Total tokens: [dim]{result['total_tokens']:,}[/dim]")
    console.print(f"Current cost: [yellow]${result['current_cost']:.4f}[/yellow]")
    console.print()

    # Show all costs
    console.print("[bold]Cost by AI Tool:[/bold]")
    sorted_costs = sorted(result["all_costs"].items(), key=lambda x: x[1])
    for tool, cost in sorted_costs:
        is_current = tool == result["current_tool"]
        marker = "→" if is_current else " "
        color = "yellow" if is_current else "dim"
        console.print(f"  {marker} {tool:20s} ${cost:.4f}" if is_current else f"  {marker} [{color}]{tool:20s} ${cost:.4f}[/{color}]")

    # Show savings
    if result["potential_savings"] > 0.01:
        console.print()
        console.print("[bold green]💰 Potential Savings:[/bold green]")
        console.print(f"  Cheapest: [green]{result['cheapest_tool']}[/green] (${result['cheapest_cost']:.4f})")
        console.print(f"  Savings: [green]${result['potential_savings']:.4f}[/green] ({result['savings_percentage']:.1f}%)")
        console.print()
        console.print("[dim]  Tip: Use --auto-optimize when creating new worktrees to save costs[/dim]")
    else:
        console.print()
        console.print("[green]✓ Already using the most cost-effective tool![/green]")


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
                session_id = (
                    session.session_id[:12] + "..."
                    if session.session_id and len(session.session_id) > 12
                    else session.session_id or "[dim]-[/dim]"
                )
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

    from open_orchestrator.core.hooks import get_hook_service_from_config

    hook_service = get_hook_service_from_config()
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
    type=click.Choice(
        [
            "on_status_changed",
            "on_task_started",
            "on_task_completed",
            "on_blocked",
            "on_error",
            "on_idle",
        ]
    ),
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
    from open_orchestrator.core.hooks import get_hook_service_from_config
    from open_orchestrator.models.hooks import HookAction, HookConfig, HookType

    hook_service = get_hook_service_from_config()

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
    from open_orchestrator.core.hooks import get_hook_service_from_config

    hook_service = get_hook_service_from_config()

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
    from open_orchestrator.core.hooks import get_hook_service_from_config

    hook_service = get_hook_service_from_config()

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
    from open_orchestrator.core.hooks import get_hook_service_from_config

    hook_service = get_hook_service_from_config()

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
    from open_orchestrator.core.hooks import get_hook_service_from_config

    hook_service = get_hook_service_from_config()
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

    from open_orchestrator.core.hooks import get_hook_service_from_config

    hook_service = get_hook_service_from_config()
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
    from open_orchestrator.core.hooks import get_hook_service_from_config

    hook_service = get_hook_service_from_config()
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
            console.print("  [dim](auto-detected from branch name)[/dim]")
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
    type=click.Choice(["claude", "opencode", "droid", "codex", "gemini-cli", "aider", "amp", "kilo-code"]),
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
            console.print("[yellow]Process was already stopped[/yellow]")
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
# TUI Command (dmux-style persistent sidebar)
# =============================================================================


@main.command("tui")
@click.option("--workspace", "workspace_name", help="Workspace name (auto-detected from OWT_WORKSPACE env if omitted).")
@click.option("--repo", "repo_path", type=click.Path(exists=True), help="Repository path (auto-detected from OWT_REPO if omitted).")
def tui_command(
    workspace_name: str | None,
    repo_path: str | None,
) -> None:
    """Launch persistent TUI sidebar (dmux-style).

    When run outside tmux, bootstraps a tmux session with the TUI sidebar
    in pane 0 and attaches — just like dmux. When run inside tmux, starts
    the Textual app directly.

    Keys: n=new, x=close, m=merge, j/k=navigate, Enter=focus, ?=help, q=quit

    Example:
        owt tui                              # Auto-detect workspace
        owt tui --workspace owt-myproject    # Explicit workspace
    """
    if not workspace_name:
        workspace_name = os.environ.get("OWT_WORKSPACE", "")
    if not repo_path:
        repo_path = os.environ.get("OWT_REPO", "")

    wt_manager = None
    if not repo_path:
        try:
            wt_manager = WorktreeManager()
            repo_path = str(wt_manager.repo.working_dir)
        except (NotAGitRepositoryError, Exception):
            console.print("[yellow]Warning: Not in a git repo. Some features will be limited.[/yellow]")

    if not workspace_name and repo_path:
        workspace_name = f"owt-{Path(repo_path).name}"

    # If not inside tmux, bootstrap a tmux session and attach (like dmux)
    if not os.environ.get("TMUX"):
        _bootstrap_tui_session(workspace_name, repo_path or ".")
        return

    # Inside tmux — run the Textual app directly
    from open_orchestrator.tui.app import launch_tui

    status_tracker = StatusTracker()
    if wt_manager is None and repo_path:
        wt_manager = WorktreeManager(repo_path=repo_path)

    launch_tui(
        status_tracker=status_tracker,
        wt_manager=wt_manager,
        workspace_name=workspace_name,
        repo_path=repo_path,
    )


def _bootstrap_tui_session(workspace_name: str, repo_path: str) -> None:
    """Bootstrap a tmux session with TUI in pane 0 and attach.

    Uses TmuxManager.create_tui_session for session setup, then replaces
    the current process with tmux attach (like dmux).
    """
    session_name = workspace_name or f"owt-{Path(repo_path).name}"
    tmux_mgr = TmuxManager()

    if tmux_mgr.session_exists(session_name):
        console.print(f"[green]Attaching to existing session: {session_name}[/green]")
    else:
        try:
            tmux_mgr.create_tui_session(
                workspace_name=session_name,
                repo_path=repo_path,
            )
            console.print(f"[green]Created workspace: {session_name}[/green]")
        except TmuxSessionExistsError:
            # Race condition: session was created between check and create
            console.print(f"[green]Attaching to existing session: {session_name}[/green]")

    # Replace this process with tmux attach (like dmux's execvp pattern)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


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


@main.command("version")
@click.option("--full", is_flag=True, help="Show detailed version information")
def version_cmd(full: bool) -> None:
    """Show version information.

    Examples:
        owt version              # Show version number
        owt version --full       # Show detailed installation info
    """
    from open_orchestrator.core.updater import Updater

    updater = Updater()

    if full:
        info = updater.get_install_info()
        console.print(f"\n[bold cyan]Open Orchestrator v{info['version']}[/bold cyan]\n")

        table = Table(show_header=False, box=None)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Install Path", info.get("install_path", "Unknown"))
        table.add_row("Python Version", info["python_version"].split()[0])
        table.add_row("Dev Install", "Yes" if info.get("is_dev_install") else "No")

        if "git_branch" in info:
            table.add_row("Git Branch", info["git_branch"])
        if "git_commit" in info:
            table.add_row("Git Commit", info["git_commit"])
        if info.get("has_local_changes"):
            table.add_row("Local Changes", "[yellow]Yes[/yellow]")

        console.print(table)
        console.print()
    else:
        console.print(f"Open Orchestrator v{updater.get_current_version()}")


@main.command("update")
@click.option("--check", is_flag=True, help="Check for updates without installing")
@click.option("--version", "target_version", help="Update to specific version (e.g., v0.2.0)")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
def update_cmd(check: bool, target_version: str | None, yes: bool) -> None:
    """Update Open Orchestrator to the latest version.

    Examples:
        owt update               # Update to latest version
        owt update --check       # Check if updates are available
        owt update --version v0.2.0  # Update to specific version
        owt update -y            # Update without confirmation
    """
    from open_orchestrator.core.updater import Updater

    updater = Updater()

    if check:
        # Check for updates only
        console.print("[cyan]Checking for updates...[/cyan]")
        update_info = updater.check_for_updates()

        if update_info.update_available:
            console.print(
                f"\n[green]✓[/green] Update available: "
                f"v{update_info.current_version} → [bold green]v{update_info.latest_version}[/bold green]\n"
            )

            if update_info.release_url:
                console.print(f"Release URL: {update_info.release_url}")

            if update_info.release_notes:
                console.print("\n[bold]Release Notes:[/bold]")
                console.print(update_info.release_notes[:500])
                if len(update_info.release_notes) > 500:
                    console.print("\n[dim]...[/dim]")

            console.print(f"\nRun [cyan]owt update[/cyan] to install v{update_info.latest_version}")
        else:
            console.print(
                f"\n[green]✓[/green] Already up to date (v{update_info.current_version})\n"
            )
        return

    # Perform update
    if not yes:
        if target_version:
            msg = f"Update to version {target_version}?"
        else:
            console.print("[cyan]Checking for updates...[/cyan]")
            update_info = updater.check_for_updates()

            if not update_info.update_available:
                console.print(
                    f"\n[green]✓[/green] Already up to date (v{update_info.current_version})\n"
                )
                return

            msg = f"Update from v{update_info.current_version} to v{update_info.latest_version}?"

        if not click.confirm(msg):
            console.print("[yellow]Update cancelled[/yellow]")
            return

    console.print("[cyan]Updating Open Orchestrator...[/cyan]")
    success, message = updater.update(target_version)

    if success:
        console.print(f"\n[green]✓[/green] {message}\n")
    else:
        console.print(f"\n[red]✗[/red] {message}\n")
        raise click.Abort()


# =============================================================================
# Autonomous Agent Commands
# =============================================================================


@main.group("agent")
def agent_group() -> None:
    """Manage autonomous AI agents for worktrees.

    Start, stop, and monitor autonomous agents that work independently
    without user interaction.
    """


@agent_group.command("start")
@click.argument("worktree_identifier")
@click.argument("task")
@click.option(
    "--ai-tool",
    type=click.Choice(["claude", "opencode", "droid"]),
    default="claude",
    help="AI tool to use (default: claude)",
)
@click.option(
    "--plan-mode",
    is_flag=True,
    help="Start Claude in plan mode",
)
def agent_start(worktree_identifier: str, task: str, ai_tool: str, plan_mode: bool) -> None:
    """Start an autonomous agent for a worktree.

    The agent will work on the task independently, automatically handling
    workspace trust prompts and other interactive inputs.

    Example:
        owt agent start my-feature "Implement user authentication"
        owt agent start my-feature "Fix bug in checkout flow" --plan-mode
    """
    from open_orchestrator.config import AITool
    from open_orchestrator.core.auto_agent import AutoAgent, AutoAgentError

    wt_manager = get_worktree_manager()
    status_tracker = StatusTracker()

    # Find worktree
    try:
        worktree = wt_manager.get(worktree_identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    # Check if already running
    existing_status = status_tracker.get_status(worktree.name)
    if existing_status and existing_status.activity_status == AIActivityStatus.WORKING:
        raise click.ClickException(
            f"Agent is already working on {worktree.name}. Use 'owt agent stop {worktree.name}' first."
        )

    # Set up log directory
    log_dir = Path.home() / ".cache" / "open-orchestrator" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{worktree.name}_{timestamp}.log"

    # Parse AI tool
    tool_enum = AITool(ai_tool)

    try:
        console.print(f"[cyan]Starting autonomous agent for {worktree.name}...[/cyan]")
        console.print(f"Task: {task}")
        console.print(f"AI tool: {ai_tool}")
        console.print(f"Log file: {log_file}")
        console.print()

        # Create and start agent
        agent = AutoAgent(
            worktree_path=worktree.path,
            task=task,
            ai_tool=tool_enum,
            log_file=log_file,
            plan_mode=plan_mode,
        )

        # Initialize status tracking
        status_tracker.initialize_status(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            branch=worktree.branch,
            ai_tool=tool_enum,
        )

        # Start agent in background (we won't block)
        agent.start()

        # Update status
        status_tracker.update_task(worktree.name, task, AIActivityStatus.WORKING)

        console.print(f"[green]✓[/green] Autonomous agent started for {worktree.name}")
        console.print()
        console.print("[dim]Monitor with:[/dim]")
        console.print("  owt agent status")
        console.print(f"  owt agent logs {worktree.name}")

    except AutoAgentError as e:
        raise click.ClickException(f"Failed to start agent: {e}") from e


@agent_group.command("stop")
@click.argument("worktree_identifier")
@click.option(
    "--force",
    is_flag=True,
    help="Force kill the agent process",
)
def agent_stop(worktree_identifier: str, force: bool) -> None:
    """Stop a running autonomous agent.

    Example:
        owt agent stop my-feature
        owt agent stop my-feature --force
    """
    from open_orchestrator.core.process_manager import ProcessManager, ProcessNotFoundError

    wt_manager = get_worktree_manager()
    status_tracker = StatusTracker()

    try:
        worktree = wt_manager.get(worktree_identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    # Try to stop via ProcessManager
    process_manager = ProcessManager()

    try:
        console.print(f"[cyan]Stopping agent for {worktree.name}...[/cyan]")
        success = process_manager.stop_ai_tool(worktree.name, force=force)

        if success:
            console.print(f"[green]✓[/green] Agent stopped for {worktree.name}")
            status_tracker.mark_idle(worktree.name)
        else:
            console.print(f"[yellow]No running agent found for {worktree.name}[/yellow]")

    except ProcessNotFoundError:
        console.print(f"[yellow]No running agent found for {worktree.name}[/yellow]")


@agent_group.command("status")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def agent_status(json_output: bool) -> None:
    """Show status of all autonomous agents.

    Example:
        owt agent status
        owt agent status --json
    """
    status_tracker = StatusTracker()
    wt_manager = get_worktree_manager()

    worktrees = wt_manager.list_all()
    worktree_names = [wt.name for wt in worktrees]

    summary = status_tracker.get_summary(worktree_names)

    if json_output:
        output = {
            "active_agents": summary.active_ai_sessions,
            "idle_agents": summary.idle_ai_sessions,
            "blocked_agents": summary.blocked_ai_sessions,
            "agents": [
                {
                    "name": s.worktree_name,
                    "status": s.activity_status.value if hasattr(s.activity_status, "value") else str(s.activity_status),
                    "task": s.current_task,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for s in summary.statuses
            ],
        }
        console.print(json.dumps(output, indent=2))
        return

    if not summary.statuses:
        console.print("[yellow]No active agents found.[/yellow]")
        console.print("\nStart an agent with:")
        console.print('  owt agent start <worktree> "<task>"')
        return

    console.print("[bold]Autonomous Agent Status[/bold]")
    console.print()
    console.print(f"Active: {summary.active_ai_sessions} | Idle: {summary.idle_ai_sessions} | Blocked: {summary.blocked_ai_sessions}")
    console.print()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Worktree")
    table.add_column("Status")
    table.add_column("Current Task")
    table.add_column("Last Updated")

    for wt_status in summary.statuses:
        if wt_status.activity_status == AIActivityStatus.WORKING:
            status_style = "[green]working[/green]"
        elif wt_status.activity_status == AIActivityStatus.BLOCKED:
            status_style = "[red]blocked[/red]"
        else:
            status_style = "[dim]idle[/dim]"

        task = wt_status.current_task or "-"
        updated = wt_status.updated_at.strftime("%H:%M:%S") if wt_status.updated_at else "-"

        table.add_row(wt_status.worktree_name, status_style, task, updated)

    console.print(table)


@agent_group.command("logs")
@click.argument("worktree_identifier")
@click.option("-f", "--follow", is_flag=True, help="Follow log output (like tail -f)")
@click.option("-n", "--lines", default=50, help="Number of lines to show (default: 50)")
def agent_logs(worktree_identifier: str, follow: bool, lines: int) -> None:
    """View logs for an autonomous agent.

    Example:
        owt agent logs my-feature
        owt agent logs my-feature -f
        owt agent logs my-feature -n 100
    """
    from open_orchestrator.core.process_manager import ProcessManager

    wt_manager = get_worktree_manager()

    try:
        worktree = wt_manager.get(worktree_identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    process_manager = ProcessManager()
    log_path = process_manager.get_log_path(worktree.name)

    if not log_path or not log_path.exists():
        raise click.ClickException(f"No log file found for {worktree.name}")

    if follow:
        # Use tail -f to follow the log
        subprocess.run(["tail", "-f", str(log_path)], check=True)
    else:
        # Show last N lines
        subprocess.run(["tail", "-n", str(lines), str(log_path)], check=True)


@agent_group.command("health")
@click.argument("worktree_identifier", required=False)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def agent_health(worktree_identifier: str | None, json_output: bool) -> None:
    """Check health of autonomous agents and detect issues.

    Example:
        owt agent health              # Check all agents
        owt agent health my-feature   # Check specific agent
        owt agent health --json
    """
    status_tracker = StatusTracker()
    wt_manager = get_worktree_manager()

    if worktree_identifier:
        # Check single worktree
        try:
            worktree = wt_manager.get(worktree_identifier)
        except WorktreeNotFoundError as e:
            raise click.ClickException(str(e)) from e

        report = status_tracker.check_health(worktree.name)

        if json_output:
            console.print(report.model_dump_json(indent=2))
            return

        console.print(f"[bold]Health Report for {worktree.name}[/bold]")
        console.print()

        if report.healthy:
            console.print("[green]✓[/green] Agent is healthy")
        else:
            console.print("[red]✗[/red] Issues detected:")

        if report.issues:
            console.print()
            for issue in report.issues:
                severity_color = "red" if issue.severity.value == "critical" else "yellow"
                console.print(f"[{severity_color}]{issue.severity.value.upper()}[/{severity_color}] {issue.message}")
                console.print(f"  → {issue.recommendation}")
                console.print()

    else:
        # Check all worktrees
        health_summary = status_tracker.check_all_health()

        if json_output:
            console.print(health_summary.model_dump_json(indent=2))
            return

        console.print("[bold]Health Summary Across All Agents[/bold]")
        console.print()
        console.print(f"Total: {health_summary.total_worktrees}")
        console.print(f"[green]Healthy: {health_summary.healthy_worktrees}[/green]")
        console.print(f"[yellow]Warnings: {health_summary.worktrees_with_warnings}[/yellow]")
        console.print(f"[red]Critical: {health_summary.worktrees_with_critical_issues}[/red]")
        console.print()

        if health_summary.worktrees_with_critical_issues > 0:
            console.print("[bold]Critical Issues:[/bold]")
            for report in health_summary.reports:
                if report.critical_issues:
                    console.print(f"  [red]✗[/red] {report.worktree_name}")
                    for issue in report.critical_issues:
                        console.print(f"      {issue.message}")


# --- Command Aliases (Short Forms) ---
# These provide dmux-like shorthand for common operations.

main.add_command(new_worktree, "n")           # owt n  → owt new
main.add_command(list_worktrees, "ls")        # owt ls → owt list
main.add_command(delete_worktree, "rm")       # owt rm → owt delete
main.add_command(show_status, "st")            # owt st → owt status
main.add_command(merge_worktree, "m")         # owt m  → owt merge
main.add_command(close_worktree, "x")         # owt x  → owt close


if __name__ == "__main__":
    main()
