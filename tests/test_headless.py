"""Tests for headless agent launch in worktree commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main
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

    @patch("open_orchestrator.commands.worktree.subprocess.Popen")
    @patch.object(AITool, "get_command", return_value="claude --permission-mode plan -p")
    @patch.object(AITool, "get_executable_path", return_value="/usr/bin/claude")
    def test_forwards_plan_mode(
        self,
        mock_exec: MagicMock,
        mock_cmd: MagicMock,
        mock_popen: MagicMock,
        tmp_path: object,
    ) -> None:
        mock_popen.return_value = MagicMock(pid=1)
        tracker = MagicMock()

        _launch_headless_agent(
            worktree_path=str(tmp_path),
            ai_tool_enum=AITool.CLAUDE,
            task_description="review code",
            tracker=tracker,
            worktree_name="review",
            plan_mode=True,
        )

        mock_cmd.assert_called_once_with(
            AITool.CLAUDE,
            executable_path="/usr/bin/claude",
            prompt="review code",
            plan_mode=True,
        )


class TestHeadlessProviderValidation:
    def test_only_claude_supports_headless(self) -> None:
        assert AITool.supports_headless(AITool.CLAUDE) is True
        assert AITool.supports_headless(AITool.DROID) is False
        assert AITool.supports_headless(AITool.OPENCODE) is False

    @patch("open_orchestrator.commands.worktree._resolve_ai_tool", return_value="droid")
    def test_rejects_droid_headless(self, mock_resolve: MagicMock, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["new", "test task", "--headless", "--ai-tool", "droid", "-y"])
        assert result.exit_code != 0
        assert "headless mode requires claude" in result.output.lower()

    @patch("open_orchestrator.commands.worktree._resolve_ai_tool", return_value="opencode")
    def test_rejects_opencode_headless(self, mock_resolve: MagicMock, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["new", "test task", "--headless", "--ai-tool", "opencode", "-y"])
        assert result.exit_code != 0
        assert "headless mode requires claude" in result.output.lower()


class TestTemplatePrepend:
    @patch("open_orchestrator.commands.worktree._launch_headless_agent")
    @patch("open_orchestrator.commands.worktree._create_tmux_session", return_value=(MagicMock(), None))
    @patch("open_orchestrator.commands.worktree._setup_environment_and_hooks")
    @patch("open_orchestrator.commands.worktree.get_worktree_manager")
    @patch("open_orchestrator.commands.worktree._resolve_ai_tool", return_value="claude")
    @patch("open_orchestrator.config.get_builtin_templates")
    def test_template_instructions_prepended_to_prompt(
        self,
        mock_templates: MagicMock,
        mock_resolve: MagicMock,
        mock_wt_mgr: MagicMock,
        mock_env: MagicMock,
        mock_tmux: MagicMock,
        mock_launch: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        tmpl = MagicMock()
        tmpl.ai_instructions = "Follow TDD strictly."
        tmpl.ai_tool = None
        tmpl.plan_mode = False
        tmpl.base_branch = None
        mock_templates.return_value = {"tdd": tmpl}

        wt = MagicMock()
        wt.name = "my-feat"
        wt.path = "/tmp/wt"
        wt.branch = "feat/my-feat"
        mock_wt_mgr.return_value.create.return_value = wt

        result = cli_runner.invoke(main, ["new", "add feature", "--headless", "-t", "tdd", "-y"])
        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once()
        prompt_arg = mock_launch.call_args[0][2]
        assert prompt_arg == "Follow TDD strictly.\n\nadd feature"
