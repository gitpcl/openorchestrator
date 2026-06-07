"""Multiplexer backend protocol.

Sprint 025 introduces a uniform interface over tmux and herdr. Call sites
should depend on :class:`MultiplexerBackend` and never on ``TmuxManager``
or ``HerdrClient`` directly — those live behind their respective adapter
implementations (:class:`open_orchestrator.core.tmux_backend.TmuxBackend`
and :class:`open_orchestrator.core.herdr_backend.HerdrBackend`).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from open_orchestrator.models.backend import BackendKind, BackendSession


@runtime_checkable
class MultiplexerBackend(Protocol):
    """Uniform abstraction over a terminal multiplexer.

    Adapter implementations translate these methods into the native
    operations of their respective multiplexer.
    """

    kind: BackendKind

    def create_session(
        self,
        worktree_name: str,
        cwd: str,
        *,
        agent_command: str | None = None,
        plan_mode: bool = False,
        automated: bool = False,
        task: str | None = None,
    ) -> BackendSession:
        """Create a session bound to ``cwd`` and optionally spawn ``agent_command``.

        ``automated`` is a hint that the agent is running unattended (no
        human will type follow-ups). Backends that surface this to the
        underlying process (e.g. tmux sets ``OWT_AUTOMATED=1``) should
        honor it; herdr currently treats it as advisory.

        ``task`` is only set for ``task_via_args`` tools (e.g. ClawCore),
        whose one-shot CLI takes the task as argv rather than a pasted
        prompt. The backend substitutes it into the launch command and the
        caller skips prompt delivery. Tools that paste/pipe the prompt leave
        this ``None``.
        """
        ...

    def session_for(self, worktree_name: str) -> BackendSession | None:
        """Return an existing session for the worktree, or ``None``."""
        ...

    def send_text(self, session: BackendSession, text: str) -> None:
        """Inject ``text`` (with trailing newline) into the session's primary pane."""
        ...

    def send_keys(self, session: BackendSession, keys: str) -> None:
        """Send literal keys (no newline) — for control sequences."""
        ...

    def read_recent(self, session: BackendSession, lines: int = 200) -> str:
        """Return the most recent ``lines`` of pane scrollback."""
        ...

    def attach(self, session: BackendSession) -> None:
        """Hand off the foreground process to this session (process replacement)."""
        ...

    def kill(self, session: BackendSession) -> None:
        """Terminate the session and release backend resources."""
        ...

    def is_alive(self, session: BackendSession) -> bool:
        """Return True if the backend still has this session running."""
        ...

    def report_agent_state(self, session: BackendSession, state: str, message: str) -> None:
        """Push agent state to the backend's sidebar (no-op for tmux)."""
        ...
