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
import time
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
            meta={
                "workspace_id": workspace_id,
                "socket": self._socket_path,
                "herdr_session": self._session_name,
            },
        )

    def _discover_root_pane(self, workspace_id: str) -> str:
        """Probe for the root pane when workspace.create didn't return one.

        ``pane.list`` is the reliable path on herdr v0.6.1 and answers
        instantly; ``workspace.panes`` is intentionally *not* probed because
        it times out on that build (5s per call). ``workspace.get`` is a
        last-resort fallback for builds with yet another shape.
        """
        if not workspace_id:
            return ""
        pane = self._root_pane_for_workspace(workspace_id)
        if pane:
            return pane
        try:
            res = self._call("workspace.get", {"workspace_id": workspace_id})
        except HerdrError:
            return ""
        _, pane = _extract_workspace_pane(res)
        return pane

    def _find_workspace_id(self, label: str) -> str:
        """Resolve a worktree label to its herdr workspace id via workspace.list.

        ``workspace.find`` times out on herdr v0.6.1, so we scan the
        ``workspace.list`` payload (which answers instantly) by label.
        """
        try:
            listing = self._call("workspace.list", {})
        except HerdrError:
            return ""
        workspaces = listing.get("workspaces") if isinstance(listing, dict) else listing
        if not isinstance(workspaces, list):
            return ""
        for ws in workspaces:
            if isinstance(ws, dict) and ws.get("label") == label:
                return _coerce_id(ws.get("workspace_id") or ws.get("id") or "")
        return ""

    def _root_pane_for_workspace(self, workspace_id: str) -> str:
        """Return the root pane id for a workspace via pane.list.

        Prefers the focused pane, else the first. Handles the v0.6.1
        ``pane.list`` shape whose panes key the id as ``pane_id`` (which the
        generic ``_extract_workspace_pane`` does not recognize).
        """
        if not workspace_id:
            return ""
        try:
            listing = self._call("pane.list", {"workspace_id": workspace_id})
        except HerdrError:
            return ""
        panes = listing.get("panes") if isinstance(listing, dict) else listing
        if not isinstance(panes, list) or not panes:
            return ""
        dict_panes = [p for p in panes if isinstance(p, dict)]
        if not dict_panes:
            return ""
        chosen = next((p for p in dict_panes if p.get("focused")), dict_panes[0])
        return str(chosen.get("pane_id") or chosen.get("paneId") or chosen.get("root_pane_id") or "")

    def _pane_agent_status(self, workspace_id: str, pane_id: str) -> tuple[str | None, str | None]:
        """Return ``(agent, agent_status)`` for a pane, or ``(None, None)``."""
        try:
            listing = self._call("pane.list", {"workspace_id": workspace_id})
        except HerdrError:
            return None, None
        panes = listing.get("panes") if isinstance(listing, dict) else None
        if not isinstance(panes, list):
            return None, None
        for p in panes:
            if isinstance(p, dict) and p.get("pane_id") == pane_id:
                return p.get("agent"), p.get("agent_status")
        return None, None

    def wait_for_ready(self, session: BackendSession, *, timeout: float = 20.0, poll_interval: float = 0.4) -> bool:
        """Block until the pane's agent has booted and is idle (ready for input).

        A freshly-created pane reports ``agent=None, agent_status="unknown"``
        while the TUI is still starting, then flips to ``agent=<tool>,
        agent_status="idle"`` once it is waiting for input. Delivering a
        prompt before that races the boot — the submit keystroke lands during
        startup and is lost, leaving the prompt sitting unsent. Mirrors the
        tmux backend's ``wait_for_ai_ready`` gate.

        Returns ``True`` if readiness was observed, ``False`` on timeout
        (caller should still attempt delivery — best effort).
        """
        workspace_id = str(session.meta.get("workspace_id", ""))
        if not workspace_id:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            agent, status = self._pane_agent_status(workspace_id, session.id)
            if agent and status == "idle":
                return True
            time.sleep(poll_interval)
        logger.debug(
            "herdr pane %s not ready after %.1fs (agent boot slow?); delivering prompt anyway",
            session.id,
            timeout,
        )
        return False

    def submit_prompt(
        self,
        session: BackendSession,
        prompt: str,
        *,
        ready_timeout: float = 20.0,
        confirm_window: float = 12.0,
        nudge_interval: float = 1.5,
    ) -> bool:
        """Deliver ``prompt`` to the agent and confirm it actually submitted.

        herdr reports ``agent_status="idle"`` while a TUI agent is still
        finishing its startup splash (loading skills/project context), so a
        single send can race the boot: the body text lands in the input box
        but the submit CR is consumed during render and lost, leaving the
        prompt sitting unsent.

        Strategy: wait for first readiness, type body + CR, then poll
        ``agent_status``. While the pane is still ``idle`` the body is sitting
        unsent in the input — nudge it with a standalone CR (the body is
        already there, so this only re-submits, never duplicates text) until
        the pane leaves ``idle`` (= the agent accepted the prompt) or the
        confirm window elapses.

        Returns ``True`` if submission was confirmed, ``False`` otherwise.
        """
        self.wait_for_ready(session, timeout=ready_timeout)
        workspace_id = str(session.meta.get("workspace_id", ""))
        self._send_line(session.id, prompt)
        if not workspace_id:
            return False
        deadline = time.monotonic() + confirm_window
        while time.monotonic() < deadline:
            time.sleep(nudge_interval)
            _, status = self._pane_agent_status(workspace_id, session.id)
            if status and status != "idle":
                return True  # agent picked up the prompt
            # Still idle: the body is in the input but unsent — nudge submit.
            try:
                self._call("pane.send_text", {"pane_id": session.id, "text": "\r"})
            except HerdrError as err:
                logger.debug("herdr submit nudge failed: %s", err)
        logger.debug("herdr prompt for pane %s never left idle within confirm window", session.id)
        return False

    def session_for(self, worktree_name: str) -> BackendSession | None:
        workspace_id = self._find_workspace_id(worktree_name)
        if not workspace_id:
            return None
        pane_id = self._root_pane_for_workspace(workspace_id)
        if not pane_id:
            return None
        return BackendSession(
            kind=self.kind,
            id=pane_id,
            worktree_name=worktree_name,
            meta={
                "workspace_id": workspace_id,
                "socket": self._socket_path,
                "herdr_session": self._session_name,
            },
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
        """Type ``text`` into the pane and submit it.

        Delegates to :meth:`_send_line`, which delivers the body and a
        standalone ``\\r`` as two separate ``pane.send_text`` calls — a TUI
        agent ignores a CR trapped inside one pasted blob but honors a CR
        sent on its own as Enter.
        """
        self._send_line(session.id, text)

    def send_keys(self, session: BackendSession, keys: str) -> None:
        self._call("pane.send_keys", {"pane_id": session.id, "keys": keys})

    def _send_line(self, pane_id: str, text: str) -> None:
        """Type ``text`` into the pane and submit it with a carriage return.

        The body and the ``\\r`` terminator are delivered as **two separate**
        ``pane.send_text`` calls. This matters: empirically (herdr v0.6.1) a
        single ``"body\\r"`` blob leaves the prompt sitting unsent in the
        agent's input box — a TUI (pi, claude, droid) does not treat the
        trailing CR inside one pasted chunk as Enter. A standalone ``"\\r"``
        delivered *after* the body is processed as a discrete submit.

        ``pane.send_keys`` is intentionally only used for the ``keys:``
        override and is not the default: on herdr v0.6.1 it times out, so
        relying on it would hang every prompt for the RPC timeout.

        Override via ``OWT_HERDR_SUBMIT`` if your herdr build differs
        (e.g. ``OWT_HERDR_SUBMIT=text:\\r\\n`` or ``OWT_HERDR_SUBMIT=keys:Return``).
        """
        body = text.rstrip("\r\n")
        mode, terminator = _resolve_submit_mode()

        if mode == "text":
            # Two calls, never one combined blob — see docstring.
            if body:
                self._call("pane.send_text", {"pane_id": pane_id, "text": body})
            self._call("pane.send_text", {"pane_id": pane_id, "text": terminator})
            return

        # mode == "keys": send body via pane.send_text, then a key event.
        if body:
            self._call("pane.send_text", {"pane_id": pane_id, "text": body})
        try:
            self._call("pane.send_keys", {"pane_id": pane_id, "keys": terminator})
        except HerdrError as err:
            logger.debug(
                "herdr pane.send_keys(%r) failed (%s); falling back to a standalone \\r",
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
