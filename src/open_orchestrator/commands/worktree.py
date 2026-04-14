"""Worktree CRUD commands: new, list, switch, delete."""

from __future__ import annotations

import logging

import click
from rich.table import Table

from open_orchestrator.commands._shared import console, get_status_tracker, get_worktree_manager
from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest
from open_orchestrator.core.pane_actions import PaneActionError
from open_orchestrator.core.tool_registry import get_registry
from open_orchestrator.core.worktree import WorktreeNotFoundError
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


def _resolve_ai_tool(ai_tool: str | None) -> str:
    """Auto-detect AI tool if not specified. Returns tool name string."""
    if ai_tool is not None:
        return ai_tool

    from open_orchestrator.core.agent_detector import detect_installed_agents

    installed = detect_installed_agents()
    if len(installed) == 0:
        raise click.ClickException("No AI coding tools found. Install claude, opencode, or droid.")
    if len(installed) == 1:
        return installed[0]

    console.print("\n[bold]Detected AI tools:[/bold]")
    for i, name in enumerate(installed, 1):
        console.print(f"  {i}. {name}")
    choice: int = click.prompt("Select AI tool", type=click.IntRange(1, len(installed)), default=1)
    return installed[choice - 1]


def _resolve_branch(
    description: tuple[str, ...],
    explicit_branch: str | None,
    prefix: str | None,
) -> tuple[str, str]:
    """Resolve task description and branch name. Returns (task_description, branch)."""
    from open_orchestrator.core.branch_namer import generate_branch_name

    if description:
        task_description = " ".join(description)
    elif explicit_branch:
        task_description = ""
    else:
        task_description = click.prompt("What are you working on?")

    if explicit_branch:
        return task_description, explicit_branch

    if not task_description.strip():
        raise click.ClickException("Task description cannot be empty")
    try:
        branch = generate_branch_name(task_description, prefix=prefix)
    except ValueError as e:
        raise click.ClickException(f"Could not generate branch name: {e}") from e
    return task_description, branch


def _check_git_ref_conflicts(branch: str) -> str:
    """Check for git ref conflicts and prompt for alternative if needed."""
    from git import Repo

    try:
        repo = Repo(search_parent_directories=True)
        existing_refs = {ref.name for ref in repo.refs}
        branch_parts = branch.split("/")
        for i in range(1, len(branch_parts)):
            partial = "/".join(branch_parts[:i])
            if partial in existing_refs:
                console.print(f"[yellow]Branch '{partial}' exists \u2014 cannot create '{branch}' (git ref conflict).[/yellow]")
                result: str = click.prompt("Enter a different branch name")
                return result
    except Exception:
        logger.debug("Git ref conflict check failed", exc_info=True)
    return branch


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.command("new")
@click.argument("description", nargs=-1)
@click.option("-b", "--base", "base_branch", help="Base branch for the new worktree.")
@click.option("--branch", "explicit_branch", help="Use this branch name instead of auto-generating.")
@click.option(
    "--ai-tool",
    default=None,
    help="AI tool to start by registered name (auto-detected if not specified).",
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
    task_description, branch = _resolve_branch(description, explicit_branch, prefix)
    branch = _check_git_ref_conflicts(branch)

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
                ai_tool = tmpl.ai_tool
            if tmpl.plan_mode:
                plan_mode = True
            if base_branch is None and tmpl.base_branch:
                base_branch = tmpl.base_branch

    ai_tool_name = _resolve_ai_tool(ai_tool)
    tool = get_registry().get(ai_tool_name)
    if tool is None:
        raise click.ClickException(
            f"Unknown AI tool '{ai_tool_name}'. Registered: {get_registry().list_names()}"
        )

    if headless and not tool.supports_headless:
        raise click.ClickException(
            f"Headless mode is not supported by '{ai_tool_name}'. "
            "The tool needs a non-interactive execution mode plus OWT hooks."
        )

    prompt = task_description or None
    if tmpl_instructions:
        prompt = f"{tmpl_instructions}\n\n{task_description}" if task_description else tmpl_instructions

    mode = LaunchMode.HEADLESS if headless else LaunchMode.INTERACTIVE
    wt_manager = get_worktree_manager()
    tracker = get_status_tracker(wt_manager.git_root)
    launcher = AgentLauncher(
        repo_path=str(wt_manager.git_root),
        wt_manager=wt_manager,
        status_tracker=tracker,
    )
    request = LaunchRequest(
        branch=branch,
        base_branch=base_branch,
        ai_tool=ai_tool_name,
        mode=mode,
        prompt=prompt,
        display_task=task_description or None,
        plan_mode=plan_mode,
    )

    try:
        result = launcher.launch(request)
    except PaneActionError as e:
        raise click.ClickException(str(e)) from e

    console.print(f"[green]Worktree created:[/green] {result.worktree_path}")
    if result.tmux_session:
        console.print(f"[green]tmux session:[/green] {result.tmux_session}")
    if result.subprocess_pid is not None:
        console.print(f"[cyan]Headless agent launched:[/cyan] PID {result.subprocess_pid}")
    if prompt and result.tmux_session:
        preview = task_description[:80] + ("..." if len(task_description) > 80 else "")
        console.print(f"[cyan]Sent task:[/cyan] {preview}")
    for warn in result.warnings:
        console.print(f"[yellow]{warn}[/yellow]")

    # Attach to tmux if requested
    if attach and result.tmux_session:
        from open_orchestrator.core.tmux_manager import TmuxManager

        tmux_manager = TmuxManager()
        if tmux_manager.is_inside_tmux():
            tmux_manager.switch_client(result.tmux_session)
        else:
            tmux_manager.attach(result.tmux_session)


@click.command("list")
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

    from open_orchestrator.core.tmux_manager import TmuxManager

    tracker = get_status_tracker(wt_manager.git_root)
    all_statuses = {s.worktree_name: s for s in tracker.get_all_statuses()}
    tmux = TmuxManager()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Branch")
    table.add_column("Status")
    table.add_column("Task")
    table.add_column("tmux")

    for wt in worktrees:
        status = all_statuses.get(wt.name)
        status_str = ""
        task_str = ""
        tmux_str = ""

        if status:
            act = status.activity_status
            if act == AIActivityStatus.WORKING:
                status_str = "[green]\u25cf working[/green]"
            elif act == AIActivityStatus.IDLE:
                status_str = "[dim]\u25cb idle[/dim]"
            elif act == AIActivityStatus.BLOCKED:
                status_str = "[yellow]\u26a0 blocked[/yellow]"
            elif act == AIActivityStatus.COMPLETED:
                status_str = "[cyan]\u2713 done[/cyan]"
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


@click.command("switch")
@click.argument("identifier")
def switch_worktree(identifier: str) -> None:
    """Jump to a worktree's tmux session.

    If inside tmux, switches the current client.
    If outside, attaches to the session.
    """
    from open_orchestrator.core.tmux_manager import TmuxManager

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


@click.command("delete")
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


def register(main: click.Group) -> None:
    """Register worktree commands on the main CLI group."""
    main.add_command(new_worktree)
    main.add_command(list_worktrees)
    main.add_command(switch_worktree)
    main.add_command(delete_worktree)
