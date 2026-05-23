"""Worktree CRUD commands: new, list, switch, delete."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import click
from rich.table import Table

from open_orchestrator.commands._shared import (
    console,
    get_status_tracker,
    get_worktree_manager,
    resolve_session_target,
)
from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest
from open_orchestrator.core.pane_actions import PaneActionError
from open_orchestrator.core.tool_registry import get_registry
from open_orchestrator.models.backend import BackendKind
from open_orchestrator.models.status import AIActivityStatus
from open_orchestrator.models.worktree_info import SessionType

if TYPE_CHECKING:
    from open_orchestrator.config import Config

logger = logging.getLogger(__name__)


def _resolve_ai_tool(ai_tool: str | None) -> str:
    """Auto-detect AI tool if not specified. Returns tool name string."""
    if ai_tool is not None:
        return ai_tool

    from open_orchestrator.core.agent_detector import detect_installed_agents

    installed = detect_installed_agents()
    if len(installed) == 0:
        raise click.ClickException("No AI coding tools found. Install claude, pi, opencode, or droid.")
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


def load_config_safe() -> Config:
    """Load config, falling back to defaults on any error."""
    from open_orchestrator.config import Config, load_config

    try:
        return load_config()
    except Exception:  # noqa: BLE001
        return Config()


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
@click.option(
    "--in-place",
    "branch_mode",
    is_flag=True,
    help="Create branch in current checkout instead of a git worktree.",
)
@click.option("--herdr", "force_herdr", is_flag=True, help="Use the herdr multiplexer backend.")
@click.option("--tmux", "force_tmux", is_flag=True, help="Use the tmux backend (default).")
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
    branch_mode: bool,
    force_herdr: bool = False,
    force_tmux: bool = False,
) -> None:
    """Create a worktree + tmux session + deps + AI agent. One command.

    Automatically generates a branch name from your task description,
    creates the worktree, installs deps, copies .env, starts the AI tool.

    Use --in-place to create a branch in the current checkout instead of
    a separate git worktree (faster, no extra disk space).

    Examples:
        owt new Add user authentication with JWT
        owt new Fix login redirect bug
        owt new "Refactor database queries" --plan-mode
        owt new --branch feat/my-branch
        owt new "Fix bug" --in-place
        owt branch "Fix bug"  # alias for owt new ... --in-place
    """
    if headless and branch_mode:
        raise click.ClickException("--headless and --in-place cannot be used together.")
    if force_herdr and force_tmux:
        raise click.ClickException("--herdr and --tmux are mutually exclusive.")
    if force_herdr and headless:
        raise click.ClickException("--herdr is incompatible with --headless (no terminal to host).")

    cfg = load_config_safe()

    # Backend resolution only matters for interactive/automated paths —
    # the headless path runs a detached subprocess and never touches a
    # multiplexer. Resolving here would make `[backend] mode = "herdr"`
    # + `--headless` fail without herdr installed (Sprint 026 P6).
    resolved_backend = None
    resolved_kind = BackendKind.TMUX
    if not headless:
        if force_herdr:
            backend_kind_override = "herdr"
        elif force_tmux:
            backend_kind_override = "tmux"
        else:
            backend_kind_override = cfg.backend.mode if hasattr(cfg, "backend") else "tmux"

        from open_orchestrator.core.backend_factory import BackendUnavailableError, select_backend

        try:
            resolved_backend = select_backend(
                getattr(cfg, "backend", None),
                override=backend_kind_override if backend_kind_override != "auto" else None,
            )
        except BackendUnavailableError as err:
            raise click.ClickException(str(err)) from err
        resolved_kind = resolved_backend.kind

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
        raise click.ClickException(f"Unknown AI tool '{ai_tool_name}'. Registered: {get_registry().list_names()}")

    if headless and not tool.supports_headless:
        raise click.ClickException(
            f"Headless mode is not supported by '{ai_tool_name}'. The tool needs a non-interactive execution mode plus OWT hooks."
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
        # Inject the already-resolved backend so the launcher doesn't
        # re-resolve and risk a second herdr socket probe. None on the
        # headless path because that code path never uses a backend.
        backend=resolved_backend,
    )
    request = LaunchRequest(
        branch=branch,
        base_branch=base_branch,
        ai_tool=ai_tool_name,
        mode=mode,
        prompt=prompt,
        display_task=task_description or None,
        plan_mode=plan_mode,
        session_type=SessionType.BRANCH if branch_mode else SessionType.WORKTREE,
        backend_kind=BackendKind(resolved_kind.value),
    )

    try:
        result = launcher.launch(request)
    except PaneActionError as e:
        raise click.ClickException(str(e)) from e

    if branch_mode:
        console.print(f"[green]Branch session created:[/green] {result.worktree_name}")
    else:
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

    # Attach to the new session via the backend used to create it.
    if attach and result.backend_session_id and resolved_backend is not None:
        from open_orchestrator.models.backend import BackendSession

        session = BackendSession(
            kind=result.backend_kind,
            id=result.backend_session_id,
            worktree_name=result.worktree_name,
        )
        resolved_backend.attach(session)


@click.command("list")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show all worktrees including main.")
def list_worktrees(show_all: bool) -> None:
    """List all worktrees and branch sessions with status.

    Quick text list (non-interactive, for scripts/pipes).
    Shows branch-mode sessions alongside git worktrees.
    """
    wt_manager = get_worktree_manager()
    worktrees = wt_manager.list_all()

    tracker = get_status_tracker(wt_manager.git_root)
    all_statuses = {s.worktree_name: s for s in tracker.get_all_statuses()}

    # Collect branch-mode sessions from status DB (not in git worktree list)
    worktree_names = {wt.name for wt in worktrees}
    branch_sessions: list[dict[str, str]] = []
    for s in tracker.get_all_statuses():
        if s.worktree_name not in worktree_names and s.branch:
            branch_sessions.append(
                {
                    "name": s.worktree_name,
                    "branch": s.branch,
                }
            )

    if not show_all:
        worktrees = [wt for wt in worktrees if not wt.is_main]

    if not worktrees and not branch_sessions:
        console.print("[dim]No worktrees or branch sessions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Branch")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Task")
    table.add_column("Session")

    for wt in worktrees:
        status = all_statuses.get(wt.name)
        status_str = ""
        task_str = ""
        session_str = ""

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
            session_id = status.backend_session_id or status.tmux_session or ""
            session_str = f"{status.backend_kind}:{session_id}" if session_id else ""

        name = "[bold]" + wt.name + "[/bold]" if wt.is_main else wt.name
        type_str = "[bold]main[/bold]" if wt.is_main else "[dim]worktree[/dim]"
        table.add_row(name, wt.branch, type_str, status_str, task_str, session_str)

    for bs in branch_sessions:
        status = all_statuses.get(bs["name"])
        status_str = ""
        task_str = ""
        session_str = ""
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
            session_id = status.backend_session_id or status.tmux_session or ""
            session_str = f"{status.backend_kind}:{session_id}" if session_id else ""
        table.add_row(
            bs["name"],
            bs["branch"],
            "[cyan]branch[/cyan]",
            status_str,
            task_str,
            session_str,
        )

    console.print(table)


@click.command("switch")
@click.argument("identifier")
def switch_worktree(identifier: str) -> None:
    """Jump to a worktree's session via its backend (tmux or herdr).

    Works for both worktree-mode and branch-mode sessions. Backend is
    resolved from the status DB row written at create-time so no flag is
    needed here — herdr-created sessions hand off to herdr, tmux-created
    sessions hand off to tmux.
    """
    from open_orchestrator.core.backend_factory import select_backend, select_backend_for_session

    wt_manager = get_worktree_manager()
    tracker = get_status_tracker(wt_manager.git_root)
    resolved = resolve_session_target(identifier, wt_manager, tracker)

    session = tracker.get_backend_session(resolved.name)
    if session is None:
        # Legacy row or no row: fall back to tmux + session_for lookup.
        backend = select_backend(load_config_safe().backend, override="tmux")
        session = backend.session_for(resolved.name)
        if session is None:
            raise click.ClickException(f"No session found for '{resolved.name}'. Run 'owt new' to create one.")
    else:
        backend = select_backend_for_session(session)
        if not backend.is_alive(session):
            raise click.ClickException(f"No {session.kind.value} session for '{resolved.name}'. Run 'owt new' to create one.")
    backend.attach(session)


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
    wt_manager = get_worktree_manager()
    tracker = get_status_tracker(wt_manager.git_root)
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


@click.command("attach")
@click.argument("identifier")
@click.option("--herdr", "force_herdr", is_flag=True, help="Force herdr backend.")
@click.option("--tmux", "force_tmux", is_flag=True, help="Force tmux backend.")
def attach_worktree(identifier: str, force_herdr: bool, force_tmux: bool) -> None:
    """Hand off to a worktree's session via the active backend.

    By default reads the backend kind recorded at create-time so
    herdr-created sessions hand off to herdr and tmux-created sessions
    hand off to tmux.

    Pass ``--herdr`` / ``--tmux`` to force a specific backend. When the
    forced backend differs from the recorded backend, owt re-resolves
    the session via ``backend.session_for(name)`` instead of coercing
    the recorded id (Sprint 026 P4 — the id formats are different, so
    coercing would silently misroute the attach).
    """
    from open_orchestrator.config import load_config
    from open_orchestrator.core.backend_factory import (
        BackendUnavailableError,
        select_backend,
        select_backend_for_session,
    )

    if force_herdr and force_tmux:
        raise click.ClickException("--herdr and --tmux are mutually exclusive.")
    override = "herdr" if force_herdr else "tmux" if force_tmux else None

    wt_manager = get_worktree_manager()
    tracker = get_status_tracker(wt_manager.git_root)
    resolved = resolve_session_target(identifier, wt_manager, tracker)

    from open_orchestrator.models.backend import BackendSession

    recorded_session = tracker.get_backend_session(resolved.name)
    session: BackendSession | None

    # No override: prefer the recorded session via its native backend.
    if override is None:
        if recorded_session is not None:
            backend = select_backend_for_session(recorded_session)
            session = recorded_session
        else:
            try:
                backend = select_backend(load_config().backend, override="tmux")
            except BackendUnavailableError as err:
                raise click.ClickException(str(err)) from err
            session = backend.session_for(resolved.name)
            if session is None:
                raise click.ClickException(f"No session for '{resolved.name}'. Run 'owt new' to create one.")
        backend.attach(session)
        return

    # Forced override: re-resolve via the forced backend rather than
    # coercing the recorded session (recorded ids are backend-specific).
    try:
        backend = select_backend(load_config().backend, override=override)
    except BackendUnavailableError as err:
        raise click.ClickException(str(err)) from err

    if recorded_session is not None and recorded_session.kind.value != override:
        # Re-resolve under the forced backend; do not pass the recorded id.
        session = backend.session_for(resolved.name)
        if session is None:
            raise click.ClickException(
                f"No {override} session for '{resolved.name}'. Recorded as {recorded_session.kind.value}. "
                f"Drop --{override} to use the recorded backend."
            )
    else:
        session = recorded_session or backend.session_for(resolved.name)
        if session is None:
            raise click.ClickException(f"No {backend.kind.value} session for '{resolved.name}'. Run 'owt new' to create one.")
    backend.attach(session)


def register(main: click.Group) -> None:
    """Register worktree commands on the main CLI group."""
    main.add_command(new_worktree)
    main.add_command(list_worktrees)
    main.add_command(switch_worktree)
    main.add_command(delete_worktree)
    main.add_command(branch_cmd)
    main.add_command(attach_worktree)
