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
5. Initialize the status tracker entry (recording the backend kind so
   later ``owt attach``/``owt send``/``owt delete`` route correctly).
6. Start the agent session via the multiplexer backend
   (:class:`open_orchestrator.core.multiplexer.MultiplexerBackend`):
   * ``INTERACTIVE`` — backend session, user attaches; prompt optional.
   * ``AUTOMATED`` — backend session with ``OWT_AUTOMATED=1`` (tmux only
     today); completion via the agent's Stop hook.
   * ``HEADLESS`` — detached subprocess; prompt required. No backend.
7. Deliver the prompt:
   * tmux backends: ``wait_for_ai_ready + paste_to_pane`` for large
     prompts that overflow ``send-keys`` buffers.
   * herdr backends: prompt is appended to the agent command in
     :meth:`HerdrBackend.create_session` (pane.send_text is one-shot).
   * headless: ``subprocess.Popen`` + stdin.

Any failure after step 2 triggers a ``PaneTransaction`` rollback so the
worktree, backend session, and status entry are torn down together.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

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
from open_orchestrator.models.backend import BackendKind
from open_orchestrator.models.status import AIActivityStatus
from open_orchestrator.models.worktree_info import SessionType

from ._path import try_resolve_binary

if TYPE_CHECKING:
    from open_orchestrator.core.multiplexer import MultiplexerBackend
    from open_orchestrator.models.backend import BackendSession

logger = logging.getLogger(__name__)


