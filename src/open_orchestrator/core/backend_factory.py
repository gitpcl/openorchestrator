"""Backend selection and instantiation.

Resolves CLI overrides + config to a concrete :class:`MultiplexerBackend`.
The factory caches the selected instance per CLI invocation so call sites
talk to one backend per process.
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

from open_orchestrator.core.multiplexer import MultiplexerBackend
from open_orchestrator.models.backend import BackendKind

if TYPE_CHECKING:
    from open_orchestrator.models.backend import BackendConfig

logger = logging.getLogger(__name__)


class BackendUnavailableError(RuntimeError):
    """Raised when the requested backend is not installed or reachable."""


_CACHE: dict[str, MultiplexerBackend] = {}


def detect_herdr(socket_session: str = "default") -> bool:
    """Return True when ``herdr`` is on PATH *and* its socket is alive."""
    if shutil.which("herdr") is None:
        return False
    try:
        import asyncio

        from open_orchestrator.core.herdr_client import HerdrClient

        async def _probe() -> bool:
            client = HerdrClient(session=socket_session, request_timeout=1.0)
            try:
                await client.connect()
                return await client.ping()
            finally:
                await client.close()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return bool(asyncio.run(_probe()))
        return bool(loop.run_until_complete(_probe()))
    except Exception:  # noqa: BLE001 — detector must never raise
        return False


def _build_tmux() -> MultiplexerBackend:
    from open_orchestrator.core.tmux_backend import TmuxBackend

    return TmuxBackend()


def _build_herdr(config: BackendConfig | None) -> MultiplexerBackend:
    from open_orchestrator.core.herdr_backend import HerdrBackend

    session = config.herdr_session if config else "default"
    socket = config.herdr_socket if config else None
    return HerdrBackend(session=session, socket_path=socket)


def select_backend(
    config: BackendConfig | None = None,
    override: str | None = None,
) -> MultiplexerBackend:
    """Pick a backend based on ``override`` (CLI flag) and ``config.mode``.

    Behavior:
      - ``override == "tmux"`` → always tmux
      - ``override == "herdr"`` → herdr, raising if unreachable
      - ``mode == "tmux"`` (default) → tmux
      - ``mode == "herdr"`` → herdr, raising if unreachable
      - ``mode == "auto"`` → herdr when detected else tmux
    """
    mode = (override or (config.mode if config else "tmux")).lower()
    session = config.herdr_session if config else "default"
    cache_key = f"{mode}:{session}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    if mode == "tmux":
        backend: MultiplexerBackend = _build_tmux()
    elif mode == "herdr":
        if not detect_herdr(session):
            raise BackendUnavailableError(
                "herdr is not installed or its socket is not reachable. Install from https://herdr.dev/install.sh, then retry."
            )
        backend = _build_herdr(config)
    elif mode == "auto":
        backend = _build_herdr(config) if detect_herdr(session) else _build_tmux()
        logger.debug("backend auto-selected: %s", backend.kind.value)
    else:
        raise BackendUnavailableError(f"Unknown backend mode: {mode!r}")

    _CACHE[cache_key] = backend
    return backend


def clear_cache() -> None:
    """For tests — clear the cached selection."""
    _CACHE.clear()


def selected_kind(backend: MultiplexerBackend) -> BackendKind:
    return backend.kind
