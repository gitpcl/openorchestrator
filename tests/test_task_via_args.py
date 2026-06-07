"""Tests for the task-via-args tool capability and the ClawCore provider.

REPL/TUI agents (Claude, Pi, …) boot a session and receive the task by
paste/stdin. One-shot agents like ClawCore take the task as positional
argv (``clawcore run "<task>" "<worktree>" --json``). These tests cover the
general ``task_via_args`` mechanism plus the ClawCore registration that
rides on it.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest
from open_orchestrator.core.tool_registry import (
    CustomTool,
    ToolRegistry,
    get_registry,
    register_custom_tools,
)

# ── tool model: get_command substitution ───────────────────────────────


class TestTaskViaArgsCommand:
    def test_substitutes_task_and_worktree_quoted(self) -> None:
        tool = CustomTool(
            name="x",
            binary="x",
            command_template="{binary} run {{task}} {{worktree}} --json",
            task_via_args=True,
        )
        cmd = tool.get_command(prompt="fix the failing test", worktree="/tmp/wt path")
        # Each substitution is shell-quoted and round-trips through shlex.
        assert shlex.split(cmd) == ["x", "run", "fix the failing test", "/tmp/wt path", "--json"]

    def test_executable_path_used_when_resolved(self) -> None:
        tool = CustomTool(
            name="x",
            binary="x",
            command_template="{binary} run {{task}} {{worktree}} --json",
            task_via_args=True,
        )
        cmd = tool.get_command(executable_path="/opt/bin/x", prompt="t", worktree="/w")
        assert shlex.split(cmd)[0] == "/opt/bin/x"

    def test_task_with_shell_metacharacters_is_safe(self) -> None:
        tool = CustomTool(
            name="x",
            binary="x",
            command_template="{binary} run {{task}} {{worktree}} --json",
            task_via_args=True,
        )
        evil = "$(rm -rf /); `whoami`; a'b\"c"
        cmd = tool.get_command(prompt=evil, worktree="/w")
        # The whole task survives as ONE argv element — no shell expansion.
        assert shlex.split(cmd) == ["x", "run", evil, "/w", "--json"]

    def test_missing_worktree_defaults_to_dot(self) -> None:
        tool = CustomTool(name="x", binary="x", command_template="{binary} run {{task}} {{worktree}}", task_via_args=True)
        assert shlex.split(tool.get_command(prompt="t")) == ["x", "run", "t", "."]


class TestReplToolUnaffected:
    def test_repl_tool_ignores_worktree_and_keeps_paste_contract(self) -> None:
        claude = get_registry().get("claude")
        assert claude is not None
        assert claude.task_via_args is False
        # worktree is accepted but ignored; no task in the command line.
        cmd = claude.get_command(prompt="do thing", worktree="/some/wt")
        assert "do thing" not in cmd
        assert "/some/wt" not in cmd


# ── ClawCore registration ───────────────────────────────────────────────


class TestClawcoreRegistration:
    def test_clawcore_is_registered_first_class(self) -> None:
        tool = get_registry().get("clawcore")
        assert tool is not None
        assert tool.name == "clawcore"
        assert tool.binary == "clawcore"
        assert tool.task_via_args is True
        assert tool.supports_headless is True
        assert tool.supports_plan_mode is False
        assert tool.supports_hooks is False

    def test_clawcore_builds_run_command(self) -> None:
        tool = get_registry().get("clawcore")
        assert tool is not None
        cmd = tool.get_command(prompt="fix the failing test", worktree="/repo/wt")
        assert shlex.split(cmd) == ["clawcore", "run", "fix the failing test", "/repo/wt", "--json"]

    def test_clawcore_known_paths_and_install_hint(self) -> None:
        tool = get_registry().get("clawcore")
        assert tool is not None
        paths = {str(p) for p in tool.get_known_paths()}
        assert any(p.endswith("/go/bin/clawcore") for p in paths)
        assert "/usr/local/bin/clawcore" in paths
        assert "/opt/homebrew/bin/clawcore" in paths
        assert "install.sh" in tool.install_hint


class TestCustomTaskViaArgsFromConfig:
    def test_config_can_declare_task_via_args(self) -> None:
        registry = ToolRegistry()
        register_custom_tools(
            registry,
            {
                "myagent": {
                    "binary": "myagent",
                    "command_template": "{binary} go {{task}} {{worktree}}",
                    "task_via_args": True,
                    "supports_headless": True,
                }
            },
        )
        tool = registry.get("myagent")
        assert tool is not None
        assert tool.task_via_args is True
        assert shlex.split(tool.get_command(prompt="t", worktree="/w")) == ["myagent", "go", "t", "/w"]


# ── launcher: interactive path ──────────────────────────────────────────


class _FakeTool:
    """AIToolProtocol stand-in with a configurable task_via_args flag."""

    def __init__(self, *, name: str = "claude", task_via_args: bool = False) -> None:
        self.name = name
        self.binary = name
        self.supports_hooks = False
        self.supports_headless = True
        self.supports_plan_mode = False
        self.task_via_args = task_via_args
        self.install_hint = ""

    def get_command(self, *, executable_path=None, plan_mode=False, prompt=None, worktree=None) -> str:
        binary = executable_path or self.binary
        if self.task_via_args:
            return f"{binary} run {shlex.quote(prompt or '')} {shlex.quote(worktree or '.')} --json"
        return f"{binary} -p"

    def is_installed(self) -> bool:
        return True

    def get_known_paths(self) -> list[Path]:
        return [Path("/usr/bin") / self.binary]

    def install_hooks(self, worktree_path, worktree_name, db_path=None) -> bool:
        return False


def _make_launcher(tmp_path: Path) -> tuple[AgentLauncher, MagicMock]:
    worktree = SimpleNamespace(name="wt", path=tmp_path, branch="feat/wt")
    wt_manager = MagicMock()
    wt_manager.list_all.return_value = []
    wt_manager.create.return_value = worktree
    wt_manager.git_root = tmp_path
    tmux = MagicMock()
    tmux.create_worktree_session.return_value = SimpleNamespace(session_name="owt-wt")
    tracker = MagicMock()
    tracker.storage_path = str(tmp_path / "status.db")
    launcher = AgentLauncher(
        repo_path=str(tmp_path),
        wt_manager=wt_manager,
        tmux=tmux,
        status_tracker=tracker,
        config=SimpleNamespace(environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False)),
    )
    return launcher, tmux


class TestInteractiveTaskViaArgs:
    def test_task_via_args_threads_task_and_skips_paste(self, tmp_path: Path) -> None:
        launcher, tmux = _make_launcher(tmp_path)
        with (
            patch("open_orchestrator.core.agent_launcher.get_registry") as reg,
            patch("open_orchestrator.core.agent_launcher._setup_pane_environment"),
            patch("open_orchestrator.core.agent_launcher._init_pane_tracking"),
        ):
            reg.return_value.get.return_value = _FakeTool(name="clawcore", task_via_args=True)
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="clawcore",
                    mode=LaunchMode.INTERACTIVE,
                    prompt="fix the failing test",
                )
            )
        # Task is threaded into session creation (becomes argv downstream)…
        assert tmux.create_worktree_session.call_args.kwargs["prompt"] == "fix the failing test"
        # …and the prompt is NOT pasted into a TUI.
        tmux.wait_for_ai_ready.assert_not_called()
        tmux.paste_to_pane.assert_not_called()

    def test_herdr_backend_rejects_task_via_args(self, tmp_path: Path) -> None:
        """One-shot tools aren't wired for herdr — fail loudly rather than
        launching the agent with no task."""
        from open_orchestrator.core.pane_actions import PaneActionError
        from open_orchestrator.models.backend import BackendKind

        launcher, _ = _make_launcher(tmp_path)
        herdr_backend = MagicMock()
        herdr_backend.kind = BackendKind.HERDR
        with (
            patch("open_orchestrator.core.agent_launcher.get_registry") as reg,
            patch("open_orchestrator.core.agent_launcher._setup_pane_environment"),
            patch.object(launcher, "_resolve_backend", return_value=herdr_backend),
        ):
            reg.return_value.get.return_value = _FakeTool(name="clawcore", task_via_args=True)
            with pytest.raises(PaneActionError, match="herdr backend does not support"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch="main",
                        ai_tool="clawcore",
                        mode=LaunchMode.INTERACTIVE,
                        prompt="fix the failing test",
                    )
                )
        # The agent session was never created.
        herdr_backend.create_session.assert_not_called()

    def test_repl_tool_still_pastes_and_does_not_thread_task(self, tmp_path: Path) -> None:
        """Regression guard: the REPL paste path is unchanged."""
        launcher, tmux = _make_launcher(tmp_path)
        with (
            patch("open_orchestrator.core.agent_launcher.get_registry") as reg,
            patch("open_orchestrator.core.agent_launcher._setup_pane_environment"),
            patch("open_orchestrator.core.agent_launcher._init_pane_tracking"),
        ):
            reg.return_value.get.return_value = _FakeTool(name="claude", task_via_args=False)
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.INTERACTIVE,
                    prompt="Implement JWT",
                )
            )
        # REPL tools do not receive the task as argv…
        assert tmux.create_worktree_session.call_args.kwargs["prompt"] is None
        # …they get it pasted after boot.
        tmux.paste_to_pane.assert_called_once_with(session_name="owt-wt", text="Implement JWT")


# ── launcher: headless path ─────────────────────────────────────────────


class TestHeadlessTaskViaArgs:
    @patch("open_orchestrator.core.agent_launcher.subprocess.Popen")
    @patch("open_orchestrator.core.agent_launcher.try_resolve_binary", return_value="clawcore")
    def test_task_via_args_uses_argv_no_stdin(self, mock_which: MagicMock, mock_popen: MagicMock, tmp_path: Path) -> None:
        mock_popen.return_value = MagicMock(pid=7)
        launcher, tmux = _make_launcher(tmp_path)
        with (
            patch("open_orchestrator.core.agent_launcher.get_registry") as reg,
            patch("open_orchestrator.core.agent_launcher._setup_pane_environment"),
        ):
            reg.return_value.get.return_value = _FakeTool(name="clawcore", task_via_args=True)
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="clawcore",
                    mode=LaunchMode.HEADLESS,
                    prompt="audit code",
                )
            )
        argv = mock_popen.call_args.args[0]
        assert argv[:3] == ["clawcore", "run", "audit code"]
        assert argv[-1] == "--json"
        # Worktree path is the substituted positional.
        assert argv[3] == str(tmp_path)
        # No prompt is piped over stdin for task_via_args tools.
        assert mock_popen.call_args.kwargs["stdin"] is subprocess.DEVNULL
        mock_popen.return_value.stdin.write.assert_not_called()

    @patch("open_orchestrator.core.agent_launcher.subprocess.Popen")
    @patch("open_orchestrator.core.agent_launcher.try_resolve_binary", return_value="claude")
    def test_repl_tool_still_writes_prompt_to_stdin(self, mock_which: MagicMock, mock_popen: MagicMock, tmp_path: Path) -> None:
        """Regression guard: the REPL stdin path is unchanged."""
        proc = MagicMock(pid=9)
        mock_popen.return_value = proc
        launcher, _ = _make_launcher(tmp_path)
        with (
            patch("open_orchestrator.core.agent_launcher.get_registry") as reg,
            patch("open_orchestrator.core.agent_launcher._setup_pane_environment"),
        ):
            reg.return_value.get.return_value = _FakeTool(name="claude", task_via_args=False)
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.HEADLESS,
                    prompt="audit code",
                )
            )
        assert mock_popen.call_args.kwargs["stdin"] is subprocess.PIPE
        proc.stdin.write.assert_called_once_with(b"audit code")
