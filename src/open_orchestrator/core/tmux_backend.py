"""Tmux adapter implementing :class:`MultiplexerBackend`.

A thin wrapper over the existing :class:`TmuxManager` so call sites can
depend on the multiplexer protocol while preserving every tmux-specific
behavior owt already has.

``report_agent_state`` is a no-op — tmux has no sidebar.  The status
tracker remains the source of truth (Sprint 025 §5).
"""

from __future__ import annotations

import logging
import subprocess  # noqa: S404 — argv form only, no shell

from open_orchestrator.core.tmux_manager import TmuxLayout, TmuxManager, TmuxSessionNotFoundError
from open_orchestrator.models.backend import BackendKind, BackendSession

logger = logging.getLogger(__name__)


class TmuxBackend:
    """``MultiplexerBackend`` implementation backed by tmux."""

    kind: BackendKind = BackendKind.TMUX

    def __init__(self, tmux: TmuxManager | None = None) -> None:
        self._tmux = tmux or TmuxManager()

    # ── lookups ────────────────────────────────────────────────────────

    def session_for(self, worktree_name: str) -> BackendSession | None:
        name = self._tmux.generate_session_name(worktree_name)
        if not self._tmux.session_exists(name):
            return None
        return BackendSession(kind=self.kind, id=name, worktree_name=worktree_name)

    # ── lifecycle ─────────────────────────────────────────────────────

    def create_session(
        self,
        worktree_name: str,
        cwd: str,
        *,
        agent_command: str | None = None,
        plan_mode: bool = False,
    ) -> BackendSession:
        info = self._tmux.create_worktree_session(
            worktree_name=worktree_name,
            worktree_path=cwd,
            layout=TmuxLayout.SINGLE,
            auto_start_ai=bool(agent_command),
            ai_tool=agent_command or "claude",
            plan_mode=plan_mode,
        )
        return BackendSession(
            kind=self.kind,
            id=info.session_name,
            worktree_name=worktree_name,
        )

    def kill(self, session: BackendSession) -> None:
        try:
            self._tmux.kill_session(session.id)
        except TmuxSessionNotFoundError:
            logger.debug("tmux session %s already gone", session.id)

    def is_alive(self, session: BackendSession) -> bool:
        return self._tmux.session_exists(session.id)

    # ── I/O ───────────────────────────────────────────────────────────

    def send_text(self, session: BackendSession, text: str) -> None:
        # send_keys_to_pane handles the literal-then-Enter cadence.
        self._tmux.send_keys_to_pane(session.id, text)

    def send_keys(self, session: BackendSession, keys: str) -> None:
        # Raw key delivery without the literal+enter transform.
        if not self._tmux.session_exists(session.id):
            raise TmuxSessionNotFoundError(f"Session '{session.id}' not found.")
        subprocess.run(  # noqa: S603 — argv list, fixed binary
            ["tmux", "send-keys", "-t", f"{session.id}:0.0", keys],
            check=True,
        )

    def read_recent(self, session: BackendSession, lines: int = 200) -> str:
        if not self._tmux.session_exists(session.id):
            return ""
        try:
            result = subprocess.run(  # noqa: S603 — argv list, fixed binary
                ["tmux", "capture-pane", "-t", f"{session.id}:0.0", "-p", "-J", "-S", f"-{lines}"],
                capture_output=True,
                text=True,
                check=True,
                timeout=3,
            )
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
            logger.debug("tmux capture-pane failed: %s", err)
            return ""

    def attach(self, session: BackendSession) -> None:
        if self._tmux.is_inside_tmux():
            self._tmux.switch_client(session.id)
        else:
            self._tmux.attach(session.id)

    def report_agent_state(self, session: BackendSession, state: str, message: str) -> None:
        # tmux has no sidebar — intentional no-op.
        del session, state, message
