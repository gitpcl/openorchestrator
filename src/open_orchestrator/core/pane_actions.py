"""Shared pane lifecycle logic for CLI.

Extracts the business logic from CLI pane commands into reusable functions.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from open_orchestrator.config import AITool, load_config
from open_orchestrator.core.environment import EnvironmentSetup, EnvironmentSetupError
from open_orchestrator.core.project_detector import ProjectDetector
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.tmux_manager import TmuxError, TmuxManager
from open_orchestrator.core.worktree import WorktreeAlreadyExistsError, WorktreeError, WorktreeManager

logger = logging.getLogger(__name__)


@dataclass
class PaneResult:
    """Result of a pane creation operation."""

    worktree_name: str
    worktree_path: str
    branch: str
    pane_index: int
    ai_tool: AITool


class PaneActionError(Exception):
    """Raised when a pane action fails."""


def popup_result_path(workspace_name: str) -> str:
    """Get the temp file path for popup picker results."""
    import tempfile

    user_tmp = Path(tempfile.gettempdir()) / f"owt-{os.getuid()}"
    user_tmp.mkdir(mode=0o700, exist_ok=True)
    return str(user_tmp / f"owt-popup-{workspace_name}.json")


def read_popup_result(popup_file: str, cleanup: bool = True) -> dict:
    """Read and parse a popup picker result file."""
    from open_orchestrator.utils.io import safe_read_json

    data = safe_read_json(popup_file)
    if data is None:
        raise PaneActionError(f"Could not read popup result: {popup_file}")

    if cleanup:
        try:
            os.unlink(popup_file)
        except OSError:
            pass

    return data


def create_pane(
    session_name: str,
    repo_path: str,
    branch: str,
    ai_tool: AITool = AITool.CLAUDE,
    template_name: str | None = None,
    plan_mode: bool = False,
    ai_instructions: str | None = None,
) -> PaneResult:
    """Create a worktree and add it as a pane to the tmux session.

    Orchestrates the full lifecycle:
    1. Create git worktree
    2. Set up environment (deps, .env, CLAUDE.md)
    3. Create tmux session for the worktree
    4. Initialize status tracking

    Args:
        session_name: tmux session name (used for status tracking).
        repo_path: Path to the main repository.
        branch: Branch name for the worktree.
        ai_tool: AI tool to start.
        template_name: Optional worktree template name.
        plan_mode: Start Claude in plan mode.
        ai_instructions: Optional AI instructions to send after creation.

    Returns:
        PaneResult with details of the created pane.

    Raises:
        PaneActionError: If any step fails fatally.
    """
    config = load_config()
    ai_tool_enum = ai_tool

    # Check for duplicate
    wt_manager = WorktreeManager(repo_path=repo_path)
    existing_names = {wt.name for wt in wt_manager.list_all()}
    candidate_name = branch.split("/")[-1] if "/" in branch else branch
    if candidate_name in existing_names:
        raise PaneActionError(
            f"A worktree named '{candidate_name}' already exists. Use a different branch name."
        )

    # Resolve template
    if template_name:
        from open_orchestrator.config import get_builtin_templates

        templates = get_builtin_templates()
        tmpl = templates.get(template_name)
        if tmpl:
            if not ai_instructions:
                ai_instructions = tmpl.ai_instructions
            if tmpl.ai_tool:
                ai_tool_enum = tmpl.ai_tool

    # 1. Create the worktree
    try:
        worktree = wt_manager.create(branch=branch)
    except WorktreeAlreadyExistsError:
        worktree = wt_manager.get(branch)
    except WorktreeError as e:
        raise PaneActionError(f"Failed to create worktree: {e}") from e

    # 2. Set up environment
    try:
        project_config = ProjectDetector().detect(str(worktree.path))
        if project_config:
            EnvironmentSetup(project_config).setup_worktree(
                worktree_path=str(worktree.path),
                source_path=repo_path,
                install_deps=config.environment.auto_install_deps,
                copy_env=config.environment.copy_env_file,
            )
    except EnvironmentSetupError as e:
        logger.warning("Environment setup issue: %s", e)

    # 3. Create tmux session
    tmux_manager = TmuxManager()
    try:
        tmux_session = tmux_manager.create_worktree_session(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            ai_tool=ai_tool_enum,
            plan_mode=plan_mode,
        )
        pane_index = 0
    except TmuxError as e:
        raise PaneActionError(f"Failed to create session: {e}") from e

    # 4. Send task instructions (poll for AI tool prompt readiness)
    prompt_ready = False
    if ai_instructions:
        import subprocess
        import time

        # Poll tmux pane until the AI tool's input prompt appears
        prompt_ready = False
        for _ in range(30):  # up to 15 seconds (30 × 0.5s)
            time.sleep(0.5)
            try:
                result = subprocess.run(
                    ["tmux", "capture-pane", "-t", tmux_session.session_name, "-p"],
                    capture_output=True, text=True, timeout=2,
                )
                if result.returncode == 0:
                    content = result.stdout.strip()
                    # Check for known AI tool input prompts on last lines
                    last_lines = content.split("\n")[-8:]
                    last_text = "\n".join(last_lines)
                    if "❯" in last_text or "How can I help" in last_text:
                        prompt_ready = True
                        break
            except (subprocess.TimeoutExpired, OSError):
                pass

        if prompt_ready:
            time.sleep(1)  # Let prompt fully render before sending
            try:
                # Send text and Enter separately — libtmux's enter=True
                # fires too fast after a large paste, so Claude Code
                # doesn't register the Enter.
                session = tmux_manager.server.sessions.filter(
                    session_name=tmux_session.session_name
                )[0]
                pane = session.windows[0].panes[0]
                pane.send_keys(ai_instructions, enter=False)
                time.sleep(0.5)
                pane.send_keys("", enter=True)
            except Exception:
                logger.warning("Failed to send instructions to %s", worktree.name)
        else:
            logger.warning("AI tool prompt not detected for %s after 15s", worktree.name)

    # 5. Install AI tool hooks for status reporting
    try:
        from open_orchestrator.core.hooks import install_hooks

        install_hooks(worktree.path, worktree.name, ai_tool_enum)
    except Exception as e:
        logger.debug("Hook installation skipped: %s", e)

    # 6. Initialize status tracking
    initial_status = "working" if (ai_instructions and prompt_ready) else "idle"
    try:
        status_tracker = StatusTracker()
        status_tracker.initialize_status(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            branch=worktree.branch,
            tmux_session=tmux_session.session_name,
            ai_tool=ai_tool_enum,
        )
        if initial_status == "working":
            from open_orchestrator.models.status import AIActivityStatus

            status_tracker.update_task(
                worktree.name,
                (ai_instructions or "")[:100],
                AIActivityStatus.WORKING,
            )
    except Exception as e:
        logger.debug("Status tracking init skipped: %s", e)

    return PaneResult(
        worktree_name=worktree.name,
        worktree_path=str(worktree.path),
        branch=worktree.branch,
        pane_index=pane_index,
        ai_tool=ai_tool_enum,
    )


def remove_pane(
    worktree_name: str,
    repo_path: str | None = None,
) -> None:
    """Delete a worktree, its tmux session, and clean up status.

    Args:
        worktree_name: Name of the worktree to remove.
        repo_path: Path to main repo (needed to delete worktree).

    Raises:
        PaneActionError: If the worktree cannot be found or removed.
    """
    tmux_manager = TmuxManager()

    # 1. Kill tmux session
    session_name = tmux_manager.generate_session_name(worktree_name)
    try:
        if tmux_manager.session_exists(session_name):
            tmux_manager.kill_session(session_name)
    except TmuxError as e:
        logger.warning("Could not kill tmux session: %s", e)

    # 2. Delete git worktree
    if repo_path:
        try:
            wt_manager = WorktreeManager(repo_path=repo_path)
            wt_manager.delete(worktree_name)
        except WorktreeError as e:
            logger.warning("Could not delete worktree: %s", e)

    # 3. Clean up status tracking
    try:
        status_tracker = StatusTracker()
        status_tracker.remove_status(worktree_name)
    except Exception:
        pass
