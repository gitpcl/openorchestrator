"""Shared pane lifecycle logic for CLI.

Extracts the business logic from CLI pane commands into reusable functions.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from open_orchestrator.config import AITool, load_config
from open_orchestrator.core.environment import EnvironmentSetup, EnvironmentSetupError
from open_orchestrator.core.project_detector import ProjectDetector
from open_orchestrator.core.status import (
    StatusConfig,
    StatusTracker,
    runtime_status_config,
)
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


def build_agent_prompt(
    task_description: str,
    retry_context: str | None = None,
) -> str:
    """Build the structured session init protocol prompt for an automated agent.

    Encodes the session init protocol from Anthropic's harness design
    research: orient → explore → implement → test → verify → commit.

    Args:
        task_description: The task for the agent to complete.
        retry_context: Optional context from a previous failed attempt.
    """
    retry_block = f"\n{retry_context}" if retry_context else ""
    return f"""\
You are an AI coding agent working in an isolated git worktree.

PROTOCOL — follow these steps in order:
1. ORIENT: Read README.md and .claude/CLAUDE.md to understand the project
2. EXPLORE: Read the source files relevant to your task
3. IMPLEMENT: Make changes following existing code patterns
4. TEST: Run the project's test suite and fix failures
5. VERIFY: Run `git diff` to review all your changes
6. COMMIT: git add -A && git commit -m 'feat: <what you did>'

If you reach a good milestone before finishing everything, commit progress:
  git add -A && git commit -m 'wip: <what you completed so far>'
Then continue with the remaining work.

TASK: {task_description}

RULES:
- You MUST commit your work. Uncommitted work is lost when this session ends.
- Commit using raw git commands: git add -A && git commit -m 'message'
- Do NOT use /commit, the built-in commit workflow, or any interactive commit
  confirmation. These block indefinitely in automated mode.
- If you can only partially complete the task, commit what you have with a
  clear message about what remains.
- Do NOT create stub files or placeholder implementations.
{retry_block}"""


def popup_result_path(workspace_name: str) -> str:
    """Get the temp file path for popup picker results."""
    import tempfile

    user_tmp = Path(tempfile.gettempdir()) / f"owt-{os.getuid()}"
    user_tmp.mkdir(mode=0o700, exist_ok=True)
    return str(user_tmp / f"owt-popup-{workspace_name}.json")


def read_popup_result(popup_file: str, cleanup: bool = True) -> dict[str, object]:
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
    base_branch: str | None = None,
    ai_tool: AITool = AITool.CLAUDE,
    template_name: str | None = None,
    plan_mode: bool = False,
    ai_instructions: str | None = None,
    display_task: str | None = None,
    status_tracker: StatusTracker | None = None,
    status_config: StatusConfig | None = None,
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
        base_branch: Optional base branch for creating a new worktree branch.
        ai_tool: AI tool to start.
        template_name: Optional worktree template name.
        plan_mode: Start Claude in plan mode.
        ai_instructions: Optional AI instructions to send after creation.
        display_task: Optional short task label for status cards.
        status_tracker: Optional shared status tracker for the caller.
        status_config: Optional status storage configuration.

    Returns:
        PaneResult with details of the created pane.

    Raises:
        PaneActionError: If any step fails fatally.
    """
    config = load_config()
    ai_tool_enum = ai_tool

    # Check for duplicate
    wt_manager = WorktreeManager(repo_path=Path(repo_path))
    existing_names = {wt.name for wt in wt_manager.list_all()}
    candidate_name = branch.split("/")[-1] if "/" in branch else branch
    if candidate_name in existing_names:
        raise PaneActionError(f"A worktree named '{candidate_name}' already exists. Use a different branch name.")

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
        worktree = wt_manager.create(branch=branch, base_branch=base_branch)
    except WorktreeAlreadyExistsError:
        worktree = wt_manager.get(branch)
    except WorktreeError as e:
        raise PaneActionError(f"Failed to create worktree: {e}") from e

    # 2. Set up environment
    project_config = None
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

    # 2b. Inject project context (test/dev commands) into CLAUDE.md
    if project_config and (project_config.test_command or project_config.dev_command):
        try:
            from open_orchestrator.core.environment import inject_project_context

            inject_project_context(str(worktree.path), project_config)
        except Exception as e:
            logger.debug("Project context injection skipped: %s", e)

    # 3. Create a live provider session so users can patch into automated work.
    tmux_manager = TmuxManager()
    try:
        tmux_session = tmux_manager.create_worktree_session(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            ai_tool=ai_tool_enum,
            plan_mode=plan_mode,
            automated=bool(ai_instructions),
        )
        pane_index = 0
    except TmuxError as e:
        raise PaneActionError(f"Failed to create session: {e}") from e

    # 4. Install AI tool hooks for status reporting
    tracker_config = status_config or runtime_status_config(repo_path)
    tracker = status_tracker or StatusTracker(tracker_config)
    try:
        from open_orchestrator.core.hooks import install_hooks

        install_hooks(
            worktree.path,
            worktree.name,
            ai_tool_enum,
            db_path=tracker.storage_path,
        )
    except Exception as e:
        logger.debug("Hook installation skipped: %s", e)

    # 5. Initialize status tracking
    initial_status = "working" if ai_instructions else "idle"
    try:
        tracker.initialize_status(
            worktree_name=worktree.name,
            worktree_path=str(worktree.path),
            branch=worktree.branch,
            tmux_session=tmux_session.session_name,
            ai_tool=ai_tool_enum,
        )
        if initial_status == "working":
            from open_orchestrator.models.status import AIActivityStatus

            tracker.update_task(
                worktree.name,
                (display_task or ai_instructions or "")[:100],
                AIActivityStatus.WORKING,
            )
    except Exception as e:
        logger.debug("Status tracking init skipped: %s", e)

    if ai_instructions:
        time.sleep(2)
        try:
            tmux_manager.send_keys_to_pane(
                session_name=tmux_session.session_name,
                keys=ai_instructions,
            )
        except Exception as e:
            teardown_worktree(
                worktree.name,
                repo_path=repo_path,
                force=True,
            )
            raise PaneActionError(f"Failed to send initial AI instructions: {e}") from e

    return PaneResult(
        worktree_name=worktree.name,
        worktree_path=str(worktree.path),
        branch=worktree.branch,
        pane_index=pane_index,
        ai_tool=ai_tool_enum,
    )