class LaunchMode(str, Enum):
    """How the agent should run."""

    INTERACTIVE = "interactive"  # tmux, user attaches, prompt optional
    AUTOMATED = "automated"  # tmux with OWT_AUTOMATED=1; completion via Stop hook
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
    session_type: SessionType = SessionType.WORKTREE
    backend_kind: BackendKind = BackendKind.TMUX


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
    session_type: SessionType = SessionType.WORKTREE
    repo_root: str | None = None
    backend_kind: BackendKind = BackendKind.TMUX
    backend_session_id: str | None = None


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
        backend: MultiplexerBackend | None = None,
    ) -> None:
        self._repo_path = repo_path
        self._wt_manager = wt_manager or WorktreeManager(repo_path=Path(repo_path))
        # Legacy tmux handle — only used by HEADLESS mode (no backend) and
        # by tests that already inject one. Backend path supersedes it for
        # session lifecycle.
        self._tmux = tmux or TmuxManager()
        self._status_tracker = status_tracker
        self._status_config = status_config
        self._config = config
        self._backend = backend

    def launch(self, request: LaunchRequest) -> LaunchResult:
        """Provision worktree + agent for the given request."""
        if request.mode in (LaunchMode.AUTOMATED, LaunchMode.HEADLESS) and not request.prompt:
            raise PaneActionError(f"{request.mode.value} mode requires a prompt")

        tool = get_registry().get(request.ai_tool)
        if tool is None:
            raise PaneActionError(f"Unknown AI tool '{request.ai_tool}'. Registered: {get_registry().list_names()}")
        if request.mode == LaunchMode.HEADLESS and not tool.supports_headless:
            raise PaneActionError(
                f"Headless mode is not supported by '{request.ai_tool}'. "
                "The tool needs a non-interactive execution mode plus OWT hooks."
            )

        txn = PaneTransaction(repo_path=self._repo_path)
        warnings: list[str] = []

        try:
            session_name, session_path, branch = self._prepare_checkout(request, txn)
            txn.worktree_name = session_name
            txn.session_type = request.session_type.value

            self._setup_environment(session_path, warnings)

            if request.mode == LaunchMode.HEADLESS:
                tracker = self._init_tracking_headless(session_name, session_path, branch, request, txn, warnings)
                pid = self._launch_headless_by_path(session_path, tool, request, tracker)
                return LaunchResult(
                    worktree_name=session_name,
                    worktree_path=session_path,
                    branch=branch,
                    ai_tool=request.ai_tool,
                    tmux_session=None,
                    subprocess_pid=pid,
                    warnings=warnings,
                    backend_kind=BackendKind.TMUX,
                )

            # Interactive or automated: create backend session, deliver prompt.
            backend = self._resolve_backend(request)
            backend_session = self._create_backend_session(backend, session_name, session_path, request)
            txn.tmux_session_created = backend.kind == BackendKind.TMUX
            txn.backend_session_id = backend_session.id
            txn.backend_kind = backend.kind.value
            txn.backend_meta = dict(backend_session.meta)
            _init_pane_tracking(
                worktree_name=session_name,
                worktree_path=session_path,
                branch=branch,
                ai_tool=request.ai_tool,
                tmux_session_name=backend_session.id if backend.kind == BackendKind.TMUX else None,
                repo_path=self._repo_path,
                ai_instructions=request.prompt,
                display_task=request.display_task,
                status_tracker=self._status_tracker,
                status_config=self._status_config,
                txn=txn,
                backend_kind=backend.kind.value,
                backend_session_id=backend_session.id,
                backend_meta=dict(backend_session.meta),
                session_type=request.session_type.value,
            )
            if request.prompt:
                self._deliver_prompt(backend, backend_session, request.prompt)

            return LaunchResult(
                worktree_name=session_name,
                worktree_path=session_path,
                branch=branch,
                ai_tool=request.ai_tool,
                tmux_session=backend_session.id if backend.kind == BackendKind.TMUX else None,
                subprocess_pid=None,
                warnings=warnings,
                session_type=request.session_type,
                repo_root=self._repo_path,
                backend_kind=backend.kind,
                backend_session_id=backend_session.id,
            )
        except PaneActionError:
            txn.rollback()
            raise
        except Exception as e:
            txn.rollback()
            raise PaneActionError(f"Unexpected error during launch: {e}") from e

    # -- pipeline steps -----------------------------------------------------

    def _prepare_checkout(self, request: LaunchRequest, txn: PaneTransaction) -> tuple[str, str, str]:
        """Dispatch to worktree creation or branch checkout based on session type.

        Returns:
            Tuple of (session_name, session_path, branch).
        """
        if request.session_type == SessionType.BRANCH:
            return self._checkout_branch(request, txn)
        return self._create_worktree(request, txn)

    def _create_worktree(self, request: LaunchRequest, txn: PaneTransaction) -> tuple[str, str, str]:
        candidate_name = request.branch.split("/")[-1] if "/" in request.branch else request.branch
        existing_names = {wt.name for wt in self._wt_manager.list_all()}
        if candidate_name in existing_names:
            raise PaneActionError(f"A worktree named '{candidate_name}' already exists. Use a different branch name.")
        try:
            worktree = self._wt_manager.create(branch=request.branch, base_branch=request.base_branch)
            txn.worktree_created = True
        except WorktreeAlreadyExistsError:
            worktree = self._wt_manager.get(request.branch)
        except WorktreeError as e:
            raise PaneActionError(f"Failed to create worktree: {e}") from e

        txn.worktree_name = worktree.name
        return (worktree.name, str(worktree.path), worktree.branch)

    def _checkout_branch(self, request: LaunchRequest, txn: PaneTransaction) -> tuple[str, str, str]:
        """Create a branch in the current checkout (no git worktree)."""
        from git import Repo
        from git.exc import GitCommandError

        candidate_name = request.branch.split("/")[-1] if "/" in request.branch else request.branch
        repo = Repo(self._repo_path)

        # Guard: reject if branch already exists locally
        try:
            repo.git.rev_parse("--verify", candidate_name)
            raise PaneActionError(f"Branch '{candidate_name}' already exists locally. Use a different branch name.")
        except GitCommandError:
            pass  # Branch doesn't exist — good

        # Guard: stash dirty state if present
        if repo.is_dirty(untracked_files=True):
            stash_marker = f"owt-auto-stash-{candidate_name}"
            repo.git.stash("push", "-u", "-m", stash_marker)
            txn.stash_created = True
            logger.info("Stashed dirty state for branch mode session '%s'", candidate_name)

        try:
            base_branch = request.base_branch or "main"
            repo.git.checkout("-b", candidate_name, base_branch)
            txn.branch_created = True
            logger.info("Created branch '%s' from '%s'", candidate_name, base_branch)
        except GitCommandError as e:
            raise PaneActionError(f"Failed to create branch '{candidate_name}': {e}") from e

        txn.worktree_name = candidate_name
        return (candidate_name, self._repo_path, candidate_name)

    def _setup_environment(self, worktree_path: str, warnings: list[str]) -> None:
        from open_orchestrator.config import load_config

        cfg = self._config or load_config()
        try:
            _setup_pane_environment(worktree_path, self._repo_path, cfg)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Environment setup warning: {e}")

    def _resolve_backend(self, request: LaunchRequest) -> MultiplexerBackend:
        """Return the backend to use.

        Precedence:
          1. Explicit ``backend=`` injected into the constructor.
          2. If ``request.backend_kind == TMUX``: wrap the injected
             ``TmuxManager`` so tests can mock the manager and still
             exercise the backend code path without spinning up a real
             tmux server.
          3. Resolve via :func:`select_backend` using the request's kind.
        """
        if self._backend is not None:
            return self._backend
        if request.backend_kind == BackendKind.TMUX:
            from open_orchestrator.core.tmux_backend import TmuxBackend

            return TmuxBackend(self._tmux)
        from open_orchestrator.core.backend_factory import select_backend

        backend_cfg = getattr(self._config, "backend", None) if self._config is not None else None
        return select_backend(backend_cfg, override=request.backend_kind.value)

    def _create_backend_session(
        self,
        backend: MultiplexerBackend,
        session_name: str,
        session_path: str,
        request: LaunchRequest,
    ) -> BackendSession:
        """Create a multiplexer session via the resolved backend.

        Both adapters accept the registered tool name as ``agent_command``;
        :class:`TmuxBackend` forwards it to :class:`TmuxManager` (which
        applies plan-mode / automated flags) and :class:`HerdrBackend`
        types it into a fresh herdr workspace pane.
        """
        automated = request.mode == LaunchMode.AUTOMATED
        try:
            return backend.create_session(
                session_name,
                session_path,
                agent_command=request.ai_tool,
                plan_mode=request.plan_mode,
                automated=automated,
            )
        except TmuxSessionExistsError:
            # Only tmux raises this — recover by reusing the existing session
            # for INTERACTIVE; refuse otherwise (prevents double-spawning agents).
            if request.mode != LaunchMode.INTERACTIVE or backend.kind != BackendKind.TMUX:
                raise PaneActionError(
                    f"tmux session for '{session_name}' already exists; refusing to reuse it in {request.mode.value} mode"
                )
            from open_orchestrator.models.backend import BackendSession

            return BackendSession(
                kind=BackendKind.TMUX,
                id=self._tmux.generate_session_name(session_name),
                worktree_name=session_name,
            )
        except TmuxError as e:
            raise PaneActionError(f"Failed to create tmux session: {e}") from e

    def _deliver_prompt(self, backend: MultiplexerBackend, session: BackendSession, prompt: str) -> None:
        """Deliver the prompt via the backend.

        Both backends must wait for the agent TUI to finish booting before
        typing: the prompt + submit keystroke race the startup otherwise and
        the prompt is left unsent. Tmux waits via ``wait_and_paste``; herdr
        waits via ``wait_for_ready`` (polling the pane's ``agent_status``
        until it is idle) before ``send_text``.
        """
        if backend.kind == BackendKind.HERDR:
            from open_orchestrator.core.herdr_backend import HerdrBackend

            if isinstance(backend, HerdrBackend):
                # Confirm submission: herdr can report idle mid-boot, so a
                # single send may race the agent's startup. submit_prompt
                # nudges the CR until the pane leaves idle.
                backend.submit_prompt(session, prompt)
            else:
                backend.send_text(session, prompt)
            return
        # tmux: tap into the adapter's wait+paste helper so the
        # heavy-prompt path is preserved.
        from open_orchestrator.core.tmux_backend import TmuxBackend

        if isinstance(backend, TmuxBackend):
            backend.wait_and_paste(session, prompt)
        else:
            backend.send_text(session, prompt)

    def _init_tracking_headless(
        self,
        session_name: str,
        session_path: str,
        branch: str,
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
                Path(session_path),
                session_name,
                request.ai_tool,
                db_path=tracker.storage_path,
            )
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Hook installation failed: {e}")
        try:
            tracker.initialize_status(
                worktree_name=session_name,
                worktree_path=session_path,
                branch=branch,
                tmux_session=None,
                ai_tool=request.ai_tool,
                session_type=request.session_type.value,
            )
            txn.status_initialized = True
            if request.prompt:
                tracker.update_task(
                    session_name,
                    (request.display_task or request.prompt)[:100],
                    AIActivityStatus.WORKING,
                )
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Status tracking init failed: {e}")
        return tracker

    def _launch_headless_by_path(
        self,
        session_path: str,
        tool: AIToolProtocol,
        request: LaunchRequest,
        tracker: StatusTracker,
    ) -> int:
        """Detached subprocess launch; prompt piped via stdin."""
        assert request.prompt is not None  # validated earlier

        # Resolve via the allowlisted PATH so a poisoned binary planted in
        # the worktree's cwd cannot hijack a headless agent launch.
        executable = try_resolve_binary(tool.binary)
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
                cwd=session_path,
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
                request.branch,
                request.prompt[:100],
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not update task on headless launch: %s", e)

        return proc.pid
