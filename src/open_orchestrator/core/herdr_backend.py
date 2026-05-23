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


_DEFAULT_SUBMIT_MODE = "text"
_DEFAULT_SUBMIT_TERMINATOR = "\r"


def _resolve_submit_mode() -> tuple[str, str]:
    """Pick how to submit a typed prompt to a herdr pane.

    Reads ``OWT_HERDR_SUBMIT`` for a per-deployment override. Supported
    values:

      - unset                  → ``("text", "\\r")``   (default — carriage
                                 return is what real Enter delivers to
                                 stdin in raw mode)
      - ``text:<terminator>``  → embed the terminator in ``pane.send_text``;
                                 examples: ``text:\\r``, ``text:\\r\\n``,
                                 ``text:\\n``
      - ``keys:<key>``         → call ``pane.send_keys`` with the named
                                 key after the text;
                                 examples: ``keys:Enter``, ``keys:Return``,
                                 ``keys:C-m``

    Backslash escapes ``\\r`` and ``\\n`` in the terminator/key are
    expanded so the env var is readable in a shell.
    """
    raw = os.environ.get("OWT_HERDR_SUBMIT", "").strip()
    if not raw:
        return _DEFAULT_SUBMIT_MODE, _DEFAULT_SUBMIT_TERMINATOR
    if ":" not in raw:
        return _DEFAULT_SUBMIT_MODE, raw.replace("\\r", "\r").replace("\\n", "\n")
    mode, _, value = raw.partition(":")
    mode = mode.strip().lower()
    value = value.replace("\\r", "\r").replace("\\n", "\n")
    if mode not in {"text", "keys"}:
        logger.warning("Unknown OWT_HERDR_SUBMIT mode %r — using default", mode)
        return _DEFAULT_SUBMIT_MODE, _DEFAULT_SUBMIT_TERMINATOR
    return mode, value or _DEFAULT_SUBMIT_TERMINATOR


# ── response parsing helpers ────────────────────────────────────────


# NB: a bare ``id`` is workspace-only on purpose. Pane ids must use a
# pane-specific key so a payload like ``{"id": "ws-9", "rootPaneId": ...}``
# doesn't accidentally pick the workspace id as the pane id.
_WORKSPACE_ID_KEYS = ("workspace_id", "id", "ws_id", "uuid")
_PANE_ID_KEYS = ("root_pane_id", "rootPaneId", "pane_id", "paneId", "root_pane")


def _coerce_id(value: Any) -> str:
    """Pull an id out of a string, int, or single-key dict."""
    if value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value)
    if isinstance(value, dict):
        for key in ("id", "uuid", "name"):
            if key in value and value[key] is not None:
                return str(value[key])
    return ""


def _scan_for_id(payload: Any, keys: tuple[str, ...]) -> str:
    """Return the first non-empty id found under ``keys`` in ``payload``."""
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        if key in payload and payload[key] is not None:
            value = _coerce_id(payload[key])
            if value:
                return value
    return ""


