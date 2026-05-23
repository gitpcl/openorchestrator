"""Sprint 024: ``--legacy-cards`` keeps the old grid view alive for one release."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from open_orchestrator.cli import main


def test_no_args_launches_control_plane() -> None:
    runner = CliRunner()
    with patch("open_orchestrator.core.control_plane_view.ControlPlaneApp") as mock_app:
        mock_app.return_value.run.return_value = None
        result = runner.invoke(main, [])
    assert result.exit_code == 0, result.output
    mock_app.assert_called_once()


def test_legacy_flag_launches_switchboard() -> None:
    runner = CliRunner()
    with patch("open_orchestrator.core.switchboard.launch_switchboard") as mock_switchboard:
        result = runner.invoke(main, ["--legacy-cards"])
    assert result.exit_code == 0, result.output
    mock_switchboard.assert_called_once()
    # Deprecation banner emitted on stderr
    assert "deprecation" in result.stderr.lower() or "deprecation" in result.output.lower()


def test_attach_command_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["attach", "--help"])
    assert result.exit_code == 0
    assert "hand off" in result.output.lower()
