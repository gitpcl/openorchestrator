"""Tests for headless agent launch in worktree commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.commands.worktree import _launch_headless_agent
from open_orchestrator.core.tool_registry import ClaudeTool, DroidTool, OpenCodeTool


class _FakeTool:
    """Minimal AIToolProtocol stand-in used in unit tests."""

    def __init__(
        self,
        name: str,
        command: str = "claude --dangerously-skip-permissions -p",
        executable: str | None = "/usr/bin/claude",
        supports_headless: bool = True,
    ) -> None:
        self.name = name
        self.binary = name
        self.supports_hooks = True
        self.supports_headless = supports_headless
        self.supports_plan_mode = True
        self.install_hint = ""
        self._command = command
        self._executable = executable
        self._last_kwargs: dict = {}

    def get_command(self, *, executable_path=None, plan_mode=False, prompt=None) -> str:  # noqa: D401
        self._last_kwargs = {
            "executable_path": executable_path,
            "plan_mode": plan_mode,
            "prompt": prompt,
        }
        return self._command

    def is_installed(self) -> bool:
        return True

    def get_known_paths(self) -> list[Path]:
        return [Path("/usr/bin/claude")]

    def install_hooks(self, worktree_path, worktree_name, db_path=None) -> bool:
        return True


class TestLaunchHeadlessAgent:
    @patch("open_orchestrator.commands.worktree.subprocess.Popen")
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_launches_subprocess(
        self,
        mock_which: MagicMock,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        tracker = MagicMock()
        tool = _FakeTool(name="claude")

        _launch_headless_agent(
            worktree_path=str(tmp_path),
            tool=tool,
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
            tool=_FakeTool(name="claude"),
            task_description="",
            tracker=tracker,
            worktree_name="empty",
        )

        mock_popen.assert_not_called()
        tracker.update_task.assert_not_called()

    @patch("open_orchestrator.commands.worktree.subprocess.Popen")
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_forwards_plan_mode(
        self,
        mock_which: MagicMock,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_popen.return_value = MagicMock(pid=1)
        tracker = MagicMock()
        tool = _FakeTool(name="claude", command="claude --permission-mode plan -p")

        _launch_headless_agent(
            worktree_path=str(tmp_path),
            tool=tool,
            task_description="review code",
            tracker=tracker,
            worktree_name="review",
            plan_mode=True,
        )

        assert tool._last_kwargs["plan_mode"] is True
        assert tool._last_kwargs["prompt"] == "review code"
        assert tool._last_kwargs["executable_path"] == "/usr/bin/claude"


class TestHeadlessProviderValidation:
    def test_only_claude_supports_headless(self) -> None:
        assert ClaudeTool().supports_headless is True
        assert DroidTool().supports_headless is False
        assert OpenCodeTool().supports_headless is False

    @patch("open_orchestrator.commands.worktree._resolve_ai_tool", return_value="droid")
    def test_rejects_droid_headless(self, mock_resolve: MagicMock, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["new", "test task", "--headless", "--ai-tool", "droid", "-y"])
        assert result.exit_code != 0
        assert "headless mode is not supported" in result.output.lower()

    @patch("open_orchestrator.commands.worktree._resolve_ai_tool", return_value="opencode")
    def test_rejects_opencode_headless(self, mock_resolve: MagicMock, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["new", "test task", "--headless", "--ai-tool", "opencode", "-y"])
        assert result.exit_code != 0
        assert "headless mode is not supported" in result.output.lower()


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