def teardown_worktree(
    worktree_name: str,
    repo_path: str | None = None,
    *,
    kill_tmux: bool = True,
    delete_git_worktree: bool = True,
    clean_status: bool = True,
    force: bool = False,
) -> list[str]:
    """Best-effort cleanup of all worktree resources.

    Always attempts all three cleanup steps regardless of individual failures,
    preventing orphaned resources when one step errors.

    Args:
        worktree_name: Name of the worktree to tear down.
        repo_path: Path to main repo (needed to delete git worktree).
        kill_tmux: Whether to kill the associated tmux session.
        delete_git_worktree: Whether to delete the git worktree directory.
        clean_status: Whether to remove the status DB entry.

    Returns:
        List of error strings for any steps that failed (empty = full success).
    """
    errors: list[str] = []

    # 1. Kill tmux session
    if kill_tmux:
        try:
            tmux_manager = TmuxManager()
            session_name = tmux_manager.generate_session_name(worktree_name)
            if tmux_manager.session_exists(session_name):
                tmux_manager.kill_session(session_name)
        except TmuxError as e:
            msg = f"Could not kill tmux session for '{worktree_name}': {e}"
            logger.warning(msg)
            errors.append(msg)
        except Exception as e:
            msg = f"Unexpected error killing tmux session for '{worktree_name}': {e}"
            logger.warning(msg)
            errors.append(msg)

    # 2. Delete git worktree
    if delete_git_worktree and repo_path:
        try:
            wt_manager = WorktreeManager(repo_path=Path(repo_path))
            wt_manager.delete(worktree_name, force=force)
        except WorktreeError as e:
            msg = f"Could not delete git worktree '{worktree_name}': {e}"
            logger.warning(msg)
            errors.append(msg)
        except Exception as e:
            msg = f"Unexpected error deleting git worktree '{worktree_name}': {e}"
            logger.warning(msg)
            errors.append(msg)

    # 3. Clean up status tracking
    if clean_status:
        try:
            StatusTracker(runtime_status_config(repo_path)).remove_status(worktree_name)
        except Exception as e:
            msg = f"Could not remove status entry for '{worktree_name}': {e}"
            logger.warning(msg)
            errors.append(msg)

    return errors


def remove_pane(
    worktree_name: str,
    repo_path: str | None = None,
) -> None:
    """Delete a worktree, its tmux session, and clean up status.

    Delegates to ``teardown_worktree`` for best-effort cleanup.
    Errors are logged but not raised.
    """
    teardown_worktree(worktree_name, repo_path=repo_path)
