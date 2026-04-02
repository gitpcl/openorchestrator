"""Tests for pane creation behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from open_orchestrator.config import AITool
from open_orchestrator.core.pane_actions import create_pane
from open_orchestrator.models.status import AIActivityStatus


@patch("open_orchestrator.core.hooks.install_hooks")
@patch("open_orchestrator.core.pane_actions.TmuxManager")
@patch("open_orchestrator.core.pane_actions.ProjectDetector")
@patch("open_orchestrator.core.pane_actions.WorktreeManager")
@patch("open_orchestrator.core.pane_actions.load_config")
def test_create_pane_uses_live_session_and_display_task(
    mock_load_config: MagicMock,
    mock_wt_manager_cls: MagicMock,
    mock_project_detector_cls: MagicMock,
    mock_tmux_cls: MagicMock,
    _mock_install_hooks: MagicMock,
) -> None:
    mock_load_config.return_value = SimpleNamespace(
        environment=SimpleNamespace(
            auto_install_deps=False,
            copy_env_file=False,
        )
    )
    mock_project_detector_cls.return_value.detect.return_value = None

    worktree = SimpleNamespace(
        name="auth-jwt",
        path=Path("/tmp/auth-jwt"),
        branch="feat/auth-jwt",
    )
    wt_manager = mock_wt_manager_cls.return_value
    wt_manager.list_all.return_value = []
    wt_manager.create.return_value = worktree

    tmux_manager = mock_tmux_cls.return_value
    tmux_manager.create_worktree_session.return_value = SimpleNamespace(
        session_name="owt-auth-jwt"
    )

    tracker = MagicMock()

    create_pane(
        session_name="orch-auth",
        repo_path="/tmp/repo",
        branch="feat/auth-jwt",
        ai_tool=AITool.CLAUDE,
        ai_instructions="system prompt goes here",
        display_task="Implement JWT auth",
        status_tracker=tracker,
    )

    # Session created in interactive mode (NO prompt= parameter)
    tmux_manager.create_worktree_session.assert_called_once_with(
        worktree_name="auth-jwt",
        worktree_path="/tmp/auth-jwt",
        ai_tool=AITool.CLAUDE,
        plan_mode=False,
        automated=True,
    )
    # Prompt delivered via wait_for_ai_ready + paste_to_pane (handles long prompts)
    tmux_manager.wait_for_ai_ready.assert_called_once_with(
        session_name="owt-auth-jwt",
        timeout=15,
    )
    tmux_manager.paste_to_pane.assert_called_once_with(
        session_name="owt-auth-jwt",
        text="system prompt goes here",
    )
    tracker.update_task.assert_called_once_with(
        "auth-jwt",
        "Implement JWT auth",
        AIActivityStatus.WORKING,
    )
