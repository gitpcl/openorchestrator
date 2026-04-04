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


@dataclass
class PaneTransaction:
    """Tracks resources created during pane creation for rollback on failure.

    Each field records whether a resource was created, so rollback()
    can clean up only what was actually provisioned.
    """

    repo_path: str | None = None
    worktree_name: str | None = None
    worktree_created: bool = False
    tmux_session_created: bool = False
    status_initialized: bool = False

    def rollback(self) -> None:
        """Roll back all tracked resources using teardown_worktree()."""
        if not self.worktree_name:
            return

        logger.warning(
            "Rolling back pane creation for '%s' (worktree=%s, tmux=%s, status=%s)",
            self.worktree_name,
            self.worktree_created,
            self.tmux_session_created,
            self.status_initialized,
        )
        teardown_worktree(
            self.worktree_name,
            repo_path=self.repo_path,
            kill_tmux=self.tmux_session_created,
            delete_git_worktree=self.worktree_created,
            clean_status=self.status_initialized,
            force=True,
        )


def build_agent_prompt(
    task_description: str,
    retry_context: str | None = None,
) -> str:
    """Build a context-aware protocol prompt for an automated agent.

    Uses PromptBuilder with task-type classification to tailor the protocol
    (bugfix → reproduce-fix-test, feature → orient-explore-implement, etc.).

    Args:
        task_description: The task for the agent to complete.
        retry_context: Optional context from a previous failed attempt.
    """
    from open_orchestrator.core.prompt_builder import (
        COMMIT_SAFETY,
        TURN_EFFICIENCY,
        PromptBuilder,
        get_protocol_for_task,
    )

    builder = (
        PromptBuilder()
        .add_section(
            "role",
            "You are an AI coding agent working in an isolated git worktree.",
            priority=100,
        )
        .add_section("commit_safety", COMMIT_SAFETY, priority=98)
        .add_section("turn_efficiency", TURN_EFFICIENCY, priority=97)
        .add_section("task", f"TASK: {task_description}", priority=95)
        .add_section("protocol", get_protocol_for_task(task_description), priority=90)
        .add_section(
            "rules",
            (
                "RULES:\n"
                "- If you can only partially complete the task, commit what you have with a\n"
                "  clear message about what remains.\n"
                "- Do NOT create stub files or placeholder implementations.\n"
                "- If you reach a good milestone before finishing, commit progress:\n"
                "  git add -A && git commit -m 'wip: <what you completed so far>'\n"
                "  Then continue with the remaining work."
            ),
            priority=85,
        )
    )

    if retry_context:
        builder = builder.add_section("retry", retry_context, priority=80)

    return builder.build()


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


def _setup_pane_environment(worktree_path: str, repo_path: str, config: object) -> None:
    """Set up environment and inject project context for a new pane."""
    project_config = None
    try:
        project_config = ProjectDetector().detect(worktree_path)
        if project_config:
            EnvironmentSetup(project_config).setup_worktree(
                worktree_path=worktree_path,
                source_path=repo_path,
                install_deps=config.environment.auto_install_deps,  # type: ignore[attr-defined]
                copy_env=config.environment.copy_env_file,  # type: ignore[attr-defined]
            )
    except EnvironmentSetupError as e:
        logger.warning("Environment setup issue: %s", e)

    if project_config and (project_config.test_command or project_config.dev_command):
        try:
            from open_orchestrator.core.environment import inject_project_context

            inject_project_context(worktree_path, project_config)
        except Exception as e:
            logger.debug("Project context injection skipped: %s", e)


def _init_pane_tracking(
    worktree: object,
    ai_tool: AITool,
    tmux_session_name: str,
    repo_path: str,
    ai_instructions: str | None,
    display_task: str | None,
    status_tracker: StatusTracker | None,
    status_config: StatusConfig | None,
    txn: PaneTransaction,
) -> StatusTracker:
    """Install hooks and initialize status tracking. Returns the tracker."""
    tracker_config = status_config or runtime_status_config(repo_path)
    tracker = status_tracker or StatusTracker(tracker_config)
    try:
        from open_orchestrator.core.hooks import install_hooks

        install_hooks(
            worktree.path,  # type: ignore[attr-defined]
            worktree.name,  # type: ignore[attr-defined]
            ai_tool,
            db_path=tracker.storage_path,
        )
    except Exception as e:
        logger.debug("Hook installation skipped: %s", e)

    try:
        tracker.initialize_status(
            worktree_name=worktree.name,  # type: ignore[attr-defined]
            worktree_path=str(worktree.path),  # type: ignore[attr-defined]
            branch=worktree.branch,  # type: ignore[attr-defined]
            tmux_session=tmux_session_name,
            ai_tool=ai_tool,
        )
        txn.status_initialized = True
        if ai_instructions:
            from open_orchestrator.models.status import AIActivityStatus

            tracker.update_task(
                worktree.name,  # type: ignore[attr-defined]
                (display_task or ai_instructions or "")[:100],
                AIActivityStatus.WORKING,
            )
    except Exception as e:
        logger.debug("Status tracking init skipped: %s", e)

    return tracker


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
    txn = PaneTransaction(repo_path=repo_path)

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

    try:
        # 1. Create the worktree
        try:
            worktree = wt_manager.create(branch=branch, base_branch=base_branch)
            txn.worktree_created = True
        except WorktreeAlreadyExistsError:
            worktree = wt_manager.get(branch)
        except WorktreeError as e:
            raise PaneActionError(f"Failed to create worktree: {e}") from e

        txn.worktree_name = worktree.name

        # 2-2b. Set up environment + project context
        _setup_pane_environment(str(worktree.path), repo_path, config)

        # 3. Create tmux session
        tmux_manager = TmuxManager()
        try:
            tmux_session = tmux_manager.create_worktree_session(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                ai_tool=ai_tool_enum,
                plan_mode=plan_mode,
                automated=bool(ai_instructions),
            )
            txn.tmux_session_created = True
            pane_index = 0
        except TmuxError as e:
            raise PaneActionError(f"Failed to create session: {e}") from e

        # 4-5. Install hooks + initialize status
        _init_pane_tracking(
            worktree=worktree,
            ai_tool=ai_tool_enum,
            tmux_session_name=tmux_session.session_name,
            repo_path=repo_path,
            ai_instructions=ai_instructions,
            display_task=display_task,
            status_tracker=status_tracker,
            status_config=status_config,
            txn=txn,
        )

        # 6. Deliver prompt after waiting for AI readiness.
        # Use tmux load-buffer + paste-buffer to handle long prompts
        # (2K+ chars) that exceed send-keys -l buffer limits.
        # Claude stays in interactive mode (no -p flag).
        if ai_instructions:
            tmux_manager.wait_for_ai_ready(
                session_name=tmux_session.session_name,
                timeout=15,
            )
            tmux_manager.paste_to_pane(
                session_name=tmux_session.session_name,
                text=ai_instructions,
            )

    except PaneActionError:
        txn.rollback()
        raise
    except Exception as e:
        txn.rollback()
        raise PaneActionError(f"Unexpected error during pane creation: {e}") from e

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
