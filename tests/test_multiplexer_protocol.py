"""Sprint 025: contract tests for the multiplexer protocol.

The protocol is what call sites depend on; both backends must satisfy
it. We exercise the shape (``isinstance``) and the smoke surface the
adapters share — concrete behavior is covered in
``test_tmux_backend.py`` and ``test_herdr_backend.py``.
"""

from __future__ import annotations

import pytest

from open_orchestrator.core.multiplexer import MultiplexerBackend
from open_orchestrator.models.backend import BackendKind, BackendSession


def _build_tmux():  # noqa: ANN202
    from open_orchestrator.core.tmux_backend import TmuxBackend

    return TmuxBackend()


def _build_herdr():  # noqa: ANN202
    from open_orchestrator.core.herdr_backend import HerdrBackend

    return HerdrBackend(socket_path="/tmp/owt-test-herdr.sock")


@pytest.mark.parametrize("factory", [_build_tmux, _build_herdr])
def test_backend_satisfies_protocol(factory) -> None:  # noqa: ANN001
    backend = factory()
    assert isinstance(backend, MultiplexerBackend)


@pytest.mark.parametrize("factory", [_build_tmux, _build_herdr])
def test_backend_exposes_kind(factory) -> None:  # noqa: ANN001
    backend = factory()
    assert isinstance(backend.kind, BackendKind)


def test_backend_session_is_frozen() -> None:
    session = BackendSession(kind=BackendKind.TMUX, id="x", worktree_name="wt")
    with pytest.raises(Exception):  # noqa: B017,PT011 — Pydantic frozen raises ValidationError
        session.id = "y"  # type: ignore[misc]
