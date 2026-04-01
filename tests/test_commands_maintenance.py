"""Tests for maintenance commands: version, sync, cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


class TestVersionCommand:
    def test_version_output(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "open-orchestrator" in result.output

    def test_version_not_empty(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["version"])
        assert len(result.output.strip()) > 0


class TestSyncCommand:
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_sync_requires_arg_or_all(self, mock_wt: MagicMock, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["sync"])
        assert result.exit_code != 0
        assert "specify" in result.output.lower() or "error" in result.output.lower()


class TestCleanupCommand:
    @patch("open_orchestrator.core.cleanup.CleanupService")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_cleanup_dry_run(self, mock_wt: MagicMock, mock_cleanup: MagicMock, cli_runner: CliRunner) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_report = MagicMock()
        mock_report.stale_worktrees_found = 0
        mock_cleanup.return_value.cleanup.return_value = mock_report

        result = cli_runner.invoke(main, ["cleanup"])
        assert result.exit_code == 0

    @patch("open_orchestrator.core.cleanup.CleanupService")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_cleanup_json_output(self, mock_wt: MagicMock, mock_cleanup: MagicMock, cli_runner: CliRunner) -> None:
        mock_wt.return_value.list_all.return_value = []
        mock_report = MagicMock()
        mock_report.stale_worktrees_found = 0
        mock_report.model_dump.return_value = {"stale_worktrees_found": 0}
        mock_cleanup.return_value.cleanup.return_value = mock_report

        result = cli_runner.invoke(main, ["cleanup", "--json"])
        assert result.exit_code == 0
