"""CliRunner tests for ``commands/worktree``.

Mirrors the harness in :mod:`tests.test_merge_cmds` — a freshly-built
``click.Group`` with ``worktree.register`` provides exactly the surface
the CLI exposes without dragging in real git, real tmux, or the
``open_orchestrator.cli.main`` side effects (which load config and a
status tracker at import time). The tests pin option parsing, prompt
flows, conflict handling, and the branch / attach / switch / delete
paths. Heavy collaborators (``WorktreeManager``, ``AgentLauncher``,
backend factory, tool registry) are monkeypatched.

Why not extend ``test_headless.py``? That file targets the
``AgentLauncher`` internals via the real top-level CLI. The goal here
is the *command layer* — the Click glue in ``commands/worktree.py``,
including branches that never reach the launcher.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from open_orchestrator.models.backend import BackendKind, BackendSession
from open_orchestrator.models.status import AIActivityStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def main_cli() -> click.Group:
    """Build a fresh CLI group with worktree commands registered."""
    from open_orchestrator.commands import worktree as worktree_cmds

    @click.group()
    def cli() -> None:  # pragma: no cover - trivial top-level
        pass

    worktree_cmds.register(cli)
    return cli


def _make_worktree(name: str = "feat-x", branch: str = "feat/x", is_main: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        branch=branch,
        path=Path("/tmp/owt-test") / name,
        is_main=is_main,
    )


def _make_status(
    name: str = "feat-x",
    branch: str = "feat/x",
    activity: AIActivityStatus = AIActivityStatus.WORKING,
    *,
    session_type: str = "worktree",
    backend_kind: str = "tmux",
    tmux_session: str | None = "owt-feat-x",
    backend_session_id: str | None = None,
    current_task: str | None = "doing work",
) -> SimpleNamespace:
    return SimpleNamespace(
        worktree_name=name,
        branch=branch,
        activity_status=activity,
        current_task=current_task,
        tmux_session=tmux_session,
        backend_session_id=backend_session_id,
        backend_kind=backend_kind,
        session_type=session_type,
    )


def _make_wt_manager(*worktrees: SimpleNamespace, git_root: Path | None = None) -> MagicMock:
    mgr = MagicMock()
    mgr.git_root = git_root or Path("/tmp/owt-test")
    mgr.list_all.return_value = list(worktrees)

    by_name = {wt.name: wt for wt in worktrees}

    def _get(identifier: str) -> SimpleNamespace:
        from open_orchestrator.core.worktree import WorktreeNotFoundError

        if identifier in by_name:
            return by_name[identifier]
        raise WorktreeNotFoundError(f"worktree '{identifier}' not found")

    mgr.get.side_effect = _get
    return mgr


def _make_tracker(*statuses: SimpleNamespace, backend_session: BackendSession | None = None) -> MagicMock:
    tracker = MagicMock()
    by_name = {s.worktree_name: s for s in statuses}
    tracker.get_all_statuses.return_value = list(statuses)
    tracker.get_status.side_effect = lambda n: by_name.get(n)
    tracker.get_backend_session.return_value = backend_session
    return tracker


def _make_launch_result(
    *,
    worktree_name: str = "feat-x",
    worktree_path: str = "/tmp/owt-test/feat-x",
    branch: str = "feat/x",
    tmux_session: str | None = "owt-feat-x",
    subprocess_pid: int | None = None,
    warnings: list[str] | None = None,
    backend_session_id: str | None = "owt-feat-x",
    backend_kind: BackendKind = BackendKind.TMUX,
) -> SimpleNamespace:
    return SimpleNamespace(
        worktree_name=worktree_name,
        worktree_path=worktree_path,
        branch=branch,
        ai_tool="claude",
        tmux_session=tmux_session,
        subprocess_pid=subprocess_pid,
        warnings=warnings or [],
        backend_session_id=backend_session_id,
        backend_kind=backend_kind,
    )


def _make_fake_tool(*, supports_headless: bool = True, supports_plan_mode: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        name="claude",
        supports_headless=supports_headless,
        supports_plan_mode=supports_plan_mode,
    )


def _patch_resolved_backend(monkeypatch: pytest.MonkeyPatch, *, kind: BackendKind = BackendKind.TMUX) -> MagicMock:
    """Replace ``select_backend`` so resolution short-circuits with a mock."""
    backend = MagicMock()
    backend.kind = kind
    monkeypatch.setattr(
        "open_orchestrator.core.backend_factory.select_backend",
        lambda *a, **kw: backend,
    )
    return backend


def _silence_ref_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the git ref conflict probe — it shells out to ``git.Repo``."""
    monkeypatch.setattr(
        "open_orchestrator.commands.worktree._check_git_ref_conflicts",
        lambda branch: branch,
    )


