"""Tests for database maintenance CLI commands: purge, vacuum, health."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


class TestDbPurge:
    """Test owt db purge command."""

    @patch("open_orchestrator.commands.db_cmd.get_status_tracker")
    def test_purge_default_days(self, mock_get_tracker: MagicMock) -> None:
        mock_get_tracker.return_value.purge_old_messages.return_value = 5
        runner = CliRunner()
        result = runner.invoke(main, ["db", "purge"])
        assert result.exit_code == 0
        assert "5 message(s)" in result.output
        mock_get_tracker.return_value.purge_old_messages.assert_called_once_with(30)

    @patch("open_orchestrator.commands.db_cmd.get_status_tracker")
    def test_purge_custom_days(self, mock_get_tracker: MagicMock) -> None:
        mock_get_tracker.return_value.purge_old_messages.return_value = 12
        runner = CliRunner()
        result = runner.invoke(main, ["db", "purge", "--days", "7"])
        assert result.exit_code == 0
        assert "12 message(s)" in result.output
        mock_get_tracker.return_value.purge_old_messages.assert_called_once_with(7)


class TestDbVacuum:
    """Test owt db vacuum command."""

    @patch("open_orchestrator.commands.db_cmd.get_status_tracker")
    def test_vacuum(self, mock_get_tracker: MagicMock) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["db", "vacuum"])
        assert result.exit_code == 0
        assert "optimized" in result.output.lower() or "vacuumed" in result.output.lower()
        mock_get_tracker.return_value.vacuum.assert_called_once()


class TestDbHealth:
    """Test owt db health command."""

    @patch("open_orchestrator.commands.db_cmd.get_status_tracker")
    def test_health_json_output(self, mock_get_tracker: MagicMock) -> None:
        import json

        mock_get_tracker.return_value.health_check.return_value = {
            "schema_version": 1,
            "worktree_count": 3,
            "peer_message_count": 42,
            "unread_message_count": 2,
            "db_size_bytes": 8192,
            "wal_mode": True,
        }
        runner = CliRunner()
        result = runner.invoke(main, ["db", "health"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["worktree_count"] == 3
        assert output["peer_message_count"] == 42

    @patch("open_orchestrator.commands.db_cmd.get_status_tracker")
    def test_health_check_passes(self, mock_get_tracker: MagicMock) -> None:
        mock_get_tracker.return_value.health_check.return_value = {
            "peer_message_count": 100,
            "db_size_bytes": 1024,
        }
        runner = CliRunner()
        result = runner.invoke(main, ["db", "health", "--check"])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    @patch("open_orchestrator.commands.db_cmd.get_status_tracker")
    def test_health_check_fails_on_high_message_count(self, mock_get_tracker: MagicMock) -> None:
        mock_get_tracker.return_value.health_check.return_value = {
            "peer_message_count": 15_000,
            "db_size_bytes": 1024,
        }
        runner = CliRunner()
        result = runner.invoke(main, ["db", "health", "--check"])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    @patch("open_orchestrator.commands.db_cmd.get_status_tracker")
    def test_health_check_fails_on_large_db(self, mock_get_tracker: MagicMock) -> None:
        mock_get_tracker.return_value.health_check.return_value = {
            "peer_message_count": 100,
            "db_size_bytes": 200_000_000,
        }
        runner = CliRunner()
        result = runner.invoke(main, ["db", "health", "--check"])
        assert result.exit_code == 1
        assert "FAIL" in result.output
