"""Tests for owt doctor command.

Sprint 026 P2 added branch-mode safety: branch rows are reconciled
against the branch list (not the worktree list), so a healthy in-place
branch session is never mistakenly flagged or removed by ``--fix``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


def _wt_info(name: str, *, is_main: bool = False) -> SimpleNamespace:
    return SimpleNamespace(name=name, is_main=is_main)


def _status_row(
    name: str,
    *,
    session_type: str = "worktree",
    backend_kind: str = "tmux",
    backend_session_id: str | None = None,
    backend_meta: dict | None = None,
) -> MagicMock:
    row = MagicMock()
    row.worktree_name = name
    row.session_type = session_type
    row.backend_kind = backend_kind
    row.backend_session_id = backend_session_id or f"owt-{name}"
    row.backend_meta = backend_meta or {}
    return row


class TestDoctor:
    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_clean_state(self, mock_wt: MagicMock, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_wt.return_value.git_root = "/tmp/repo"
        mock_status.return_value.get_all_statuses.return_value = []
        with patch("open_orchestrator.commands.doctor._list_local_branches", return_value=set()):
            result = cli_runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "clean" in result.output.lower() or "no orphaned" in result.output.lower()

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_orphan_status_detected(self, mock_wt: MagicMock, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_wt.return_value.git_root = "/tmp/repo"
        mock_status.return_value.get_all_statuses.return_value = [_status_row("orphan-wt")]
        mock_status.return_value.get_backend_session.return_value = None

        with patch("open_orchestrator.commands.doctor._list_local_branches", return_value=set()):
            result = cli_runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "1 issue" in result.output or "orphan" in result.output.lower()

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_fix_removes_orphan_status(self, mock_wt: MagicMock, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_wt.return_value.git_root = "/tmp/repo"
        mock_status.return_value.get_all_statuses.return_value = [_status_row("orphan-wt")]
        mock_status.return_value.get_backend_session.return_value = None

        with patch("open_orchestrator.commands.doctor._list_local_branches", return_value=set()):
            result = cli_runner.invoke(main, ["doctor", "--fix"])
        assert result.exit_code == 0
        mock_status.return_value.remove_status.assert_called_with("orphan-wt")


class TestDoctorBranchModeSafety:
    """Sprint 026 P2: branch rows are never flagged as worktree orphans."""

    @patch("open_orchestrator.commands.doctor.select_backend_for_session")
    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_healthy_branch_session_is_not_flagged(
        self,
        mock_wt: MagicMock,
        mock_status: MagicMock,
        mock_select: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """A live branch session whose branch exists must NOT appear in the orphan report."""
        mock_wt.return_value.list_all.return_value = []  # no worktrees
        mock_wt.return_value.git_root = "/tmp/repo"

        branch_row = _status_row("fix-bug", session_type="branch")
        mock_status.return_value.get_all_statuses.return_value = [branch_row]

        # Recorded session is alive.
        from open_orchestrator.models.backend import BackendKind, BackendSession

        session = BackendSession(kind=BackendKind.TMUX, id="owt-fix-bug", worktree_name="fix-bug")
        mock_status.return_value.get_backend_session.return_value = session

        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True
        mock_select.return_value = fake_backend

        with patch(
            "open_orchestrator.commands.doctor._list_local_branches",
            return_value={"fix-bug", "main"},
        ):
            result = cli_runner.invoke(main, ["doctor", "--fix"])

        assert result.exit_code == 0
        assert "clean" in result.output.lower() or "no orphaned" in result.output.lower()
        mock_status.return_value.remove_status.assert_not_called()

    @patch("open_orchestrator.commands.doctor.select_backend_for_session")
    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_dead_branch_session_with_missing_branch_is_flagged(
        self,
        mock_wt: MagicMock,
        mock_status: MagicMock,
        mock_select: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Branch row IS flagged when backend dead AND branch deleted out-of-band."""
        mock_wt.return_value.list_all.return_value = []
        mock_wt.return_value.git_root = "/tmp/repo"

        branch_row = _status_row("gone", session_type="branch")
        mock_status.return_value.get_all_statuses.return_value = [branch_row]
        mock_status.return_value.get_backend_session.return_value = None  # dead

        with patch(
            "open_orchestrator.commands.doctor._list_local_branches",
            return_value={"main"},  # 'gone' is not in branches
        ):
            result = cli_runner.invoke(main, ["doctor", "--fix"])

        assert result.exit_code == 0
        assert "1 issue" in result.output
        mock_status.return_value.remove_status.assert_called_with("gone")

    @patch("open_orchestrator.commands.doctor.select_backend_for_session")
    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_mixed_fleet_branch_rows_not_compared_to_worktree_list(
        self,
        mock_wt: MagicMock,
        mock_status: MagicMock,
        mock_select: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """In a mixed fleet, branch rows must never be flagged just because they
        aren't in ``git worktree list`` (the bug Sprint 026 P2 closes)."""
        # One worktree, one branch row, both healthy.
        mock_wt.return_value.list_all.return_value = [_wt_info("feat-x")]
        mock_wt.return_value.git_root = "/tmp/repo"

        wt_row = _status_row("feat-x", session_type="worktree")
        branch_row = _status_row("fix-y", session_type="branch")
        mock_status.return_value.get_all_statuses.return_value = [wt_row, branch_row]

        from open_orchestrator.models.backend import BackendKind, BackendSession

        # Both have live backend sessions.
        sessions = {
            "feat-x": BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x"),
            "fix-y": BackendSession(kind=BackendKind.TMUX, id="owt-fix-y", worktree_name="fix-y"),
        }
        mock_status.return_value.get_backend_session.side_effect = lambda name: sessions.get(name)

        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True
        mock_select.return_value = fake_backend

        with patch(
            "open_orchestrator.commands.doctor._list_local_branches",
            return_value={"feat-x", "fix-y", "main"},
        ):
            result = cli_runner.invoke(main, ["doctor", "--fix"])

        assert result.exit_code == 0
        assert "clean" in result.output.lower() or "no orphaned" in result.output.lower()
        mock_status.return_value.remove_status.assert_not_called()

    @patch("open_orchestrator.commands.doctor.select_backend_for_session")
    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_herdr_backed_worktree_uses_backend_kill_not_tmux(
        self,
        mock_wt: MagicMock,
        mock_status: MagicMock,
        mock_select: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """A herdr-backed worktree row routes ``kill`` through select_backend_for_session
        (which preserves the recorded socket) rather than tmux."""
        # Worktree row whose backend session is *live* but worktree dir is gone.
        mock_wt.return_value.list_all.return_value = []  # worktree dir gone
        mock_wt.return_value.git_root = "/tmp/repo"

        wt_row = _status_row(
            "feat-z",
            session_type="worktree",
            backend_kind="herdr",
            backend_session_id="pane-7",
            backend_meta={"socket": "/custom/h.sock"},
        )
        mock_status.return_value.get_all_statuses.return_value = [wt_row]

        from open_orchestrator.models.backend import BackendKind, BackendSession

        session = BackendSession(
            kind=BackendKind.HERDR,
            id="pane-7",
            worktree_name="feat-z",
            meta={"socket": "/custom/h.sock"},
        )
        mock_status.return_value.get_backend_session.return_value = session

        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True
        mock_select.return_value = fake_backend

        with patch(
            "open_orchestrator.commands.doctor._list_local_branches",
            return_value={"main"},
        ):
            result = cli_runner.invoke(main, ["doctor", "--fix"])

        assert result.exit_code == 0
        # The backend resolved via select_backend_for_session was the
        # one asked to kill the session (no fallback to TmuxManager).
        fake_backend.kill.assert_called_once_with(session)
        # select_backend_for_session received the recorded session, so
        # its socket would be honored when the real factory runs.
        # We assert the helper saw a BackendSession (any call) carrying
        # the herdr socket in its meta.
        assert any(call.args and getattr(call.args[0], "kind", None) == BackendKind.HERDR for call in mock_select.call_args_list)
