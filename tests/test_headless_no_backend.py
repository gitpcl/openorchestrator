"""Sprint 026 P6: ``owt new --headless`` skips backend resolution entirely.

When ``[backend] mode = "herdr"`` is configured but herdr isn't installed,
the headless launch path must still work — it never touches a multiplexer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


def test_headless_skips_backend_resolution_when_herdr_uninstalled() -> None:
    """With [backend] mode='herdr' + herdr unreachable + --headless, command succeeds.

    The backend factory's herdr probe must never fire because the headless
    code path runs a detached subprocess with no multiplexer involvement.
    """
    runner = CliRunner()

    fake_cfg = MagicMock()
    fake_cfg.backend.mode = "herdr"

    # Track whether select_backend is called — it must NOT be.
    select_backend_calls: list = []

    def _spy_select(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        select_backend_calls.append((args, kwargs))
        raise RuntimeError("select_backend should not run on headless path")

    fake_result = MagicMock()
    fake_result.worktree_name = "headless-task"
    fake_result.worktree_path = "/tmp/wt/headless-task"
    fake_result.tmux_session = None
    fake_result.backend_session_id = None
    fake_result.subprocess_pid = 9999
    fake_result.warnings = []

    fake_launcher_instance = MagicMock()
    fake_launcher_instance.launch.return_value = fake_result

    fake_tool = MagicMock()
    fake_tool.supports_headless = True

    with (
        patch("open_orchestrator.commands.worktree.load_config_safe", return_value=fake_cfg),
        patch("open_orchestrator.core.backend_factory.select_backend", side_effect=_spy_select),
        patch("open_orchestrator.commands.worktree.AgentLauncher", return_value=fake_launcher_instance),
        patch("open_orchestrator.commands.worktree.get_worktree_manager"),
        patch("open_orchestrator.commands.worktree.get_status_tracker"),
        patch("open_orchestrator.commands.worktree.get_registry") as reg,
        patch("open_orchestrator.commands.worktree._resolve_ai_tool", return_value="claude"),
        patch("open_orchestrator.commands.worktree._check_git_ref_conflicts", side_effect=lambda x: x),
    ):
        reg.return_value.get.return_value = fake_tool
        result = runner.invoke(
            main,
            ["new", "Run my headless task", "--yes", "--headless"],
        )

    assert result.exit_code == 0, result.output
    assert select_backend_calls == [], f"Headless path must NOT call select_backend; got: {select_backend_calls}"
    # Launcher was still invoked with the request.
    fake_launcher_instance.launch.assert_called_once()
