"""Tests for AgentLauncher — unified worktree + agent provisioning."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest
from open_orchestrator.core.pane_actions import PaneActionError


class _FakeTool:
    """AIToolProtocol stand-in for unit tests."""

    def __init__(
        self,
        name: str = "claude",
        supports_headless: bool = True,
        command: str = "claude --dangerously-skip-permissions -p",
    ) -> None:
        self.name = name
        self.binary = name
        self.supports_hooks = True
        self.supports_headless = supports_headless
        self.supports_plan_mode = True
        self.install_hint = ""
        self._command = command

    def get_command(self, *, executable_path=None, plan_mode=False, prompt=None) -> str:
        return self._command

    def is_installed(self) -> bool:
        return True

    def get_known_paths(self) -> list[Path]:
        return [Path("/usr/bin/claude")]

    def install_hooks(self, worktree_path, worktree_name, db_path=None) -> bool:
        return True


def _make_launcher(tmp_path: Path) -> tuple[AgentLauncher, MagicMock, MagicMock, SimpleNamespace]:
    """Build a launcher with mocks; returns (launcher, wt_manager, tmux, worktree)."""
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
        config=SimpleNamespace(
            environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False),
            recall_enabled=False,
        ),
    )
    return launcher, wt_manager, tmux, worktree


class TestLaunchModeValidation:
    def test_headless_without_prompt_raises(self, tmp_path: Path) -> None:
        launcher, _, _, _ = _make_launcher(tmp_path)
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg:
            reg.return_value.get.return_value = _FakeTool()
            with pytest.raises(PaneActionError, match="prompt"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch=None,
                        ai_tool="claude",
                        mode=LaunchMode.HEADLESS,
                        prompt=None,
                    )
                )

    def test_automated_without_prompt_raises(self, tmp_path: Path) -> None:
        launcher, _, _, _ = _make_launcher(tmp_path)
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg:
            reg.return_value.get.return_value = _FakeTool()
            with pytest.raises(PaneActionError, match="prompt"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch=None,
                        ai_tool="claude",
                        mode=LaunchMode.AUTOMATED,
                        prompt=None,
                    )
                )

    def test_unknown_tool_raises(self, tmp_path: Path) -> None:
        launcher, _, _, _ = _make_launcher(tmp_path)
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg:
            reg.return_value.get.return_value = None
            reg.return_value.list_names.return_value = ["claude", "droid"]
            with pytest.raises(PaneActionError, match="Unknown AI tool"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch=None,
                        ai_tool="bogus",
                        mode=LaunchMode.INTERACTIVE,
                    )
                )

    def test_headless_on_unsupported_tool_raises(self, tmp_path: Path) -> None:
        launcher, _, _, _ = _make_launcher(tmp_path)
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg:
            reg.return_value.get.return_value = _FakeTool(name="opencode", supports_headless=False)
            with pytest.raises(PaneActionError, match="Headless mode is not supported"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch=None,
                        ai_tool="opencode",
                        mode=LaunchMode.HEADLESS,
                        prompt="do thing",
                    )
                )

    def test_duplicate_worktree_raises(self, tmp_path: Path) -> None:
        launcher, wt_manager, _, _ = _make_launcher(tmp_path)
        wt_manager.list_all.return_value = [SimpleNamespace(name="wt")]
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ):
            reg.return_value.get.return_value = _FakeTool()
            with pytest.raises(PaneActionError, match="already exists"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch=None,
                        ai_tool="claude",
                        mode=LaunchMode.INTERACTIVE,
                    )
                )


class TestInteractiveLaunch:
    def test_interactive_without_prompt_no_paste(self, tmp_path: Path) -> None:
        launcher, _, tmux, _ = _make_launcher(tmp_path)
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ), patch("open_orchestrator.core.agent_launcher._init_pane_tracking"):
            reg.return_value.get.return_value = _FakeTool()
            result = launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.INTERACTIVE,
                )
            )
        assert result.tmux_session == "owt-wt"
        assert result.subprocess_pid is None
        tmux.wait_for_ai_ready.assert_not_called()
        tmux.paste_to_pane.assert_not_called()

    def test_interactive_with_prompt_delivers_via_paste(self, tmp_path: Path) -> None:
        launcher, _, tmux, _ = _make_launcher(tmp_path)
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ), patch("open_orchestrator.core.agent_launcher._init_pane_tracking"):
            reg.return_value.get.return_value = _FakeTool()
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.INTERACTIVE,
                    prompt="Implement JWT",
                )
            )
        tmux.wait_for_ai_ready.assert_called_once_with(session_name="owt-wt", timeout=15)
        tmux.paste_to_pane.assert_called_once_with(session_name="owt-wt", text="Implement JWT")

    def test_long_prompt_delivered_intact(self, tmp_path: Path) -> None:
        """Regression guard: prompts >2K chars must reach paste_to_pane intact."""
        launcher, _, tmux, _ = _make_launcher(tmp_path)
        long_prompt = "x" * 3000
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ), patch("open_orchestrator.core.agent_launcher._init_pane_tracking"):
            reg.return_value.get.return_value = _FakeTool()
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.AUTOMATED,
                    prompt=long_prompt,
                )
            )
        call_kwargs = tmux.paste_to_pane.call_args.kwargs
        assert call_kwargs["text"] == long_prompt
        assert len(call_kwargs["text"]) == 3000


class TestAutomatedLaunch:
    def test_automated_sets_automated_flag(self, tmp_path: Path) -> None:
        launcher, _, tmux, _ = _make_launcher(tmp_path)
        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ), patch("open_orchestrator.core.agent_launcher._init_pane_tracking"):
            reg.return_value.get.return_value = _FakeTool()
            launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.AUTOMATED,
                    prompt="automated task",
                )
            )
        call_kwargs = tmux.create_worktree_session.call_args.kwargs
        assert call_kwargs["automated"] is True

    def test_automated_rejects_stale_session_reuse(self, tmp_path: Path) -> None:
        """AUTOMATED must fail fast if the tmux session already exists.

        Silent reuse would bind batch/orchestrator to whatever the previous
        run left behind, violating the fresh-prompt contract.
        """
        from open_orchestrator.core.tmux_manager import TmuxSessionExistsError

        launcher, _, tmux, _ = _make_launcher(tmp_path)
        tmux.create_worktree_session.side_effect = TmuxSessionExistsError("already exists")

        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ):
            reg.return_value.get.return_value = _FakeTool()
            with pytest.raises(PaneActionError, match="refusing to reuse"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch="main",
                        ai_tool="claude",
                        mode=LaunchMode.AUTOMATED,
                        prompt="auto task",
                    )
                )

    def test_interactive_reuses_stale_session(self, tmp_path: Path) -> None:
        """INTERACTIVE may reuse an existing session so attach keeps working."""
        from open_orchestrator.core.tmux_manager import TmuxSessionExistsError

        launcher, _, tmux, _ = _make_launcher(tmp_path)
        tmux.create_worktree_session.side_effect = TmuxSessionExistsError("already exists")
        tmux.generate_session_name.return_value = "owt-wt"

        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ), patch("open_orchestrator.core.agent_launcher._init_pane_tracking"):
            reg.return_value.get.return_value = _FakeTool()
            result = launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.INTERACTIVE,
                )
            )
        assert result.tmux_session == "owt-wt"


class TestHeadlessLaunch:
    @patch("open_orchestrator.core.agent_launcher.subprocess.Popen")
    @patch("open_orchestrator.core.agent_launcher.shutil.which", return_value="/usr/bin/claude")
    def test_headless_launches_subprocess(
        self,
        mock_which: MagicMock,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_popen.return_value = MagicMock(pid=42)
        launcher, _, tmux, _ = _make_launcher(tmp_path)

        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ):
            reg.return_value.get.return_value = _FakeTool()
            result = launcher.launch(
                LaunchRequest(
                    branch="feat/wt",
                    base_branch="main",
                    ai_tool="claude",
                    mode=LaunchMode.HEADLESS,
                    prompt="audit code",
                )
            )

        tmux.create_worktree_session.assert_not_called()
        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["env"]["OWT_AUTOMATED"] == "1"
        assert result.subprocess_pid == 42
        assert result.tmux_session is None


class TestRollback:
    def test_tmux_failure_triggers_rollback(self, tmp_path: Path) -> None:
        from open_orchestrator.core.tmux_manager import TmuxError

        launcher, _, tmux, _ = _make_launcher(tmp_path)
        tmux.create_worktree_session.side_effect = TmuxError("tmux not found")

        with patch("open_orchestrator.core.agent_launcher.get_registry") as reg, patch(
            "open_orchestrator.core.agent_launcher._setup_pane_environment"
        ), patch("open_orchestrator.core.agent_launcher.PaneTransaction") as mock_txn_cls:
            reg.return_value.get.return_value = _FakeTool()
            mock_txn = mock_txn_cls.return_value
            with pytest.raises(PaneActionError, match="Failed to create tmux session"):
                launcher.launch(
                    LaunchRequest(
                        branch="feat/wt",
                        base_branch="main",
                        ai_tool="claude",
                        mode=LaunchMode.INTERACTIVE,
                    )
                )
            mock_txn.rollback.assert_called_once()
