"""``owt new`` — create a worktree (or branch session) + agent."""

from __future__ import annotations

import click

from open_orchestrator.commands import worktree as _pkg
from open_orchestrator.commands._shared import console
from open_orchestrator.core.agent_launcher import LaunchMode, LaunchRequest
from open_orchestrator.core.pane_actions import PaneActionError
from open_orchestrator.models.backend import BackendKind
from open_orchestrator.models.worktree_info import SessionType


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
@click.option(
    "--workflow",
    is_flag=True,
    help="Launch a native plan-first Claude Code workflow (plan mode + plan-then-execute protocol).",
)
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
    workflow: bool,
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
        owt new "Refactor the billing module" --workflow  # native plan-first workflow
        owt new "Port parser to Rust" --ai-tool droid     # supervise a different provider
        owt new --branch feat/my-branch
        owt new "Fix bug" --in-place
        owt branch "Fix bug"  # alias for owt new ... --in-place
    """
    if headless and branch_mode:
        raise click.ClickException("--headless and --in-place cannot be used together.")
    if workflow and headless:
        raise click.ClickException("--workflow is plan-first and interactive; it can't run --headless.")
    if force_herdr and force_tmux:
        raise click.ClickException("--herdr and --tmux are mutually exclusive.")
    # --workflow is a high-level alias: native plan-first launch.
    if workflow:
        plan_mode = True
    if force_herdr and headless:
        raise click.ClickException("--herdr is incompatible with --headless (no terminal to host).")

    cfg = _pkg.load_config_safe()

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

    task_description, branch = _pkg._resolve_branch(description, explicit_branch, prefix)
    branch = _pkg._check_git_ref_conflicts(branch)

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

    ai_tool_name = _pkg._resolve_ai_tool(ai_tool)
    tool = _pkg.get_registry().get(ai_tool_name)
    if tool is None:
        raise click.ClickException(f"Unknown AI tool '{ai_tool_name}'. Registered: {_pkg.get_registry().list_names()}")

    if headless and not tool.supports_headless:
        raise click.ClickException(
            f"Headless mode is not supported by '{ai_tool_name}'. The tool needs a non-interactive execution mode plus OWT hooks."
        )

    if workflow and not tool.supports_plan_mode:
        raise click.ClickException(
            f"--workflow needs a plan-mode-capable tool; '{ai_tool_name}' doesn't support plan mode. Try --ai-tool claude."
        )

    prompt = task_description or None
    if tmpl_instructions:
        prompt = f"{tmpl_instructions}\n\n{task_description}" if task_description else tmpl_instructions

    # --workflow frames the task with a plan-first, task-type-specific protocol
    # so the native agent plans before it executes.
    display_task = task_description or None
    if workflow and task_description:
        from open_orchestrator.core.prompt_builder import get_protocol_for_task

        protocol = get_protocol_for_task(task_description)
        prompt = f"{protocol}\n\n{prompt}" if prompt else protocol
        display_task = f"⟳ {task_description}"

    mode = LaunchMode.HEADLESS if headless else LaunchMode.INTERACTIVE
    wt_manager = _pkg.get_worktree_manager()
    tracker = _pkg.get_status_tracker(wt_manager.git_root)
    launcher = _pkg.AgentLauncher(
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
        display_task=display_task,
        plan_mode=plan_mode,
        session_type=SessionType.BRANCH if branch_mode else SessionType.WORKTREE,
        backend_kind=BackendKind(resolved_kind.value),
    )

    try:
        result = launcher.launch(request)
    except PaneActionError as e:
        raise click.ClickException(str(e)) from e

    tracker.record_usage("new")
    if workflow:
        tracker.record_usage("workflow")

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
