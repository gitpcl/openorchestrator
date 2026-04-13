"""Tests for headless agent launch via AgentLauncher."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest
from open_orchestrator.core.tool_registry import ClaudeTool, DroidTool, OpenCodeTool


class _FakeTool:
    """Minimal AIToolProtocol stand-in for unit tests."""

    def __init__(
        self,
        name: str = "claude",
        command: str = "claude --dangerously-skip-permissions -p",
        supports_headless: bool = True,
    ) -> None:
        self.name = name
        self.binary = name
        self.supports_hooks = True
        self.supports_headless = supports_headless
        self.supports_plan_mode = True
        self.install_hint = ""
        self._command = command
        self._last_kwargs: dict = {}

    def get_command(self, *, executable_path=None, plan_mode=False, prompt=None) -> str:
        self._last_kwargs = {"executable_path": executable_path, "plan_mode": plan_mode, "prompt": prompt}
        return self._command

    def is_installed(self) -> bool:
        return True

    def get_known_paths(self) -> list[Path]:
        return [Path("/usr/bin/claude")]

    def install_hooks(self, worktree_path, worktree_name, db_path=None) -> bool:
        return True


def _make_launcher(tmp_path: Path, tool: _FakeTool) -> AgentLauncher:
    """Build an AgentLauncher with mocked dependencies for headless tests."""
    wt = SimpleNamespace(name="wt", path=tmp_path, branch="feat/wt")

    wt_manager = MagicMock()
    wt_manager.list_all.return_value = []
    wt_manager.create.return_value = wt
    wt_manager.git_root = tmp_path

    tmux = MagicMock()  # headless never calls tmux
    tracker = MagicMock()
    tracker.storage_path = str(tmp_path / "status.db")

    launcher = AgentLauncher(
        repo_path=str(tmp_path),
        wt_manager=wt_manager,
        tmux=tmux,
        status_tracker=tracker,
        config=SimpleNamespace(environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False)),
    )
    # Patch registry lookup for the duration of the test
    launcher._fake_tool = tool  # type: ignore[attr-defined]
    launcher._tracker = tracker  # type: ignore[attr-defined]
    launcher._wt = wt  # type: ignore[attr-defined]
    return launcher


class TestHeadlessLaunch:
    @patch("open_orchestrator.core.agent_launcher.subprocess.Popen")
    @patch("open_orchestrator.core.agent_launcher.shutil.which", return_value="/usr/bin/claude")
    def test_launches_subprocess_with_env(
        self,
        mock_which: MagicMock,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        tool = _FakeTool(name="claude")
        launcher = _make_launcher(tmp_path, tool)

        with patch("open_orchestrator.core.agent_launcher.get_registry") as mock_registry, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ), patch("open_orchestrator.core.agent_launcher.install_hooks", return_value=True, create=True):
            mock_registry.return_value.get.return_value = tool
            result = launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.HEADLESS,
                    prompt="Run security audit",
                )
            )

        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["cwd"] == str(tmp_path)
        assert call_kwargs["start_new_session"] is True
        assert call_kwargs["env"]["OWT_AUTOMATED"] == "1"

        mock_proc.stdin.write.assert_called_once_with(b"Run security audit")
        mock_proc.stdin.close.assert_called_once()
        assert result.subprocess_pid == 12345
        assert result.tmux_session is None

    def test_headless_rejects_unsupported_tool(self, tmp_path: Path) -> None:
        tool = _FakeTool(name="opencode", supports_headless=False)
        launcher = _make_launcher(tmp_path, tool)

        with patch("open_orchestrator.core.agent_launcher.get_registry") as mock_registry:
            mock_registry.return_value.get.return_value = tool
            import pytest as _pytest

            with _pytest.raises(Exception) as exc:
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch="main",
                        ai_tool="opencode",
                        mode=LaunchMode.HEADLESS,
                        prompt="Do thing",
                    )
                )
            assert "headless" in str(exc.value).lower()

    def test_headless_requires_prompt(self, tmp_path: Path) -> None:
        tool = _FakeTool(name="claude")
        launcher = _make_launcher(tmp_path, tool)

        with patch("open_orchestrator.core.agent_launcher.get_registry") as mock_registry:
            mock_registry.return_value.get.return_value = tool
            import pytest as _pytest

            with _pytest.raises(Exception) as exc:
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch="main",
                        ai_tool="claude",
                        mode=LaunchMode.HEADLESS,
                        prompt=None,
                    )
                )
            assert "prompt" in str(exc.value).lower()

    @patch("open_orchestrator.core.agent_launcher.subprocess.Popen")
    @patch("open_orchestrator.core.agent_launcher.shutil.which", return_value="/usr/bin/claude")
    def test_forwards_plan_mode(
        self,
        mock_which: MagicMock,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_popen.return_value = MagicMock(pid=1)

        tool = _FakeTool(name="claude", command="claude --permission-mode plan -p")
        launcher = _make_launcher(tmp_path, tool)

        with patch("open_orchestrator.core.agent_launcher.get_registry") as mock_registry, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ):
            mock_registry.return_value.get.return_value = tool
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.HEADLESS,
                    prompt="Plan this",
                    plan_mode=True,
                )
            )

        assert tool._last_kwargs["plan_mode"] is True
        assert tool._last_kwargs["prompt"] == "Plan this"
        assert tool._last_kwargs["executable_path"] == "/usr/bin/claude"


class TestHeadlessProviderValidation:
    """Verify built-in tools report the correct headless capability."""

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
    @patch("open_orchestrator.core.agent_launcher.AgentLauncher.launch")
    @patch("open_orchestrator.commands.worktree._resolve_ai_tool", return_value="claude")
    @patch("open_orchestrator.config.get_builtin_templates")
    def test_template_instructions_prepended_to_prompt(
        self,
        mock_templates: MagicMock,
        mock_resolve: MagicMock,
        mock_launch: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        tmpl = MagicMock()
        tmpl.ai_instructions = "Follow TDD strictly."
        tmpl.ai_tool = None
        tmpl.plan_mode = False
        tmpl.base_branch = None
        mock_templates.return_value = {"tdd": tmpl}

        mock_launch.return_value = SimpleNamespace(
            worktree_name="my-feat",
            worktree_path="/tmp/wt",
            branch="feat/my-feat",
            ai_tool="claude",
            tmux_session=None,
            subprocess_pid=99,
            warnings=[],
        )

        result = cli_runner.invoke(main, ["new", "add feature", "--headless", "-t", "tdd", "-y"])
        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once()
        request: LaunchRequest = mock_launch.call_args[0][0]
        assert request.prompt == "Follow TDD strictly.\n\nadd feature"
        assert request.mode == LaunchMode.HEADLESS
