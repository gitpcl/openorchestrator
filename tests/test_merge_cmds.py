"""CliRunner tests for ``commands/merge_cmds``.

The merge / ship / queue commands stitch together :class:`MergeManager`,
the worktree manager, and the tmux teardown helper. These tests pin the
CLI surface (option parsing, confirmation prompts, error handling, output
strings, exit codes) while monkeypatching the heavy collaborators.

Why CliRunner and not pytest-click? CliRunner is the canonical test
harness in Click's own docs and runs each invocation in an isolated
environment so output assertions stay deterministic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    # Click 8.2 removed ``mix_stderr``; stderr is always separate now.
    return CliRunner()


@pytest.fixture
def main_cli() -> click.Group:
    """Build a fresh CLI group with merge_cmds registered.

    Importing the real ``main`` would pull config + tracker side effects.
    A bare ``click.Group`` plus ``merge_cmds.register`` is enough surface
    for these tests and keeps them fast.
    """
    from open_orchestrator.commands import merge_cmds

    @click.group()
    def cli() -> None:  # pragma: no cover - trivial top-level
        pass

    merge_cmds.register(cli)
    return cli


def _make_worktree(name: str = "feat-x", branch: str = "feat/x") -> MagicMock:
    wt = MagicMock()
    wt.name = name
    wt.branch = branch
    wt.path = Path("/tmp/owt-test") / name
    wt.is_main = False
    return wt


def _make_merge_manager(*, worktree_cleaned: bool = False, status: str = "success", message: str = "merged ok") -> MagicMock:
    from open_orchestrator.core.merge import MergeStatus

    mm = MagicMock()
    mm.wt_manager = MagicMock()
    mm.wt_manager.git_root = Path("/tmp/owt-test")
    mm.get_base_branch.return_value = "main"
    mm.count_commits_ahead.return_value = 3
    mm.check_file_overlaps.return_value = {}
    mm.check_uncommitted_changes.return_value = []
    mm.plan_merge_order.return_value = []

    result = MagicMock()
    if status == "success":
        result.status = MergeStatus.SUCCESS
    elif status == "already_merged":
        result.status = MergeStatus.ALREADY_MERGED
    else:
        result.status = MergeStatus.FAILED  # type: ignore[attr-defined]
    result.source_branch = "feat/x"
    result.target_branch = "main"
    result.commits_merged = 3
    result.worktree_cleaned = worktree_cleaned
    result.message = message
    mm.merge.return_value = result
    return mm


# ---------------------------------------------------------------------------
# _detect_session_type
# ---------------------------------------------------------------------------


class TestDetectSessionType:
    def test_returns_false_when_worktree_exists(self) -> None:
        from open_orchestrator.commands.merge_cmds import _detect_session_type

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wm:
            mock_wm.return_value.get.return_value = _make_worktree()
            assert _detect_session_type("feat-x") is False

    def test_returns_true_when_worktree_missing(self) -> None:
        from open_orchestrator.commands.merge_cmds import _detect_session_type
        from open_orchestrator.core.worktree import WorktreeNotFoundError

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wm:
            mock_wm.return_value.get.side_effect = WorktreeNotFoundError("not found")
            assert _detect_session_type("feat-x") is True

    def test_returns_false_on_unexpected_exception(self) -> None:
        from open_orchestrator.commands.merge_cmds import _detect_session_type

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wm:
            mock_wm.return_value.get.side_effect = RuntimeError("bad git")
            assert _detect_session_type("feat-x") is False


# ---------------------------------------------------------------------------
# merge command
# ---------------------------------------------------------------------------


class TestMergeCommand:
    def test_help_lists_options(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["merge", "--help"])
        assert result.exit_code == 0
        assert "--base" in result.output
        assert "--keep" in result.output
        assert "--rebase" in result.output
        assert "--leave-conflicts" in result.output

    def test_missing_worktree_raises_click_exception(self, runner: CliRunner, main_cli: click.Group) -> None:
        from open_orchestrator.core.worktree import WorktreeNotFoundError

        mm = _make_merge_manager()
        wt_manager = MagicMock()
        wt_manager.get.side_effect = WorktreeNotFoundError("worktree 'missing' not found")

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
        ):
            result = runner.invoke(main_cli, ["merge", "missing", "-y"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "not found" in (result.stderr or "").lower()

    def test_user_aborts_at_confirm(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
        ):
            # Reply 'n' to the confirm prompt
            result = runner.invoke(main_cli, ["merge", "feat-x"], input="n\n")

        assert result.exit_code == 0
        assert "abort" in result.output.lower()
        mm.merge.assert_not_called()

    def test_success_path_with_yes(self, runner: CliRunner, main_cli: click.Group) -> None:
        # ``worktree_cleaned=True`` is the path that triggers the explicit
        # teardown branch in merge_worktree (deletes only tmux + status).
        mm = _make_merge_manager(worktree_cleaned=True)
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]) as td,
        ):
            result = runner.invoke(main_cli, ["merge", "feat-x", "-y"])

        assert result.exit_code == 0, result.output
        assert "merged" in result.output.lower()
        mm.merge.assert_called_once()
        # Teardown should fire because --keep is not set and worktree_cleaned=True
        td.assert_called()

    def test_keep_flag_skips_teardown(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager(worktree_cleaned=False)
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]) as td,
        ):
            result = runner.invoke(main_cli, ["merge", "feat-x", "-y", "--keep"])

        assert result.exit_code == 0, result.output
        td.assert_not_called()

    def test_already_merged_path(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager(status="already_merged", message="branch already merged")
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]),
        ):
            result = runner.invoke(main_cli, ["merge", "feat-x", "-y"])

        assert result.exit_code == 0
        assert "already merged" in result.output.lower()

    def test_merge_error_raises_click_exception(self, runner: CliRunner, main_cli: click.Group) -> None:
        from open_orchestrator.core.merge import MergeError

        mm = _make_merge_manager()
        mm.merge.side_effect = MergeError("base branch missing")
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
        ):
            result = runner.invoke(main_cli, ["merge", "feat-x", "-y"])

        assert result.exit_code != 0
        # Click prints the message via "Error: <msg>" or to stderr
        combined = result.output + (result.stderr or "")
        assert "base branch missing" in combined.lower()

    def test_branch_mode_invokes_branch_path(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=True),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]),
        ):
            result = runner.invoke(main_cli, ["merge", "feat/branch-only", "-y"])

        assert result.exit_code == 0, result.output
        # branch_mode kwarg should be True when MergeManager.merge is called
        _, kwargs = mm.merge.call_args
        assert kwargs.get("branch_mode") is True

    def test_overlap_warning_surfaced(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        mm.check_file_overlaps.return_value = {"src/api.py": ["other-wt"]}
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]),
        ):
            result = runner.invoke(main_cli, ["merge", "feat-x", "-y"])

        assert "overlap" in result.output.lower()
        assert "src/api.py" in result.output


# ---------------------------------------------------------------------------
# ship command
# ---------------------------------------------------------------------------


class TestShipCommand:
    def test_help_lists_options(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["ship", "--help"])
        assert result.exit_code == 0
        assert "--message" in result.output
        assert "--base" in result.output

    def test_missing_worktree_raises_click_exception(self, runner: CliRunner, main_cli: click.Group) -> None:
        from open_orchestrator.core.worktree import WorktreeNotFoundError

        wt_manager = MagicMock()
        wt_manager.get.side_effect = WorktreeNotFoundError("worktree 'missing' not found")

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
        ):
            result = runner.invoke(main_cli, ["ship", "missing", "-y"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "not found" in combined.lower()

    def test_cannot_ship_main_worktree(self, runner: CliRunner, main_cli: click.Group) -> None:
        worktree = _make_worktree()
        worktree.is_main = True
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
        ):
            result = runner.invoke(main_cli, ["ship", "main", "-y"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "main" in combined.lower()

    def test_aborts_at_confirm(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
        ):
            result = runner.invoke(main_cli, ["ship", "feat-x"], input="n\n")

        assert result.exit_code == 0
        assert "abort" in result.output.lower()
        mm.merge.assert_not_called()

    def test_success_path_with_yes_no_dirty(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager(worktree_cleaned=True)
        mm.check_uncommitted_changes.return_value = []
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree
        wt_manager.git_root = Path("/tmp/owt-test")

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]),
        ):
            result = runner.invoke(main_cli, ["ship", "feat-x", "-y"])

        assert result.exit_code == 0, result.output
        assert "shipped" in result.output.lower()
        mm.merge.assert_called_once()


# ---------------------------------------------------------------------------
# queue command
# ---------------------------------------------------------------------------


class TestQueueCommand:
    def test_help(self, runner: CliRunner, main_cli: click.Group) -> None:
        result = runner.invoke(main_cli, ["queue", "--help"])
        assert result.exit_code == 0
        assert "--ship" in result.output

    def test_empty_queue_message(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        mm.plan_merge_order.return_value = []

        with patch("open_orchestrator.core.merge.MergeManager", return_value=mm):
            result = runner.invoke(main_cli, ["queue"])

        assert result.exit_code == 0
        assert "no completed" in result.output.lower() or "ready to merge" in result.output.lower()

    def test_queue_displays_table(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        mm.plan_merge_order.return_value = [
            ("feat-a", 2, 0),
            ("feat-b", 5, 1),
        ]

        with patch("open_orchestrator.core.merge.MergeManager", return_value=mm):
            result = runner.invoke(main_cli, ["queue"])

        assert result.exit_code == 0
        assert "Merge Queue" in result.output
        assert "feat-a" in result.output
        assert "feat-b" in result.output

    def test_queue_ship_aborts_at_confirm(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        mm.plan_merge_order.return_value = [("feat-a", 2, 0)]

        with patch("open_orchestrator.core.merge.MergeManager", return_value=mm):
            result = runner.invoke(main_cli, ["queue", "--ship"], input="n\n")

        assert result.exit_code == 0
        assert "abort" in result.output.lower()
        mm.merge.assert_not_called()

    def test_queue_ship_yes_runs_merges(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        mm.plan_merge_order.return_value = [("feat-a", 2, 0), ("feat-b", 1, 0)]

        with (
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]),
        ):
            result = runner.invoke(main_cli, ["queue", "--ship", "-y"])

        assert result.exit_code == 0, result.output
        assert mm.merge.call_count == 2

    def test_queue_ship_stops_on_conflict(self, runner: CliRunner, main_cli: click.Group) -> None:
        from open_orchestrator.core.merge import MergeConflictError

        mm = _make_merge_manager()
        mm.plan_merge_order.return_value = [("feat-a", 2, 0), ("feat-b", 1, 0)]
        # Configure the conflict exception with the .conflicts attribute the
        # printer expects.
        exc = MergeConflictError("conflicts in feat-a", conflicts=["src/a.py"])
        mm.merge.side_effect = exc

        with patch("open_orchestrator.core.merge.MergeManager", return_value=mm):
            result = runner.invoke(main_cli, ["queue", "--ship", "-y"])

        # Should not proceed to the second merge after a conflict
        assert mm.merge.call_count == 1
        assert "conflict" in result.output.lower()


# ---------------------------------------------------------------------------
# merge command conflict path + branch-mode ship
# ---------------------------------------------------------------------------


class TestMergeConflictPath:
    def test_merge_conflict_prints_files_and_exits_nonzero(self, runner: CliRunner, main_cli: click.Group) -> None:
        from open_orchestrator.core.merge import MergeConflictError

        mm = _make_merge_manager()
        mm.merge.side_effect = MergeConflictError(
            "conflicts in feat-x",
            conflicts=["src/a.py", "src/b.py"],
        )
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
        ):
            result = runner.invoke(main_cli, ["merge", "feat-x", "-y"])

        assert result.exit_code == 1
        assert "conflict" in result.output.lower()
        assert "src/a.py" in result.output
        assert "src/b.py" in result.output

    def test_merge_conflict_leave_in_progress_message(self, runner: CliRunner, main_cli: click.Group) -> None:
        from open_orchestrator.core.merge import MergeConflictError

        mm = _make_merge_manager()
        mm.merge.side_effect = MergeConflictError(
            "conflicts in feat-x",
            conflicts=["src/a.py"],
        )
        worktree = _make_worktree()
        wt_manager = MagicMock()
        wt_manager.get.return_value = worktree

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=False),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.get_worktree_manager", return_value=wt_manager),
        ):
            result = runner.invoke(main_cli, ["merge", "feat-x", "-y", "--leave-conflicts", "--rebase"])

        assert result.exit_code == 1
        # "Rebase left in-progress" hint should appear when --rebase + --leave-conflicts
        assert "rebase" in result.output.lower()
        assert "in-progress" in result.output.lower() or "leave" in result.output.lower()


class TestShipBranchMode:
    """Cover the _ship_branch helper (branch-mode ship)."""

    def test_branch_ship_aborts_at_confirm(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        repo = MagicMock()
        repo.is_dirty.return_value = False

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=True),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("git.Repo", return_value=repo),
        ):
            result = runner.invoke(main_cli, ["ship", "feat/branch-only"], input="n\n")

        assert result.exit_code == 0
        assert "abort" in result.output.lower()
        mm.merge.assert_not_called()

    def test_branch_ship_success_with_dirty_commit(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        repo = MagicMock()
        repo.is_dirty.return_value = True
        repo.git = MagicMock()

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=True),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]),
            patch("git.Repo", return_value=repo),
        ):
            result = runner.invoke(main_cli, ["ship", "feat/branch-only", "-y", "-m", "feat: thing"])

        assert result.exit_code == 0, result.output
        repo.git.add.assert_called_with("-A")
        repo.git.commit.assert_called_once()
        # branch_mode=True in MergeManager.merge call
        _, kwargs = mm.merge.call_args
        assert kwargs.get("branch_mode") is True
        assert "shipped" in result.output.lower()

    def test_branch_ship_no_dirty_skips_commit(self, runner: CliRunner, main_cli: click.Group) -> None:
        mm = _make_merge_manager()
        repo = MagicMock()
        repo.is_dirty.return_value = False
        repo.git = MagicMock()

        with (
            patch("open_orchestrator.commands.merge_cmds._detect_session_type", return_value=True),
            patch("open_orchestrator.core.merge.MergeManager", return_value=mm),
            patch("open_orchestrator.commands.merge_cmds.teardown_worktree", return_value=[]),
            patch("git.Repo", return_value=repo),
        ):
            result = runner.invoke(main_cli, ["ship", "feat/branch-only", "-y"])

        assert result.exit_code == 0, result.output
        repo.git.commit.assert_not_called()


class TestPrivateHelpers:
    """Targeted tests for helpers that don't fit neatly into the command flow."""

    def test_auto_commit_dirty_files_uses_default_msg(self, tmp_path: Path) -> None:
        from open_orchestrator.commands.merge_cmds import _auto_commit_dirty_files

        repo = MagicMock()
        repo.git = MagicMock()
        with patch("git.Repo", return_value=repo):
            msg = _auto_commit_dirty_files(tmp_path, "feat/my-cool-thing", None)

        assert "my cool thing" in msg
        repo.git.add.assert_called_with("-A")
        repo.git.commit.assert_called_once()

    def test_auto_commit_dirty_files_honors_explicit_msg(self, tmp_path: Path) -> None:
        from open_orchestrator.commands.merge_cmds import _auto_commit_dirty_files

        repo = MagicMock()
        repo.git = MagicMock()
        with patch("git.Repo", return_value=repo):
            msg = _auto_commit_dirty_files(tmp_path, "feat/x", "explicit message")

        assert msg == "explicit message"
