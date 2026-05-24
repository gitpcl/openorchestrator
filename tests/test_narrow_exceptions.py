"""Tests asserting that Phase 5 narrowed exception handlers do NOT silently swallow
unrelated errors at OS/IPC boundaries.

These tests pin the contract: the four priority files (sync.py, orchestrator.py,
intelligence.py, batch.py) catch only the narrow exception types they're documented
to catch — any other exception must propagate (and any caught exception must be
traced via log.exception so it can be debugged after the fact).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core import intelligence
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


# ─── intelligence.py: agno-exposed tools catch narrow errors only ───────────


class TestIntelligenceToolsDoNotSwallowUnrelated:
    """The Agno-exposed _read_file / _git_log / _git_diff_stat tools now catch
    only OSError / SubprocessError. A bug in the tool itself must surface."""

    def test_read_file_propagates_unrelated_runtime_error(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("hello")

        # Force splitlines() to blow up with a non-OSError — must propagate.
        original_read_text = Path.read_text

        def boom(self: Path, *args: object, **kwargs: object) -> str:  # noqa: ARG001
            if self == target.resolve():
                raise RuntimeError("simulated tool bug")
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(intelligence, "_active_repo_root", str(tmp_path)), patch.object(Path, "read_text", boom):
            with pytest.raises(RuntimeError, match="simulated tool bug"):
                intelligence._read_file(str(target))

    def test_git_log_logs_subprocess_error_via_log_exception(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """When _git_log catches a SubprocessError it must log.exception so the
        operator can see why the Agno agent got an Error: response."""

        def fail(*args: object, **kwargs: object) -> None:
            raise subprocess.SubprocessError("git crashed")

        with (
            patch("subprocess.run", side_effect=fail),
            caplog.at_level(logging.ERROR, logger="open_orchestrator.core.intelligence"),
        ):
            out = intelligence._git_log(str(tmp_path))

        assert out.startswith("Error:")
        assert any("_git_log failed" in rec.message for rec in caplog.records)
        assert any(rec.exc_info is not None for rec in caplog.records)


# ─── orchestrator.py: reconciliation does not eat unrelated errors ──────────


class TestOrchestratorReconcileDoesNotSwallow:
    """_reconcile_world_state narrowed its inspector except to (OSError, RuntimeError).
    Anything else (e.g. a KeyError from a misconfigured runtime mock) must propagate."""

    def test_reconcile_propagates_unrelated_key_error(self) -> None:
        from open_orchestrator.core.orchestrator import (
            Orchestrator,
            OrchestratorState,
            TaskPhase,
            TaskState,
        )

        state = OrchestratorState(
            goal="g",
            feature_branch="feat/x",
            repo_path="/tmp/repo",
            plan_path="/tmp/plan.toml",
            tasks=[
                TaskState(
                    id="t1",
                    description="d",
                    status=TaskPhase.RUNNING,
                    worktree_name="wt-1",
                ),
            ],
        )
        # Skip filesystem touches in __init__
        with (
            patch("open_orchestrator.core.orchestrator.StatusTracker"),
            patch("open_orchestrator.core.orchestrator.TmuxManager"),
            patch.object(Orchestrator, "_save_state"),
        ):
            orch = Orchestrator(state)

            # tmux says session is dead so we hit inspect_worktree_commits
            orch.tmux.session_exists = MagicMock(return_value=False)  # type: ignore[method-assign]
            orch.tmux.generate_session_name = MagicMock(return_value="orch-t1")  # type: ignore[method-assign]
            orch._runtime.inspect_worktree_commits = MagicMock(  # type: ignore[method-assign]
                side_effect=KeyError("unexpected missing key"),
            )

            with pytest.raises(KeyError):
                orch._reconcile_world_state()
