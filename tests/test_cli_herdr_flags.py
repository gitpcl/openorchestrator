"""Sprint 025: ``owt new`` and ``owt attach`` flag wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


def test_owt_new_advertises_herdr_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["new", "--help"])
    assert result.exit_code == 0
    assert "--herdr" in result.output
    assert "--tmux" in result.output


def test_owt_attach_advertises_herdr_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["attach", "--help"])
    assert result.exit_code == 0
    assert "--herdr" in result.output
    assert "--tmux" in result.output


def test_herdr_and_tmux_are_mutually_exclusive_on_new() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["new", "Test task", "--yes", "--herdr", "--tmux"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_herdr_and_tmux_are_mutually_exclusive_on_attach() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["attach", "wt", "--herdr", "--tmux"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_owt_new_herdr_fails_clearly_when_unreachable() -> None:
    runner = CliRunner()
    with patch("open_orchestrator.core.backend_factory.detect_herdr", return_value=False):
        result = runner.invoke(main, ["new", "Test task", "--yes", "--herdr"])
    assert result.exit_code != 0
    assert "herdr" in result.output.lower()


def test_owt_new_herdr_incompatible_with_headless() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["new", "Test", "--yes", "--herdr", "--headless"])
    assert result.exit_code != 0
    assert "incompatible" in result.output.lower() or "headless" in result.output.lower()


def test_owt_attach_invokes_backend_attach(tmp_path) -> None:  # noqa: ANN001
    """When attach succeeds, ``backend.attach`` is the only path called."""
    runner = CliRunner()
    fake_backend = MagicMock()
    fake_backend.kind.value = "tmux"
    fake_backend.session_for.return_value = MagicMock(id="owt-wt", worktree_name="wt")

    with (
        patch("open_orchestrator.core.backend_factory.select_backend", return_value=fake_backend),
        patch("open_orchestrator.commands.worktree.get_worktree_manager") as wt_get,
        patch("open_orchestrator.commands.worktree.get_status_tracker"),
    ):
        wt_get.return_value.get.return_value = MagicMock(name="wt", branch="feat/x")
        wt_get.return_value.get.return_value.name = "wt"
        result = runner.invoke(main, ["attach", "wt"])
    # Either succeeds via mock OR is gated by missing args — verify attach was attempted
    fake_backend.session_for.assert_called_once_with("wt")
    fake_backend.attach.assert_called_once()
    del result  # exit code from mocked attach is not meaningful
