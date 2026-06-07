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


class TestUsageCommand:
    @patch("open_orchestrator.commands._shared.get_status_tracker")
    def test_usage_text_output(self, mock_tracker: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker.return_value.usage_counts.return_value = {"new": 5, "workflow": 2, "control_plane": 9}
        result = cli_runner.invoke(main, ["usage"])
        assert result.exit_code == 0, result.output
        assert "worktrees started" in result.output
        assert "5" in result.output

    @patch("open_orchestrator.commands._shared.get_status_tracker")
    def test_usage_json_output(self, mock_tracker: MagicMock, cli_runner: CliRunner) -> None:
        mock_tracker.return_value.usage_counts.return_value = {"new": 3}
        result = cli_runner.invoke(main, ["usage", "--json", "--days", "7"])
        assert result.exit_code == 0, result.output
        import json

        payload = json.loads(result.output)
        assert payload == {"days": 7, "counts": {"new": 3}}


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
