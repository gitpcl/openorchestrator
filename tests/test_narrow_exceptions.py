"""Tests asserting that narrowed exception handlers do NOT silently swallow
unrelated errors at OS/IPC boundaries.

These tests pin the contract: sync.py catches only the narrow exception types
it's documented to catch — any other exception must propagate (and any caught
exception must be traced via log.exception so it can be debugged after the fact).
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from open_orchestrator.core.sync import SyncService, SyncStatus

# ─── sync.py: outer except is narrowed ──────────────────────────────────────


class TestSyncBoundaryDoesNotSwallowUnrelated:
    """sync_worktree / get_sync_status catch (SubprocessError, OSError) only."""

    def test_sync_worktree_propagates_unrelated_runtime_error(self, tmp_path: Path) -> None:
        """A RuntimeError raised mid-sync must NOT be converted into an ERROR result —
        only OS/subprocess errors get caught at this IPC boundary."""
        worktree = tmp_path / "wt"
        worktree.mkdir()
        service = SyncService()

        with (
            patch.object(service, "_get_current_branch", return_value="main"),
            patch.object(service, "_get_upstream_branch", return_value="origin/main"),
            patch.object(service, "_has_uncommitted_changes", return_value=False),
            patch.object(service, "_fetch_upstream", side_effect=RuntimeError("unexpected internal bug")),
        ):
            with pytest.raises(RuntimeError, match="unexpected internal bug"):
                service.sync_worktree(str(worktree))

    def test_sync_worktree_logs_when_oserror_is_caught(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """When the narrowed handler catches an OSError, it must log.exception so the
        failure is traceable even though it was converted to a typed result."""
        worktree = tmp_path / "wt"
        worktree.mkdir()
        service = SyncService()

        with (
            patch.object(service, "_get_current_branch", return_value="main"),
            patch.object(service, "_get_upstream_branch", return_value="origin/main"),
            patch.object(service, "_has_uncommitted_changes", return_value=False),
            patch.object(service, "_fetch_upstream", side_effect=OSError("permission denied")),
        ):
            with caplog.at_level(logging.ERROR, logger="open_orchestrator.core.sync"):
                result = service.sync_worktree(str(worktree))

        assert result.status == SyncStatus.ERROR
        assert "Sync failed" in result.message
        assert any("sync_worktree failed" in rec.message for rec in caplog.records)
        # log.exception attaches the traceback
        assert any(rec.exc_info is not None for rec in caplog.records)

    def test_get_sync_status_propagates_unrelated_value_error(self, tmp_path: Path) -> None:
        worktree = tmp_path / "wt"
        worktree.mkdir()
        service = SyncService()

        with (
            patch.object(service, "_get_current_branch", return_value="main"),
            patch.object(service, "_get_upstream_branch", return_value="origin/main"),
            patch.object(service, "_fetch_upstream", side_effect=ValueError("bad ref format")),
        ):
            with pytest.raises(ValueError, match="bad ref format"):
                service.get_sync_status(str(worktree))
