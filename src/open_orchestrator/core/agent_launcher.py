"""Unified worktree + AI agent provisioning.

``AgentLauncher.launch`` is the single entry point for creating a worktree
and starting its agent. Replaces parallel code paths in
``commands/worktree.py:new`` (interactive CLI, headless CI) and
``core/pane_actions.py:create_pane`` (popup/batch/orchestrator). Every
launch mode goes through the same pipeline:

1. Resolve the tool from the registry.
2. Create the worktree (recovering from an already-existing match).
3. Set up environment (deps, .env, CLAUDE.md injection).
4. Install status hooks (if the tool supports them).
5. Initialize the status tracker entry.
6. Start the agent session:
   * ``INTERACTIVE`` — tmux session, user attaches; prompt is optional.
   * ``AUTOMATED`` — tmux session with ``auto_exit``; prompt required.
   * ``HEADLESS`` — detached subprocess; prompt required.
7. Deliver the prompt via ``wait_for_ai_ready + paste_to_pane`` for tmux
   modes (killing the old ``time.sleep(2) + send_keys`` path) or
   ``subprocess.Popen`` + stdin for headless.

Any failure after step 2 triggers a ``PaneTransaction`` rollback so the
worktree/tmux/status entry are torn down together.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from open_orchestrator.core.pane_actions import (
    PaneActionError,
    PaneTransaction,
    _init_pane_tracking,
    _setup_pane_environment,
)
from open_orchestrator.core.status import StatusConfig, StatusTracker, runtime_status_config
from open_orchestrator.core.tmux_manager import TmuxError, TmuxManager, TmuxSessionExistsError
from open_orchestrator.core.tool_protocol import AIToolProtocol
from open_orchestrator.core.tool_registry import get_registry
from open_orchestrator.core.worktree import WorktreeAlreadyExistsError, WorktreeError, WorktreeManager
from open_orchestrator.models.status import AIActivityStatus
from open_orchestrator.models.worktree_info import WorktreeInfo

logger = logging.getLogger(__name__)


class LaunchMode(str, Enum):
    """How the agent should run."""

    INTERACTIVE = "interactive"  # tmux, user attaches, prompt optional
    AUTOMATED = "automated"  # tmux with auto_exit, prompt required
    HEADLESS = "headless"  # subprocess, no tmux, prompt required


@dataclass(frozen=True)
class LaunchRequest:
    """Input to ``AgentLauncher.launch``."""

    branch: str
    base_branch: str | None
    ai_tool: str
    mode: LaunchMode
    prompt: str | None = None
    display_task: str | None = None
    plan_mode: bool = False


@dataclass
class LaunchResult:
    """Result of a successful launch."""

    worktree_name: str
    worktree_path: str
    branch: str
    ai_tool: str
    tmux_session: str | None
    subprocess_pid: int | None
    warnings: list[str] = field(default_factory=list)


class AgentLauncher:
    """Owns worktree + agent provisioning for all three launch modes."""

    def __init__(
        self,
        *,
        repo_path: str,
        wt_manager: WorktreeManager | None = None,
        tmux: TmuxManager | None = None,
        status_tracker: StatusTracker | None = None,
        status_config: StatusConfig | None = None,
        config: object | None = None,
    ) -> None:
        self._repo_path = repo_path
        self._wt_manager = wt_manager or WorktreeManager(repo_path=Path(repo_path))
        self._tmux = tmux or TmuxManager()
        self._status_tracker = status_tracker
        self._status_config = status_config
        self._config = config

    def launch(self, request: LaunchRequest) -> LaunchResult:
        """Provision worktree + agent for the given request."""
        if request.mode in (LaunchMode.AUTOMATED, LaunchMode.HEADLESS) and not request.prompt:
            raise PaneActionError(f"{request.mode.value} mode requires a prompt")

        tool = get_registry().get(request.ai_tool)
        if tool is None:
            raise PaneActionError(
                f"Unknown AI tool '{request.ai_tool}'. Registered: {get_registry().list_names()}"
            )
        if request.mode == LaunchMode.HEADLESS and not tool.supports_headless:
            raise PaneActionError(
                f"Headless mode is not supported by '{request.ai_tool}'. "
                "The tool needs a non-interactive execution mode plus OWT hooks."
            )

        txn = PaneTransaction(repo_path=self._repo_path)
        warnings: list[str] = []

        try:
            worktree = self._create_worktree(request, txn)
            self._setup_environment(str(worktree.path), warnings)

            if request.mode == LaunchMode.HEADLESS:
                tracker = self._init_tracking_headless(worktree, request, txn, warnings)
                pid = self._launch_headless(worktree, tool, request, tracker)
                return LaunchResult(
                    worktree_name=worktree.name,
                    worktree_path=str(worktree.path),
                    branch=worktree.branch,
                    ai_tool=request.ai_tool,
                    tmux_session=None,
                    subprocess_pid=pid,
                    warnings=warnings,
                )

            # Interactive or automated: create tmux session, deliver prompt via paste.
            session_name = self._create_tmux_session(worktree, request, txn)
            tracker = _init_pane_tracking(
                worktree=worktree,
                ai_tool=request.ai_tool,
                tmux_session_name=session_name,
                repo_path=self._repo_path,
                ai_instructions=request.prompt,
                display_task=request.display_task,
                status_tracker=self._status_tracker,
                status_config=self._status_config,
                txn=txn,
            )
            if request.prompt:
                self._deliver_prompt_via_paste(session_name, request.prompt)

            return LaunchResult(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                branch=worktree.branch,
                ai_tool=request.ai_tool,
                tmux_session=session_name,
                subprocess_pid=None,
                warnings=warnings,
            )
        except PaneActionError:
            txn.rollback()
            raise
        except Exception as e:
            txn.rollback()
            raise PaneActionError(f"Unexpected error during launch: {e}") from e

    # -- pipeline steps -----------------------------------------------------

    def _create_worktree(self, request: LaunchRequest, txn: PaneTransaction) -> WorktreeInfo:
        candidate_name = request.branch.split("/")[-1] if "/" in request.branch else request.branch
        existing_names = {wt.name for wt in self._wt_manager.list_all()}
        if candidate_name in existing_names:
            raise PaneActionError(
                f"A worktree named '{candidate_name}' already exists. Use a different branch name."
            )
        try:
            worktree = self._wt_manager.create(branch=request.branch, base_branch=request.base_branch)
            txn.worktree_created = True
        except WorktreeAlreadyExistsError:
            # Race condition safety net: list_all() saw no conflict but create()
            # did. Recover by fetching the existing worktree.
            worktree = self._wt_manager.get(request.branch)
        except WorktreeError as e:
            raise PaneActionError(f"Failed to create worktree: {e}") from e

        txn.worktree_name = worktree.name
        return worktree

    def _setup_environment(self, worktree_path: str, warnings: list[str]) -> None:
        from open_orchestrator.config import load_config

        cfg = self._config or load_config()
        try:
            _setup_pane_environment(worktree_path, self._repo_path, cfg)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Environment setup warning: {e}")

    def _create_tmux_session(
        self,
        worktree: WorktreeInfo,
        request: LaunchRequest,
        txn: PaneTransaction,
    ) -> str:
        automated = request.mode == LaunchMode.AUTOMATED
        try:
            info = self._tmux.create_worktree_session(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                ai_tool=request.ai_tool,
                plan_mode=request.plan_mode,
                automated=automated,
            )
            txn.tmux_session_created = True
            return info.session_name
        except TmuxSessionExistsError:
            # Interactive re-use: fall back to existing session.
            return self._tmux.generate_session_name(worktree.name)
        except TmuxError as e:
            raise PaneActionError(f"Failed to create tmux session: {e}") from e

    def _deliver_prompt_via_paste(self, session_name: str, prompt: str) -> None:
        """Wait for the agent to be ready, then paste the prompt.

        This replaces the legacy ``time.sleep(2) + send_keys`` path with
        ``wait_for_ai_ready + paste_to_pane``, which handles prompts >2K
        chars without send-keys buffer truncation.
        """
        self._tmux.wait_for_ai_ready(session_name=session_name, timeout=15)
        self._tmux.paste_to_pane(session_name=session_name, text=prompt)

    def _init_tracking_headless(
        self,
        worktree: WorktreeInfo,
        request: LaunchRequest,
        txn: PaneTransaction,
        warnings: list[str],
    ) -> StatusTracker:
        """Status tracking setup for headless mode (no tmux session)."""
        tracker_config = self._status_config or runtime_status_config(self._repo_path)
        tracker = self._status_tracker or StatusTracker(tracker_config)
        try:
            from open_orchestrator.core.hooks import install_hooks

            install_hooks(
                worktree.path,
                worktree.name,
                request.ai_tool,
                db_path=tracker.storage_path,
            )
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Hook installation failed: {e}")
        try:
            tracker.initialize_status(
                worktree_name=worktree.name,
                worktree_path=str(worktree.path),
                branch=worktree.branch,
                tmux_session=None,
                ai_tool=request.ai_tool,
            )
            txn.status_initialized = True
            if request.prompt:
                tracker.update_task(
                    worktree.name,
                    (request.display_task or request.prompt)[:100],
                    AIActivityStatus.WORKING,
                )
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Status tracking init failed: {e}")
        return tracker

    def _launch_headless(
        self,
        worktree: WorktreeInfo,
        tool: AIToolProtocol,
        request: LaunchRequest,
        tracker: StatusTracker,
    ) -> int:
        """Detached subprocess launch; prompt piped via stdin."""
        assert request.prompt is not None  # validated earlier

        executable = shutil.which(tool.binary)
        if executable is None:
            for candidate in tool.get_known_paths():
                if candidate.exists() and candidate.is_file():
                    executable = str(candidate)
                    break

        command = tool.get_command(
            executable_path=executable,
            plan_mode=request.plan_mode,
            prompt=request.prompt,
        )

        try:
            proc = subprocess.Popen(
                shlex.split(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(worktree.path),
                env={**os.environ, "OWT_AUTOMATED": "1"},
                start_new_session=True,
            )
            assert proc.stdin is not None
            proc.stdin.write(request.prompt.encode())
            proc.stdin.close()
        except OSError as e:
            raise PaneActionError(f"Could not launch headless agent: {e}") from e

        try:
            tracker.update_task(
                worktree.name,
                request.prompt[:100],
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not update task on headless launch: %s", e)

        return proc.pid
