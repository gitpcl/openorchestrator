"""Tests for headless agent launch in worktree commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from open_orchestrator.commands.worktree import _launch_headless_agent
from open_orchestrator.config import AITool


class TestLaunchHeadlessAgent:
    @patch("open_orchestrator.commands.worktree.subprocess.Popen")
    @patch.object(AITool, "get_command", return_value="claude --dangerously-skip-permissions -p")
    @patch.object(AITool, "get_executable_path", return_value="/usr/bin/claude")
    def test_launches_subprocess(
        self,
        mock_exec: MagicMock,
        mock_cmd: MagicMock,
        mock_popen: MagicMock,
        tmp_path: object,
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        tracker = MagicMock()

        _launch_headless_agent(
            worktree_path=str(tmp_path),
            ai_tool_enum=AITool.CLAUDE,
            task_description="Run security audit",
            tracker=tracker,
            worktree_name="sec-audit",
        )

        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["cwd"] == str(tmp_path)
        assert call_kwargs["start_new_session"] is True
        assert call_kwargs["env"]["OWT_AUTOMATED"] == "1"

        mock_proc.stdin.write.assert_called_once_with(b"Run security audit")
        mock_proc.stdin.close.assert_called_once()
        tracker.update_task.assert_called_once_with("sec-audit", "Run security audit")

    @patch("open_orchestrator.commands.worktree.subprocess.Popen")
    def test_skips_launch_without_task(self, mock_popen: MagicMock) -> None:
        tracker = MagicMock()

        _launch_headless_agent(
            worktree_path="/tmp/wt",
            ai_tool_enum=AITool.CLAUDE,
            task_description="",
            tracker=tracker,
            worktree_name="empty",
        )

        mock_popen.assert_not_called()
        tracker.update_task.assert_not_called()
