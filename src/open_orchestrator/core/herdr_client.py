"""Low-level JSON-RPC client for the herdr socket.

Herdr exposes its multiplexer API over a Unix domain socket using
newline-delimited JSON. Requests carry an ``id`` correlator; responses
arrive on the same socket and must be matched back by id.

This module implements just enough of the protocol for owt:
- ``connect`` / ``close``
- ``call(method, params) -> result`` with id correlation
- ``ping()`` for the detector
- Reconnect-once on broken pipe; surface fatal errors as ``HerdrError``

The actual herdr server lives at ``herdr.dev``; this client is built
against the spec it publishes (workspace.create, pane.send_text, etc).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HerdrError(RuntimeError):
    """Wraps a herdr error response or transport failure."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def default_socket_path(session: str = "default") -> Path:
    """Compute the herdr socket path for ``session``.

    Honors ``$XDG_CONFIG_HOME`` and falls back to ``~/.config``.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    if session == "default":
        return base / "herdr" / "herdr.sock"
    return base / "herdr" / "sessions" / session / "herdr.sock"


class HerdrClient:
    """Asynchronous newline-delimited JSON-RPC client over a Unix socket."""

    def __init__(
        self,
        socket_path: Path | str | None = None,
        *,
        session: str = "default",
        request_timeout: float = 5.0,
    ) -> None:
        if socket_path is None:
            self._socket = default_socket_path(session)
        else:
            self._socket = Path(socket_path)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._id_seq = itertools.count(1)
        self._reader_task: asyncio.Task[None] | None = None
        self._timeout = request_timeout
        self._lock = asyncio.Lock()

    @property
    def socket_path(self) -> Path:
        return self._socket

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    # ── lifecycle ─────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self.connected:
            return
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(str(self._socket))
        except (FileNotFoundError, ConnectionRefusedError) as err:
            raise HerdrError(f"herdr socket not reachable at {self._socket}: {err}") from err
        self._reader_task = asyncio.create_task(self._read_loop(), name="herdr-client-reader")

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
        self._writer = None
        self._reader = None
        # Fail any in-flight requests
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(HerdrError("herdr connection closed"))
        self._pending.clear()

    async def __aenter__(self) -> HerdrClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ── RPC ──────────────────────────────────────────────────────────

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a request and await the response.

        Reconnects once on a broken pipe; further failures raise
        :class:`HerdrError`.
        """
        if not self.connected:
            await self.connect()
        try:
            return await self._call_once(method, params or {})
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("herdr connection dropped — reconnecting once")
            await self.close()
            await self.connect()
            return await self._call_once(method, params or {})

    async def ping(self) -> bool:
        """Best-effort liveness check; returns False rather than raising."""
        try:
            await self.call("ping", {})
            return True
        except HerdrError:
            return False
        except Exception as err:  # noqa: BLE001
            logger.debug("herdr ping failed: %s", err)
            return False

    # ── internals ────────────────────────────────────────────────────

    async def _call_once(self, method: str, params: dict[str, Any]) -> Any:
        if self._writer is None:
            raise HerdrError("herdr client is not connected")
        req_id = f"req_{next(self._id_seq)}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = future
        payload = json.dumps({"id": req_id, "method": method, "params": params}) + "\n"
        async with self._lock:
            self._writer.write(payload.encode("utf-8"))
            await self._writer.drain()
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError as err:
            self._pending.pop(req_id, None)
            raise HerdrError(f"herdr {method} timed out after {self._timeout}s") from err

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.debug("herdr: dropping non-JSON line %r", line[:80])
                    continue
                req_id = str(payload.get("id", ""))
                fut = self._pending.pop(req_id, None)
                if fut is None or fut.done():
                    continue
                err = payload.get("error")
                if err:
                    fut.set_exception(HerdrError(str(err.get("message", "herdr error")), code=err.get("code")))
                else:
                    fut.set_result(payload.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            logger.debug("herdr read loop exiting: %s", err)
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(HerdrError(f"herdr read loop closed: {err}"))
            self._pending.clear()
