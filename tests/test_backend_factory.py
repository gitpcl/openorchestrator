"""Sprint 025: backend factory selection logic."""

from __future__ import annotations

import pytest

from open_orchestrator.core.backend_factory import (
    BackendUnavailableError,
    clear_cache,
    select_backend,
)
from open_orchestrator.models.backend import BackendConfig, BackendKind


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_cache()


def test_tmux_is_default() -> None:
    backend = select_backend(BackendConfig())
    assert backend.kind == BackendKind.TMUX


def test_tmux_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("open_orchestrator.core.backend_factory.detect_herdr", lambda *_: True)
    backend = select_backend(BackendConfig(mode="herdr"), override="tmux")
    assert backend.kind == BackendKind.TMUX


def test_herdr_override_raises_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("open_orchestrator.core.backend_factory.detect_herdr", lambda *_: False)
    with pytest.raises(BackendUnavailableError):
        select_backend(BackendConfig(), override="herdr")


def test_auto_falls_back_to_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("open_orchestrator.core.backend_factory.detect_herdr", lambda *_: False)
    backend = select_backend(BackendConfig(mode="auto"))
    assert backend.kind == BackendKind.TMUX


def test_auto_picks_herdr_when_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("open_orchestrator.core.backend_factory.detect_herdr", lambda *_: True)
    backend = select_backend(BackendConfig(mode="auto"))
    assert backend.kind == BackendKind.HERDR


def test_unknown_mode_raises() -> None:
    with pytest.raises(BackendUnavailableError):
        select_backend(BackendConfig(), override="nope")  # type: ignore[arg-type]


def test_cache_is_per_mode_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("open_orchestrator.core.backend_factory.detect_herdr", lambda *_: True)
    a = select_backend(BackendConfig(mode="auto"))
    b = select_backend(BackendConfig(mode="auto"))
    assert a is b
