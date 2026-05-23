"""Herdr adapter implementing :class:`MultiplexerBackend`.

Herdr's hierarchy is *workspace → tab → pane*. Sprint 025 maps one owt
worktree to one herdr workspace and runs the agent in the workspace's
root pane.

The protocol methods are synchronous (matching tmux); we bridge into the
async :class:`HerdrClient` via ``asyncio.run`` for one-shot calls. Code
paths that are already async (e.g. ``action_dispatcher``) can also
construct a long-lived client via :meth:`async_client`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from typing import Any

from open_orchestrator.core.herdr_client import HerdrClient, HerdrError, default_socket_path
from open_orchestrator.models.backend import BackendKind, BackendSession

logger = logging.getLogger(__name__)


class HerdrBackend:
    """``MultiplexerBackend`` implementation backed by herdr's socket API."""

    kind: BackendKind = BackendKind.HERDR

    def __init__(
        self,
        *,
        session: str = "default",
        socket_path: str | None = None,
        client: HerdrClient | None = None,
    ) -> None:
        self._session_name = session
        self._socket_path = socket_path or str(default_socket_path(session))
        # Optional injected client (tests / persistent connections)
        self._client = client

    # ── client management ─────────────────────────────────────────────

    def _new_client(self) -> HerdrClient:
        return self._client or HerdrClient(socket_path=self._socket_path, session=self._session_name)

    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Run a one-shot RPC call synchronously."""

        async def _go() -> Any:
            client = self._new_client()
            try:
                if self._client is None:
                    await client.connect()
                return await client.call(method, params or {})
            finally:
                if self._client is None:
                    await client.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_go())
        # We're already inside a loop — schedule and wait.
        return loop.run_until_complete(_go())

    # ── lifecycle ─────────────────────────────────────────────────────

    def create_session(
        self,
        worktree_name: str,
        cwd: str,
        *,
        agent_command: str | None = None,
        plan_mode: bool = False,
        automated: bool = False,
    ) -> BackendSession:
        del plan_mode, automated  # both are communicated via prompt content for herdr
        workspace = self._call(
            "workspace.create",
            {"cwd": cwd, "label": worktree_name},
        )
        if not isinstance(workspace, dict):
            raise HerdrError("herdr.workspace.create returned a non-object result")
        workspace_id = str(workspace.get("workspace_id") or workspace.get("id") or "")
        pane_id = str(workspace.get("root_pane_id") or workspace.get("pane_id") or "")
        if not pane_id:
            raise HerdrError("herdr.workspace.create did not return a pane id")
        if agent_command:
            self._call(
                "pane.send_text",
                {"pane_id": pane_id, "text": agent_command + "\n"},
            )
        return BackendSession(
            kind=self.kind,
            id=pane_id,
            worktree_name=worktree_name,
            meta={"workspace_id": workspace_id, "socket": self._socket_path},
        )

    def session_for(self, worktree_name: str) -> BackendSession | None:
        try:
            result = self._call("workspace.find", {"label": worktree_name})
        except HerdrError:
            return None
        if not result:
            return None
        if isinstance(result, list):
            if not result:
                return None
            result = result[0]
        if not isinstance(result, dict):
            return None
        pane_id = str(result.get("root_pane_id") or result.get("pane_id") or "")
        if not pane_id:
            return None
        return BackendSession(
            kind=self.kind,
            id=pane_id,
            worktree_name=worktree_name,
            meta={"workspace_id": str(result.get("workspace_id", "")), "socket": self._socket_path},
        )

    def kill(self, session: BackendSession) -> None:
        try:
            self._call("pane.close", {"pane_id": session.id})
        except HerdrError as err:
            logger.debug("herdr pane.close failed: %s", err)
        workspace_id = session.meta.get("workspace_id")
        if workspace_id:
            try:
                self._call("workspace.close", {"workspace_id": workspace_id})
            except HerdrError as err:
                logger.debug("herdr workspace.close failed: %s", err)

    def is_alive(self, session: BackendSession) -> bool:
        try:
            res = self._call("pane.exists", {"pane_id": session.id})
        except HerdrError:
            return False
        return bool(res)

    # ── I/O ───────────────────────────────────────────────────────────

    def send_text(self, session: BackendSession, text: str) -> None:
        payload = text if text.endswith("\n") else text + "\n"
        self._call("pane.send_text", {"pane_id": session.id, "text": payload})

    def send_keys(self, session: BackendSession, keys: str) -> None:
        self._call("pane.send_keys", {"pane_id": session.id, "keys": keys})

    def read_recent(self, session: BackendSession, lines: int = 200) -> str:
        result = self._call(
            "pane.read",
            {"pane_id": session.id, "source": "recent", "lines": lines},
        )
        if isinstance(result, dict):
            return str(result.get("text", ""))
        return str(result or "")

    def attach(self, session: BackendSession) -> None:
        """Replace the current process with ``herdr agent attach <pane_id>``."""
        argv = ["herdr", "agent", "attach", session.id]
        try:
            os.execvp(argv[0], argv)  # noqa: S606 — argv list, intentional handoff
        except FileNotFoundError as err:
            raise HerdrError("herdr CLI not found on PATH; install from https://herdr.dev/install.sh") from err

    def report_agent_state(self, session: BackendSession, state: str, message: str) -> None:
        try:
            self._call(
                "pane.report_agent",
                {"pane_id": session.id, "custom_status": state, "message": message},
            )
        except HerdrError as err:
            # Non-fatal: status DB is the source of truth.
            logger.debug("herdr report_agent failed (non-fatal): %s", err)

    # ── advanced ──────────────────────────────────────────────────────

    @staticmethod
    def attach_argv(pane_id: str) -> list[str]:
        """Public helper used by tests to assert the attach command-line."""
        return ["herdr", "agent", "attach", pane_id]

    def _command_for_prompt(self, prompt: str | None) -> str:
        return shlex.quote(prompt) if prompt else ""
