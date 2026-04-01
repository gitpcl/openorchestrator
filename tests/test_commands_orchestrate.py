"""Tests for orchestrate/batch/plan commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


class TestOrchestrateStatus:
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_status_no_state(self, mock_wt: MagicMock, cli_runner: CliRunner, tmp_path) -> None:
        mock_wt.return_value.git_root = tmp_path
        result = cli_runner.invoke(main, ["orchestrate", "--status"])
        assert result.exit_code == 0
        assert "no orchestrator state" in result.output.lower()


class TestBatchCommand:
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_batch_requires_file_or_resume(self, mock_wt: MagicMock, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["batch"])
        assert result.exit_code != 0
        assert "provide" in result.output.lower() or "error" in result.output.lower()

    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_batch_resume_no_state(self, mock_wt: MagicMock, cli_runner: CliRunner, tmp_path) -> None:
        mock_wt.return_value.git_root = tmp_path
        result = cli_runner.invoke(main, ["batch", "--resume"])
        assert result.exit_code != 0
        assert "no batch state" in result.output.lower()


class TestPlanCommand:
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_plan_requires_goal(self, mock_wt: MagicMock, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["plan"])
        assert result.exit_code != 0
