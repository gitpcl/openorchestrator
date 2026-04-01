"""Worktree CRUD commands: new, list, switch, delete."""

from __future__ import annotations

import logging
import time

import click
from rich.table import Table

from open_orchestrator.commands._shared import console, get_status_tracker, get_worktree_manager
from open_orchestrator.config import AITool, load_config
from open_orchestrator.core.worktree import (
    WorktreeError,
    WorktreeNotFoundError,
)
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


def register(main: click.Group) -> None:
    """Register worktree commands on the main CLI group."""

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
            logger.debug("Git ref conflict check failed", exc_info=True)

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
        except WorktreeError as e:
            raise click.ClickException(str(e)) from e

        console.print(f"[green]Worktree created:[/green] {worktree.path}")

        # 2. Set up environment
        from open_orchestrator.core.environment import EnvironmentSetup, EnvironmentSetupError
        from open_orchestrator.core.project_detector import ProjectDetector

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

        tracker = get_status_tracker(wt_manager.git_root)
        hooks_installed = install_hooks(
            worktree.path,
            worktree.name,
            ai_tool_enum,
            db_path=tracker.storage_path,
        )
        if hooks_installed:
            console.print(f"[green]Hooks installed:[/green] {ai_tool_enum.value} → owt status")

        # 4. Create tmux session + start AI tool (skip in headless mode)
        from open_orchestrator.core.tmux_manager import TmuxError, TmuxManager, TmuxSessionExistsError

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
                tracker.update_task(worktree.name, task_description[:100])
            except Exception as e:
                console.print(f"[yellow]Could not send prompt: {e}[/yellow]")

        # 7. Send template instructions
        if tmpl_instructions and session_name:
            try:
                tmux_manager.send_keys_to_pane(session_name=session_name, keys=tmpl_instructions)
            except Exception:
                logger.debug("Failed to send template instructions", exc_info=True)

        # 8. Attach if requested
        if attach and session_name:
            if tmux_manager.is_inside_tmux():
                tmux_manager.switch_client(session_name)
            else:
                tmux_manager.attach(session_name)

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

    @main.command("switch")
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
