"""Sprint 025: StatusTracker forwards updates to the active backend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from open_orchestrator.core.status import StatusConfig, StatusTracker
from open_orchestrator.models.backend import BackendKind, BackendSession
from open_orchestrator.models.status import AIActivityStatus


@pytest.fixture
def tracker(tmp_path: Path) -> StatusTracker:
    cfg = StatusConfig(storage_path=tmp_path / "status.db")
    t = StatusTracker(cfg)
    t.initialize_status(
        worktree_name="wt-feat",
        worktree_path=str(tmp_path / "wt"),
        branch="feat/x",
        tmux_session="owt-wt-feat",
        ai_tool="claude",
    )
    return t


def test_update_without_backend_does_not_call_anything(tracker: StatusTracker) -> None:
    result = tracker.update_task("wt-feat", "doing X")
    assert result is not None
    assert result.activity_status == AIActivityStatus.WORKING


def test_update_with_backend_forwards_state(tracker: StatusTracker) -> None:
    backend = MagicMock()
    backend.kind = BackendKind.HERDR

    tracker.update_task("wt-feat", "doing X", backend=backend)

    backend.report_agent_state.assert_called_once()
    args, _ = backend.report_agent_state.call_args
    session, state, message = args
    assert isinstance(session, BackendSession)
    assert session.worktree_name == "wt-feat"
    assert state == "working"
    assert message == "doing X"


def test_backend_forwarding_is_non_fatal(tracker: StatusTracker) -> None:
    """An exception inside the backend must NOT break the status write."""
    backend = MagicMock()
    backend.kind = BackendKind.HERDR
    backend.report_agent_state.side_effect = RuntimeError("herdr down")

    result = tracker.update_task("wt-feat", "doing X", backend=backend)
    assert result is not None
    assert result.activity_status == AIActivityStatus.WORKING
    assert result.current_task == "doing X"
