"""Tests for agent commands: note, hook, wait."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.models.status import AIActivityStatus


class TestNoteCommand:
    @patch("open_orchestrator.commands._shared.StatusTracker")
    def test_note_adds_and_shares(self, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker = mock_status.return_value
        mock_tracker.get_all_statuses.return_value = []
        mock_tracker.get_shared_notes.return_value = ["test note"]

        result = cli_runner.invoke(main, ["note", "test note"])
        assert result.exit_code == 0
        mock_tracker.add_shared_note.assert_called_once_with("test note")

    @patch("open_orchestrator.commands._shared.StatusTracker")
    def test_note_clear(self, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker = mock_status.return_value
        result = cli_runner.invoke(main, ["note", "--clear", "ignored"])
        assert result.exit_code == 0
        mock_tracker.clear_shared_notes.assert_called_once()


class TestHookCommand:
    @patch("open_orchestrator.commands._shared.StatusTracker")
    def test_hook_working_event(self, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker = mock_status.return_value
        mock_wt_status = MagicMock()
        mock_tracker.get_status.return_value = mock_wt_status

        result = cli_runner.invoke(main, ["hook", "--event", "working", "--worktree", "my-wt"])
        assert result.exit_code == 0


class TestWaitCommand:
    @patch("open_orchestrator.commands._shared.StatusTracker")
    def test_wait_completed(self, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker = mock_status.return_value
        mock_wt_status = MagicMock()
        mock_wt_status.activity_status = AIActivityStatus.COMPLETED
        mock_wt_status.current_task = "done"
        mock_tracker.get_status.return_value = mock_wt_status

        result = cli_runner.invoke(main, ["wait", "my-feature", "--poll", "1", "--timeout", "5"])
        assert result.exit_code == 0
        assert "completed" in result.output.lower()

    @patch("open_orchestrator.commands._shared.StatusTracker")
    def test_wait_waiting(self, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker = mock_status.return_value
        mock_wt_status = MagicMock()
        mock_wt_status.activity_status = AIActivityStatus.WAITING
        mock_wt_status.current_task = "done"
        mock_tracker.get_status.return_value = mock_wt_status

        result = cli_runner.invoke(main, ["wait", "my-feature", "--poll", "1", "--timeout", "5"])
        assert result.exit_code == 0
        assert "waiting" in result.output.lower()

    @patch("open_orchestrator.commands._shared.StatusTracker")
    def test_wait_no_status(self, mock_status: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker = mock_status.return_value
        mock_tracker.get_status.return_value = None

        result = cli_runner.invoke(main, ["wait", "missing-wt", "--poll", "1", "--timeout", "1"])
        assert result.exit_code != 0
        assert "no status" in result.output.lower()