# ---------------------------------------------------------------------------
# _resolve_ai_tool
# ---------------------------------------------------------------------------


class TestResolveAiTool:
    def test_returns_explicit_tool(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_ai_tool

        assert _resolve_ai_tool("claude") == "claude"

    def test_raises_when_none_installed(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_ai_tool

        with patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=[]):
            with pytest.raises(click.ClickException, match="No AI coding tools"):
                _resolve_ai_tool(None)

    def test_single_installed_autoselects(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_ai_tool

        with patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=["pi"]):
            assert _resolve_ai_tool(None) == "pi"

    def test_multiple_installed_prompts(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_ai_tool

        with (
            patch(
                "open_orchestrator.core.agent_detector.detect_installed_agents",
                return_value=["claude", "pi", "droid"],
            ),
            patch("click.prompt", return_value=2),
        ):
            assert _resolve_ai_tool(None) == "pi"


# ---------------------------------------------------------------------------
# _resolve_branch
# ---------------------------------------------------------------------------


class TestResolveBranch:
    def test_uses_description_words(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_branch

        with patch(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            return_value="feat/add-auth",
        ):
            task, branch = _resolve_branch(("add", "auth"), None, None)
        assert task == "add auth"
        assert branch == "feat/add-auth"

    def test_explicit_branch_with_no_description(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_branch

        task, branch = _resolve_branch((), "feat/custom", None)
        assert task == ""
        assert branch == "feat/custom"

    def test_prompts_when_no_input(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_branch

        with (
            patch("click.prompt", return_value="implement login"),
            patch(
                "open_orchestrator.core.branch_namer.generate_branch_name",
                return_value="feat/implement-login",
            ),
        ):
            task, branch = _resolve_branch((), None, None)
        assert task == "implement login"
        assert branch == "feat/implement-login"

    def test_empty_description_raises(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_branch

        with patch("click.prompt", return_value="   "):
            with pytest.raises(click.ClickException, match="cannot be empty"):
                _resolve_branch((), None, None)

    def test_branch_name_generation_error_is_click_exception(self) -> None:
        from open_orchestrator.commands.worktree import _resolve_branch

        with patch(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            side_effect=ValueError("too short"),
        ):
            with pytest.raises(click.ClickException, match="Could not generate"):
                _resolve_branch(("x",), None, None)


# ---------------------------------------------------------------------------
# load_config_safe
# ---------------------------------------------------------------------------


class TestLoadConfigSafe:
    def test_returns_loaded_config(self) -> None:
        from open_orchestrator.commands.worktree import load_config_safe

        sentinel = SimpleNamespace(backend=SimpleNamespace(mode="tmux"))
        with patch("open_orchestrator.config.load_config", return_value=sentinel):
            assert load_config_safe() is sentinel

    def test_falls_back_on_exception(self) -> None:
        from open_orchestrator.commands.worktree import load_config_safe

        with patch(
            "open_orchestrator.config.load_config",
            side_effect=RuntimeError("bad toml"),
        ):
            cfg = load_config_safe()
        # Default Config should have a backend section
        assert hasattr(cfg, "backend")


# ---------------------------------------------------------------------------
# _check_git_ref_conflicts
# ---------------------------------------------------------------------------


class TestCheckGitRefConflicts:
    def test_returns_branch_when_no_conflict(self) -> None:
        from open_orchestrator.commands.worktree import _check_git_ref_conflicts

        repo = MagicMock()
        repo.refs = [SimpleNamespace(name="main"), SimpleNamespace(name="develop")]
        with patch("git.Repo", return_value=repo):
            assert _check_git_ref_conflicts("feat/new-thing") == "feat/new-thing"

    def test_prompts_on_conflict(self) -> None:
        from open_orchestrator.commands.worktree import _check_git_ref_conflicts

        # "feat" already exists as a ref — creating "feat/x" would conflict
        repo = MagicMock()
        repo.refs = [SimpleNamespace(name="feat"), SimpleNamespace(name="main")]
        with (
            patch("git.Repo", return_value=repo),
            patch("click.prompt", return_value="feat-x"),
        ):
            assert _check_git_ref_conflicts("feat/x") == "feat-x"

    def test_returns_branch_on_repo_failure(self) -> None:
        from open_orchestrator.commands.worktree import _check_git_ref_conflicts

        with patch("git.Repo", side_effect=Exception("not a repo")):
            assert _check_git_ref_conflicts("feat/x") == "feat/x"


# ---------------------------------------------------------------------------
# new_worktree
# ---------------------------------------------------------------------------


class TestNewWorktree:
    def test_help_lists_options(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["new", "--help"])
        assert result.exit_code == 0
        assert "--branch" in result.output
        assert "--headless" in result.output
        assert "--in-place" in result.output

    def test_headless_in_place_mutually_exclusive(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["new", "x", "--headless", "--in-place", "-y"])
        assert result.exit_code != 0
        assert "cannot be used together" in result.output.lower()

    def test_herdr_and_tmux_mutually_exclusive(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["new", "x", "--herdr", "--tmux", "-y"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_herdr_and_headless_incompatible(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["new", "x", "--herdr", "--headless", "-y"])
        assert result.exit_code != 0
        assert "incompatible" in result.output.lower()

    def test_workflow_and_headless_incompatible(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["new", "x", "--workflow", "--headless", "-y"])
        assert result.exit_code != 0
        assert "plan-first" in result.output.lower()

    def test_workflow_requires_plan_mode_tool(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)
        _patch_resolved_backend(monkeypatch)

        registry = MagicMock()
        registry.get.return_value = _make_fake_tool(supports_plan_mode=False)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        result = runner.invoke(
            main_cli,
            ["new", "refactor billing", "--workflow", "--ai-tool", "droid", "-y"],
        )
        assert result.exit_code != 0
        assert "--workflow needs a plan-mode-capable tool" in result.output

    def test_workflow_sets_plan_mode_and_protocol(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)
        backend = _patch_resolved_backend(monkeypatch)
        backend.kind = BackendKind.TMUX

        registry = MagicMock()
        registry.get.return_value = _make_fake_tool()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        launcher = MagicMock()
        launcher.launch.return_value = _make_launch_result(tmux_session="owt-x")
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        result = runner.invoke(
            main_cli,
            ["new", "fix the login bug", "--workflow", "--ai-tool", "claude", "-y"],
        )
        assert result.exit_code == 0, result.output
        request = launcher.launch.call_args.args[0]
        assert request.plan_mode is True
        assert request.display_task.startswith("⟳ ")
        # The plan-first protocol scaffold is prepended to the prompt.
        assert request.prompt is not None
        assert "fix the login bug" in request.prompt
        assert len(request.prompt) > len("fix the login bug")

    def test_backend_unavailable_raises_click_exception(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from open_orchestrator.core.backend_factory import BackendUnavailableError

        _silence_ref_conflict(monkeypatch)
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            MagicMock(side_effect=BackendUnavailableError("herdr socket missing")),
        )

        result = runner.invoke(main_cli, ["new", "--branch", "feat/x", "--herdr", "-y"])
        assert result.exit_code != 0
        assert "herdr socket missing" in result.output.lower()

    def test_unknown_tool_raises(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)
        _patch_resolved_backend(monkeypatch)

        registry = MagicMock()
        registry.get.return_value = None
        registry.list_names.return_value = ["claude", "pi"]
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        result = runner.invoke(
            main_cli,
            ["new", "--branch", "feat/x", "--ai-tool", "ghost", "-y"],
        )
        assert result.exit_code != 0
        assert "unknown ai tool" in result.output.lower()

    def test_headless_with_non_headless_tool_rejected(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)

        registry = MagicMock()
        registry.get.return_value = _make_fake_tool(supports_headless=False)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        result = runner.invoke(
            main_cli,
            ["new", "add login", "--headless", "--ai-tool", "droid", "-y"],
        )
        assert result.exit_code != 0
        assert "headless mode is not supported" in result.output.lower()

    def test_success_headless_path(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)

        registry = MagicMock()
        registry.get.return_value = _make_fake_tool(supports_headless=True)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        launch_result = _make_launch_result(
            tmux_session=None,
            subprocess_pid=4242,
            warnings=["something noisy"],
        )
        launcher = MagicMock()
        launcher.launch.return_value = launch_result
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        result = runner.invoke(
            main_cli,
            ["new", "add", "auth", "--headless", "--ai-tool", "claude", "-y"],
        )
        assert result.exit_code == 0, result.output
        assert "Worktree created" in result.output
        assert "PID 4242" in result.output
        assert "something noisy" in result.output
        launcher.launch.assert_called_once()

    def test_in_place_branch_session_message(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)
        backend = _patch_resolved_backend(monkeypatch)
        backend.kind = BackendKind.TMUX

        registry = MagicMock()
        registry.get.return_value = _make_fake_tool()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        launch_result = _make_launch_result(
            worktree_name="feat-inplace",
            tmux_session="owt-feat-inplace",
        )
        launcher = MagicMock()
        launcher.launch.return_value = launch_result
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        result = runner.invoke(
            main_cli,
            ["new", "in-place task", "--in-place", "--ai-tool", "claude", "-y"],
        )
        assert result.exit_code == 0, result.output
        assert "Branch session created" in result.output
        # task preview is printed because launch_result.tmux_session is set
        assert "Sent task" in result.output

    def test_template_overrides_applied(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)
        _patch_resolved_backend(monkeypatch)

        registry = MagicMock()
        registry.get.return_value = _make_fake_tool()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        tmpl = SimpleNamespace(
            ai_instructions="Follow TDD strictly.",
            ai_tool="claude",
            plan_mode=True,
            base_branch="develop",
        )
        monkeypatch.setattr(
            "open_orchestrator.config.get_builtin_templates",
            lambda: {"tdd": tmpl},
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        launch_result = _make_launch_result()
        launcher = MagicMock()
        launcher.launch.return_value = launch_result
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        result = runner.invoke(
            main_cli,
            ["new", "add auth", "-t", "tdd", "--branch", "feat/x"],
        )
        assert result.exit_code == 0, result.output
        request = launcher.launch.call_args[0][0]
        assert request.plan_mode is True
        assert request.base_branch == "develop"
        assert "Follow TDD" in (request.prompt or "")

    def test_attach_after_create_invokes_backend_attach(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _silence_ref_conflict(monkeypatch)
        backend = _patch_resolved_backend(monkeypatch)

        monkeypatch.setattr(
            "open_orchestrator.commands.worktree._resolve_ai_tool",
            lambda t: t or "claude",
        )
        registry = MagicMock()
        registry.get.return_value = _make_fake_tool()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        launch_result = _make_launch_result(backend_session_id="owt-feat-x")
        launcher = MagicMock()
        launcher.launch.return_value = launch_result
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        result = runner.invoke(
            main_cli,
            ["new", "--branch", "feat/x", "--attach", "-y"],
        )
        assert result.exit_code == 0, result.output
        backend.attach.assert_called_once()

    def test_pane_action_error_becomes_click_exception(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from open_orchestrator.core.pane_actions import PaneActionError

        _silence_ref_conflict(monkeypatch)
        _patch_resolved_backend(monkeypatch)

        monkeypatch.setattr(
            "open_orchestrator.commands.worktree._resolve_ai_tool",
            lambda t: t or "claude",
        )
        registry = MagicMock()
        registry.get.return_value = _make_fake_tool()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        launcher = MagicMock()
        launcher.launch.side_effect = PaneActionError("backend ran away")
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        result = runner.invoke(main_cli, ["new", "--branch", "feat/x", "-y"])
        assert result.exit_code != 0
        assert "backend ran away" in result.output.lower()

    def test_confirm_prompts_when_not_yes_and_no_explicit_branch(
        self,
        runner: CliRunner,
        main_cli: click.Group,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without -y, the command confirms the generated branch and on 'n'
        re-prompts for a branch name. We provide a name to keep going."""
        _silence_ref_conflict(monkeypatch)
        _patch_resolved_backend(monkeypatch)

        monkeypatch.setattr(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            lambda desc, prefix=None: "feat/auto-name",
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree._resolve_ai_tool",
            lambda t: t or "claude",
        )

        registry = MagicMock()
        registry.get.return_value = _make_fake_tool()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        launcher = MagicMock()
        launcher.launch.return_value = _make_launch_result(branch="feat/custom-name")
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        # Reply 'n' to the confirm, then provide an alternative branch name
        result = runner.invoke(
            main_cli,
            ["new", "add", "feature"],
            input="n\nfeat/custom-name\n",
        )
        assert result.exit_code == 0, result.output
        request = launcher.launch.call_args[0][0]
        assert request.branch == "feat/custom-name"


# ---------------------------------------------------------------------------
# list_worktrees
# ---------------------------------------------------------------------------


class TestListWorktrees:
    def test_empty(self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch) -> None:
        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        result = runner.invoke(main_cli, ["list"])
        assert result.exit_code == 0
        assert "No worktrees" in result.output

    def test_renders_all_status_branches(self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch) -> None:
        wt_main = _make_worktree(name="main", branch="main", is_main=True)
        wt_a = _make_worktree(name="feat-a", branch="feat/a")
        wt_b = _make_worktree(name="feat-b", branch="feat/b")
        wt_c = _make_worktree(name="feat-c", branch="feat/c")
        wt_d = _make_worktree(name="feat-d", branch="feat/d")
        wt_e = _make_worktree(name="feat-e", branch="feat/e")

        statuses = [
            _make_status(name="feat-a", activity=AIActivityStatus.WORKING),
            _make_status(name="feat-b", activity=AIActivityStatus.IDLE),
            _make_status(name="feat-c", activity=AIActivityStatus.BLOCKED),
            _make_status(name="feat-d", activity=AIActivityStatus.COMPLETED),
            _make_status(name="feat-e", activity=AIActivityStatus.STALLED),
            # Branch-mode session — exists in tracker but not git worktree list
            _make_status(
                name="branch-only",
                branch="feat/branch-only",
                activity=AIActivityStatus.WORKING,
                tmux_session=None,
                backend_session_id="herdr-pane-1",
                backend_kind="herdr",
            ),
        ]
        wt_manager = _make_wt_manager(wt_main, wt_a, wt_b, wt_c, wt_d, wt_e)
        tracker = _make_tracker(*statuses)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        result = runner.invoke(main_cli, ["list", "--all"])
        assert result.exit_code == 0, result.output
        # Main worktree visible only with --all
        assert "main" in result.output
        for name in ("feat-a", "feat-b", "feat-c", "feat-d", "feat-e", "branch-only"):
            assert name in result.output

    def test_default_hides_main(self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch) -> None:
        wt_main = _make_worktree(name="main", branch="main", is_main=True)
        wt_a = _make_worktree(name="feat-a", branch="feat/a")
        wt_manager = _make_wt_manager(wt_main, wt_a)
        tracker = _make_tracker(
            _make_status(name="feat-a", activity=AIActivityStatus.WORKING),
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        result = runner.invoke(main_cli, ["list"])
        assert result.exit_code == 0
        assert "feat-a" in result.output


# ---------------------------------------------------------------------------
# switch_worktree
# ---------------------------------------------------------------------------


class TestSwitchWorktree:
    def test_attaches_recorded_backend_session(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=session)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        backend = MagicMock()
        backend.is_alive.return_value = True
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend_for_session",
            lambda s: backend,
        )

        result = runner.invoke(main_cli, ["switch", "feat-x"])
        assert result.exit_code == 0, result.output
        backend.attach.assert_called_once_with(session)

    def test_legacy_fallback_uses_tmux_session_for_lookup(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        # No recorded backend session — triggers legacy lookup branch
        tracker = _make_tracker(_make_status(), backend_session=None)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        legacy_session = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        backend = MagicMock()
        backend.session_for.return_value = legacy_session
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["switch", "feat-x"])
        assert result.exit_code == 0, result.output
        backend.attach.assert_called_once_with(legacy_session)

    def test_legacy_fallback_no_session_raises(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=None)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        backend = MagicMock()
        backend.session_for.return_value = None
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["switch", "feat-x"])
        assert result.exit_code != 0
        assert "no session" in result.output.lower()

    def test_recorded_session_dead_raises(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=session)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        backend = MagicMock()
        backend.is_alive.return_value = False
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend_for_session",
            lambda s: backend,
        )

        result = runner.invoke(main_cli, ["switch", "feat-x"])
        assert result.exit_code != 0
        assert "no tmux session" in result.output.lower()


# ---------------------------------------------------------------------------
# delete_worktree
# ---------------------------------------------------------------------------


class TestDeleteWorktree:
    def test_cannot_delete_main(self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch) -> None:
        wt_main = _make_worktree(name="main", branch="main", is_main=True)
        wt_manager = _make_wt_manager(wt_main)
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        result = runner.invoke(main_cli, ["delete", "main", "-y"])
        assert result.exit_code != 0
        assert "cannot delete the main" in result.output.lower()

    def test_aborts_at_confirm(self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        with patch("open_orchestrator.core.pane_actions.teardown_worktree", return_value=[]) as td:
            result = runner.invoke(main_cli, ["delete", "feat-x"], input="n\n")

        assert result.exit_code == 0
        assert "aborted" in result.output.lower()
        td.assert_not_called()

    def test_success_deletes_worktree(self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status())
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        with patch("open_orchestrator.core.pane_actions.teardown_worktree", return_value=[]) as td:
            result = runner.invoke(main_cli, ["delete", "feat-x", "-y"])

        assert result.exit_code == 0, result.output
        assert "Deleted worktree" in result.output
        td.assert_called_once()
        # default delete_git_worktree=True path
        _, kwargs = td.call_args
        assert kwargs.get("delete_git_worktree") is True

    def test_branch_session_deletes_branch_not_worktree(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Branch-mode session — no git worktree on disk
        wt_manager = _make_wt_manager()
        status = _make_status(
            name="branch-only",
            branch="feat/branch-only",
            session_type="branch",
        )
        tracker = _make_tracker(status)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        with patch("open_orchestrator.core.pane_actions.teardown_worktree", return_value=[]) as td:
            result = runner.invoke(main_cli, ["delete", "branch-only", "-y"])

        assert result.exit_code == 0, result.output
        assert "Deleted branch session" in result.output
        _, kwargs = td.call_args
        assert kwargs.get("delete_git_worktree") is False
        assert kwargs.get("delete_branch") is True
        assert kwargs.get("pop_stash") is True

    def test_git_error_raises_click_exception(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status())
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        with patch(
            "open_orchestrator.core.pane_actions.teardown_worktree",
            return_value=["failed to remove git worktree: locked"],
        ):
            result = runner.invoke(main_cli, ["delete", "feat-x", "-y"])

        assert result.exit_code != 0
        assert "git worktree" in result.output.lower()

    def test_non_git_error_is_warning(self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status())
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        with patch(
            "open_orchestrator.core.pane_actions.teardown_worktree",
            return_value=["tmux session already gone"],
        ):
            result = runner.invoke(main_cli, ["delete", "feat-x", "-y"])

        # Non-git errors are warnings, not fatal
        assert result.exit_code == 0, result.output
        assert "Warning" in result.output


# ---------------------------------------------------------------------------
# branch_cmd
# ---------------------------------------------------------------------------


class TestBranchCmd:
    def test_help(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["branch", "--help"])
        assert result.exit_code == 0
        assert "branch" in result.output.lower()

    def test_branch_forwards_to_new_with_in_place(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _silence_ref_conflict(monkeypatch)
        _patch_resolved_backend(monkeypatch)

        monkeypatch.setattr(
            "open_orchestrator.commands.worktree._resolve_ai_tool",
            lambda t: t or "claude",
        )
        registry = MagicMock()
        registry.get.return_value = _make_fake_tool()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_registry",
            lambda: registry,
        )

        wt_manager = _make_wt_manager()
        tracker = _make_tracker()
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            lambda desc, prefix=None: "feat/auto",
        )

        launch_result = _make_launch_result(worktree_name="feat-auto")
        launcher = MagicMock()
        launcher.launch.return_value = launch_result
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.AgentLauncher",
            lambda **kw: launcher,
        )

        from open_orchestrator.models.worktree_info import SessionType

        result = runner.invoke(main_cli, ["branch", "add", "auth", "-y"])
        assert result.exit_code == 0, result.output
        request = launcher.launch.call_args[0][0]
        assert request.session_type == SessionType.BRANCH


# ---------------------------------------------------------------------------
# attach_worktree
# ---------------------------------------------------------------------------


class TestAttachWorktree:
    def test_help(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["attach", "--help"])
        assert result.exit_code == 0

    def test_force_herdr_and_tmux_mutually_exclusive(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even though we never reach the resolution path, the command
        # may still consult worktree manager before raising. Provide
        # safe mocks so the error we get back is the mutex one.
        wt_manager = _make_wt_manager(_make_worktree())
        tracker = _make_tracker(_make_status())
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x", "--herdr", "--tmux"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_no_override_recorded_session(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=session)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        backend = MagicMock()
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend_for_session",
            lambda s: backend,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x"])
        assert result.exit_code == 0, result.output
        backend.attach.assert_called_once_with(session)

    def test_no_override_no_recorded_falls_back_to_tmux(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=None)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        # load_config has to succeed (not load_config_safe)
        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )

        legacy_session = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        backend = MagicMock()
        backend.session_for.return_value = legacy_session
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x"])
        assert result.exit_code == 0, result.output
        backend.attach.assert_called_once_with(legacy_session)

    def test_no_override_no_session_raises(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=None)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )

        backend = MagicMock()
        backend.session_for.return_value = None
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x"])
        assert result.exit_code != 0
        assert "no session" in result.output.lower()

    def test_no_override_backend_unavailable(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from open_orchestrator.core.backend_factory import BackendUnavailableError

        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=None)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            MagicMock(side_effect=BackendUnavailableError("no tmux")),
        )

        result = runner.invoke(main_cli, ["attach", "feat-x"])
        assert result.exit_code != 0
        assert "no tmux" in result.output.lower()

    def test_force_override_reresolves_when_recorded_differs(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Recorded session is herdr but user forces --tmux
        recorded = BackendSession(kind=BackendKind.HERDR, id="pane-1", worktree_name="feat-x")
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=recorded)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )

        tmux_session = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        backend = MagicMock()
        backend.session_for.return_value = tmux_session
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x", "--tmux"])
        assert result.exit_code == 0, result.output
        backend.session_for.assert_called_once_with("feat-x")
        backend.attach.assert_called_once_with(tmux_session)

    def test_force_override_reresolve_no_session_raises(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = BackendSession(kind=BackendKind.HERDR, id="pane-1", worktree_name="feat-x")
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=recorded)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )

        backend = MagicMock()
        backend.session_for.return_value = None
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x", "--tmux"])
        assert result.exit_code != 0
        assert "recorded as herdr" in result.output.lower()

    def test_force_override_matches_recorded_uses_recorded(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=recorded)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )

        backend = MagicMock()
        backend.kind = BackendKind.TMUX
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x", "--tmux"])
        assert result.exit_code == 0, result.output
        backend.attach.assert_called_once_with(recorded)

    def test_force_override_backend_unavailable(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from open_orchestrator.core.backend_factory import BackendUnavailableError

        recorded = BackendSession(kind=BackendKind.TMUX, id="owt-feat-x", worktree_name="feat-x")
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=recorded)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            MagicMock(side_effect=BackendUnavailableError("herdr offline")),
        )

        result = runner.invoke(main_cli, ["attach", "feat-x", "--herdr"])
        assert result.exit_code != 0
        assert "herdr offline" in result.output.lower()

    def test_force_override_matches_no_session_raises(
        self, runner: CliRunner, main_cli: click.Group, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Recorded matches the forced backend, but session_for returns None
        # and recorded is also None on this path (the right-branch of the
        # final else)
        wt = _make_worktree()
        wt_manager = _make_wt_manager(wt)
        tracker = _make_tracker(_make_status(), backend_session=None)
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_worktree_manager",
            lambda: wt_manager,
        )
        monkeypatch.setattr(
            "open_orchestrator.commands.worktree.get_status_tracker",
            lambda _root: tracker,
        )

        monkeypatch.setattr(
            "open_orchestrator.config.load_config",
            lambda: SimpleNamespace(backend=SimpleNamespace(mode="tmux")),
        )

        backend = MagicMock()
        backend.kind = BackendKind.TMUX
        backend.session_for.return_value = None
        monkeypatch.setattr(
            "open_orchestrator.core.backend_factory.select_backend",
            lambda *a, **kw: backend,
        )

        result = runner.invoke(main_cli, ["attach", "feat-x", "--tmux"])
        assert result.exit_code != 0
        assert "no tmux session" in result.output.lower()