def _extract_workspace_pane(payload: Any) -> tuple[str, str]:
    """Parse herdr workspace/pane ids out of an RPC payload.

    Tolerates the shapes seen across herdr builds:
      - ``{"workspace": {"workspace_id": ...}, "root_pane": {"pane_id": ...}}``
        (current ``workspace_created`` shape — sibling sub-objects, not envelope)
      - ``{"workspace_id": ..., "root_pane_id": ...}``        (flat)
      - ``{"id": ..., "pane_id": ...}``                        (alt names)
      - ``{"workspace": {"panes": [...]}}``                    (panes array)
      - ``{"data": {...}}`` / ``{"result": {...}}``            (RPC envelope)
    """
    if payload is None:
        return "", ""
    if isinstance(payload, list):
        if not payload:
            return "", ""
        return _extract_workspace_pane(payload[0])
    if not isinstance(payload, dict):
        return "", ""

    # 1. Unwrap a generic RPC envelope (``data`` / ``result``) if present —
    #    the real shape lives one level down.
    for envelope_key in ("data", "result"):
        if envelope_key in payload and isinstance(payload[envelope_key], (dict, list)):
            inner_ws, inner_pane = _extract_workspace_pane(payload[envelope_key])
            if inner_ws or inner_pane:
                return inner_ws, inner_pane

    # 2. Composite shape — sibling sub-objects ``workspace`` + ``root_pane``.
    #    This is the shape herdr's ``workspace_created`` event uses today.
    workspace_id = ""
    pane_id = ""

    ws_obj = payload.get("workspace")
    if isinstance(ws_obj, dict):
        workspace_id = _scan_for_id(ws_obj, _WORKSPACE_ID_KEYS)
        # The pane id is sometimes nested inside the workspace sub-object
        # (either as a flat key or inside a ``panes`` array).
        pane_id = _scan_for_id(ws_obj, _PANE_ID_KEYS)
        if not pane_id:
            nested_panes = ws_obj.get("panes")
            if isinstance(nested_panes, list) and nested_panes:
                pane_id = _coerce_id(nested_panes[0])

    if not pane_id:
        for pane_key in ("root_pane", "rootPane", "pane"):
            sub = payload.get(pane_key)
            if isinstance(sub, dict):
                pane_id = _scan_for_id(sub, _PANE_ID_KEYS)
                if pane_id:
                    break

    if workspace_id or pane_id:
        # If only one was found, still let the flat-key sweep below fill the gap.
        if not workspace_id:
            workspace_id = _scan_for_id(payload, _WORKSPACE_ID_KEYS)
        if not pane_id:
            pane_id = _scan_for_id(payload, _PANE_ID_KEYS)
            if not pane_id:
                panes = payload.get("panes")
                if isinstance(panes, list) and panes:
                    pane_id = _coerce_id(panes[0])
        return workspace_id, pane_id

    # 3. Flat shape — keys live at the top level.
    workspace_id = _scan_for_id(payload, _WORKSPACE_ID_KEYS)
    pane_id = _scan_for_id(payload, _PANE_ID_KEYS)

    # 4. Fallback: pull pane id out of a ``panes`` array.
    if not pane_id:
        panes = payload.get("panes")
        if isinstance(panes, list) and panes:
            pane_id = _coerce_id(panes[0])

    return workspace_id, pane_id


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
        raw = self._call(
            "workspace.create",
            {"cwd": cwd, "label": worktree_name},
        )
        workspace_id, pane_id = _extract_workspace_pane(raw)
        if not pane_id:
            # Some herdr builds return only a workspace id from create and
            # require a follow-up call to discover the root pane. Try both
            # common probe methods before giving up.
            pane_id = self._discover_root_pane(workspace_id)
        if not pane_id:
            raise HerdrError(
                "herdr.workspace.create did not return a pane id. "
                f"Raw response: {raw!r}. If your herdr build uses different "
                "field names, please share this output."
            )
        if agent_command:
            # Type the command then press Enter as a real key event — the
            # newline-in-text trick works for some shells but not for TUI
            # agents that read raw stdin and treat \n as "newline in input".
            self._send_line(pane_id, agent_command)
        return BackendSession(
            kind=self.kind,
            id=pane_id,
            worktree_name=worktree_name,
            meta={"workspace_id": workspace_id, "socket": self._socket_path},
        )

    def _discover_root_pane(self, workspace_id: str) -> str:
        """Probe for the root pane when workspace.create didn't return one."""
        if not workspace_id:
            return ""
        for method, params in (
            ("workspace.get", {"workspace_id": workspace_id}),
            ("workspace.panes", {"workspace_id": workspace_id}),
            ("pane.list", {"workspace_id": workspace_id}),
        ):
            try:
                res = self._call(method, params)
            except HerdrError:
                continue
            _, pane = _extract_workspace_pane(res)
            if pane:
                return pane
        return ""

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
        """Type ``text`` into the pane and press Enter so the TUI submits.

        ``pane.send_text`` alone leaves a literal newline character in the
        agent's input box (TUI apps read raw stdin and don't treat ``\\n``
        as a submit). We split into two calls so the Enter is a real key
        event, matching what a user pressing Return would deliver.
        """
        self._send_line(session.id, text)

    def send_keys(self, session: BackendSession, keys: str) -> None:
        self._call("pane.send_keys", {"pane_id": session.id, "keys": keys})

    def _send_line(self, pane_id: str, text: str) -> None:
        """Type ``text`` into the pane and submit with a real Enter event.

        We embed ``\\r`` (carriage return) as the terminator because that is
        what a physical Enter key delivers to stdin in raw mode. ``\\n``
        alone is a literal line feed character and TUI agents (pi, claude,
        droid) treat it as "insert newline" rather than "submit".

        Override via ``OWT_HERDR_SUBMIT`` if your herdr build needs a
        different terminator (e.g. ``OWT_HERDR_SUBMIT=text:\\r\\n`` or
        ``OWT_HERDR_SUBMIT=keys:Return``).
        """
        body = text.rstrip("\r\n")
        mode, terminator = _resolve_submit_mode()

        if mode == "text":
            payload = (body + terminator) if body else terminator
            self._call("pane.send_text", {"pane_id": pane_id, "text": payload})
            return

        # mode == "keys": send body via pane.send_text, then a key event.
        if body:
            self._call("pane.send_text", {"pane_id": pane_id, "text": body})
        try:
            self._call("pane.send_keys", {"pane_id": pane_id, "keys": terminator})
        except HerdrError as err:
            logger.debug(
                "herdr pane.send_keys(%r) failed (%s); falling back to embedded \\r",
                terminator,
                err,
            )
            self._call("pane.send_text", {"pane_id": pane_id, "text": "\r"})

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
