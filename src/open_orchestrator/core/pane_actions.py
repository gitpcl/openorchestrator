"""Shared pane lifecycle logic for CLI and TUI.

Extracts the business logic from CLI pane_add/pane_remove commands into
reusable functions that can be called from both the CLI and the Textual TUI.
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
from open_orchestrator.core.workspace import WorkspaceManager, WorkspaceNotFoundError
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
    """Get the temp file path for popup picker results.

    Uses a user-specific directory to prevent symlink/TOCTOU attacks.

    Args:
        workspace_name: Workspace / session name.

    Returns:
        Path to a user-owned temp file for popup results.
    """
    import tempfile

    user_tmp = Path(tempfile.gettempdir()) / f"owt-{os.getuid()}"
    user_tmp.mkdir(mode=0o700, exist_ok=True)
    return str(user_tmp / f"owt-popup-{workspace_name}.json")


def read_popup_result(popup_file: str, cleanup: bool = True) -> dict:
    """Read and parse a popup picker result file.

    Uses safe_read_json for consistent error handling, then optionally
    cleans up the temp file to prevent stale results.

    Args:
        popup_file: Path to the JSON file written by owt-popup.
        cleanup: Remove the file after reading (default True).

    Returns:
        Dict with keys like 'branch', 'ai_tool', 'template'.

    Raises:
        PaneActionError: If file cannot be read or parsed.
    """
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
    workspace_name: str,
    repo_path: str,
    branch: str,
    ai_tool: AITool = AITool.CLAUDE,
    template_name: str | None = None,
    plan_mode: bool = False,
    ai_instructions: str | None = None,
) -> PaneResult:
    """Create a worktree and add it as a pane to the workspace.

    Orchestrates the full lifecycle:
    1. Create git worktree
    2. Set up environment (deps, .env, CLAUDE.md)
    3. Add tmux pane to session
    4. Track in workspace store
    5. Send template instructions if any
    6. Initialize status tracking

    Args:
        workspace_name: tmux session / workspace name.
        repo_path: Path to the main repository.
        branch: Branch name for the worktree.
        ai_tool: AI tool to start in the pane.
        template_name: Optional worktree template name.
        plan_mode: Start Claude in plan mode.
        ai_instructions: Optional AI instructions to send after pane creation.

    Returns:
        PaneResult with details of the created pane.

    Raises:
        PaneActionError: If any step fails fatally.
    """
    config = load_config()
    ai_tool_enum = ai_tool

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
    wt_manager = WorktreeManager(repo_path=repo_path)
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

    # 3. Add pane to tmux session
    tmux_manager = TmuxManager()
    try:
        pane_index = tmux_manager.add_worktree_pane(
            session_name=workspace_name,
            worktree_path=str(worktree.path),
            worktree_name=worktree.name,
            ai_tool=ai_tool_enum,
            plan_mode=plan_mode,
        )
    except TmuxError as e:
        raise PaneActionError(f"Failed to add pane: {e}") from e

    # 4. Track in workspace store
    try:
        workspace_manager = WorkspaceManager()
        workspace_manager.add_worktree_pane(
            workspace_name=workspace_name,
            pane_index=pane_index,
            worktree_name=worktree.name,
            worktree_path=worktree.path,
        )
    except Exception as e:
        logger.warning("Could not track pane in workspace store: %s", e)

    # 5. Send template instructions if available
    if ai_instructions:
        try:
            tmux_manager.send_keys_to_pane(
                session_name=workspace_name,
                keys=ai_instructions,
                pane_index=pane_index,
            )
        except TmuxError:
            pass

    # 6. Initialize status tracking
    try:
        status_tracker = StatusTracker()
        status_tracker.initialize_status(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            branch=worktree.branch,
            tmux_session=workspace_name,
            ai_tool=ai_tool_enum,
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
    workspace_name: str,
    worktree_name: str,
    repo_path: str | None = None,
    keep_worktree: bool = False,
    pane_index: int | None = None,
) -> None:
    """Remove a pane from the workspace and optionally delete its worktree.

    Args:
        workspace_name: Workspace / tmux session name.
        worktree_name: Name of the worktree to remove.
        repo_path: Path to main repo (needed to delete worktree).
        keep_worktree: If True, keep the git worktree on disk.
        pane_index: tmux pane index (resolved from workspace if not provided).

    Raises:
        PaneActionError: If the pane cannot be found or removed.
    """
    workspace_manager = WorkspaceManager()
    try:
        workspace = workspace_manager.get_workspace(workspace_name)
    except WorkspaceNotFoundError as e:
        raise PaneActionError(f"Workspace '{workspace_name}' not found") from e

    # Resolve pane index from worktree name
    target_pane = workspace.get_pane_by_worktree(worktree_name)
    if not target_pane:
        raise PaneActionError(f"Worktree '{worktree_name}' not found in workspace")

    resolved_pane_index = pane_index if pane_index is not None else target_pane.pane_index

    # 1. Remove pane from tmux
    tmux_manager = TmuxManager()
    try:
        tmux_manager.remove_pane(workspace_name, resolved_pane_index)
    except TmuxError as e:
        logger.warning("Could not remove tmux pane: %s", e)

    # 2. Remove from workspace store
    workspace_manager.remove_worktree_pane(workspace_name, worktree_name)

    # 3. Delete git worktree if requested
    if not keep_worktree and repo_path:
        try:
            wt_manager = WorktreeManager(repo_path=repo_path)
            wt_manager.delete(worktree_name)
        except WorktreeError as e:
            logger.warning("Could not delete worktree: %s", e)

    # 4. Clean up status tracking
    try:
        status_tracker = StatusTracker()
        status_tracker.remove_status(worktree_name)
    except Exception:
        pass
