"""Tests for owt doctor command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


class TestDoctor:
    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.core.tmux_manager.TmuxManager")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_clean_state(self, mock_wt: MagicMock, mock_tmux: MagicMock, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_status.return_value.get_all_statuses.return_value = []
        result = cli_runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "clean" in result.output.lower() or "no orphaned" in result.output.lower()

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.core.tmux_manager.TmuxManager")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_orphan_status_detected(
        self, mock_wt: MagicMock, mock_tmux: MagicMock, mock_status: MagicMock, cli_runner: CliRunner
    ) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_wt.return_value.git_root = "/tmp/repo"
        # Status entry with no matching worktree
        status_entry = MagicMock()
        status_entry.worktree_name = "orphan-wt"
        mock_status.return_value.get_all_statuses.return_value = [status_entry]
        mock_tmux.return_value.session_exists.return_value = False

        result = cli_runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "1 issue" in result.output or "orphan" in result.output.lower()

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.core.tmux_manager.TmuxManager")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_fix_removes_orphan_status(
        self, mock_wt: MagicMock, mock_tmux: MagicMock, mock_status: MagicMock, cli_runner: CliRunner
    ) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_wt.return_value.git_root = "/tmp/repo"
        status_entry = MagicMock()
        status_entry.worktree_name = "orphan-wt"
        mock_status.return_value.get_all_statuses.return_value = [status_entry]
        mock_tmux.return_value.session_exists.return_value = False

        result = cli_runner.invoke(main, ["doctor", "--fix"])
        assert result.exit_code == 0
        mock_status.return_value.remove_status.assert_called_with("orphan-wt")
