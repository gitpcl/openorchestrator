"""CliRunner tests for ``commands/orchestrate_cmds``.

Tests cover the three orchestration commands (``plan``, ``batch``,
``orchestrate``) plus the shared status-print helpers. Heavy collaborators
(``plan_tasks``, ``BatchRunner``, ``Orchestrator``) are monkeypatched so
the suite stays fast and deterministic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli() -> click.Group:
    from open_orchestrator.commands import orchestrate_cmds

    @click.group()
    def main() -> None:  # pragma: no cover
        pass

    orchestrate_cmds.register(main)
    return main


def _toml_plan(tmp_path: Path, n_tasks: int = 2) -> Path:
    """Write a minimal valid plan TOML to ``tmp_path/plan.toml``."""
    lines = ["[batch]\nmax_concurrent = 3\n"]
    for i in range(n_tasks):
        lines.append(f'\n[[tasks]]\nid = "t{i}"\ndescription = "Task {i}"\n')
        if i > 0:
            lines.append(f'depends_on = ["t{i - 1}"]\n')
    path = tmp_path / "plan.toml"
    path.write_text("".join(lines))
    return path


def _wt_manager(repo_root: Path) -> MagicMock:
    mgr = MagicMock()
    mgr.git_root = repo_root
    return mgr


# ---------------------------------------------------------------------------
# plan command
# ---------------------------------------------------------------------------


class TestPlanCommand:
    def test_help_lists_flags(self, runner: CliRunner, cli: click.Group) -> None:
        result = runner.invoke(cli, ["plan", "--help"])
        assert result.exit_code == 0
        assert "--execute" in result.output
        assert "--start" in result.output
        assert "--ai-tool" in result.output
        assert "--auto-ship" in result.output

    def test_plan_no_ai_tools_raises(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=[]),
        ):
            result = runner.invoke(cli, ["plan", "Build auth"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "ai coding tool" in combined.lower() or "no ai" in combined.lower()

    def test_plan_success_writes_summary(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        plan_path = _toml_plan(tmp_path, n_tasks=2)
        wt_mgr = _wt_manager(tmp_path)

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=["claude"]),
            patch("open_orchestrator.core.batch.plan_tasks", return_value=plan_path),
        ):
            result = runner.invoke(cli, ["plan", "Build", "JWT", "auth"])

        assert result.exit_code == 0, result.output
        assert "Plan:" in result.output
        assert "Tasks:" in result.output
        assert "t0" in result.output
        assert "t1" in result.output

    def test_plan_planner_raises_runtime_error(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=["claude"]),
            patch("open_orchestrator.core.batch.plan_tasks", side_effect=RuntimeError("plan failed")),
        ):
            result = runner.invoke(cli, ["plan", "Build", "auth"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "plan failed" in combined.lower()

    def test_plan_keyboard_interrupt_is_friendly(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=["claude"]),
            patch("open_orchestrator.core.batch.plan_tasks", side_effect=KeyboardInterrupt),
        ):
            result = runner.invoke(cli, ["plan", "Build", "auth"])

        assert result.exit_code == 0
        assert "cancel" in result.output.lower()

    def test_plan_execute_runs_batch(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        plan_path = _toml_plan(tmp_path, n_tasks=1)
        wt_mgr = _wt_manager(tmp_path)

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=["claude"]),
            patch("open_orchestrator.core.batch.plan_tasks", return_value=plan_path),
            patch("open_orchestrator.commands.orchestrate_cmds._execute_batch") as ex,
        ):
            result = runner.invoke(cli, ["plan", "x", "--execute", "--auto-ship", "--max-concurrent", "5"])

        assert result.exit_code == 0, result.output
        ex.assert_called_once()
        args, _ = ex.call_args
        assert args[1] is True  # auto_ship
        assert args[2] == 5  # max_concurrent

    def test_plan_start_invokes_orchestrator(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        plan_path = _toml_plan(tmp_path, n_tasks=1)
        wt_mgr = _wt_manager(tmp_path)

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=["claude"]),
            patch("open_orchestrator.core.batch.plan_tasks", return_value=plan_path),
            patch("open_orchestrator.commands.orchestrate_cmds._start_orchestrator") as st,
        ):
            result = runner.invoke(cli, ["plan", "Build", "auth", "--start", "--branch", "feat/auth-v2"])

        assert result.exit_code == 0, result.output
        st.assert_called_once()
        # branch passed through to _start_orchestrator
        args, _ = st.call_args
        assert args[2] == "feat/auth-v2"

    def test_plan_no_action_prints_hint(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        plan_path = _toml_plan(tmp_path, n_tasks=1)
        wt_mgr = _wt_manager(tmp_path)

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.agent_detector.detect_installed_agents", return_value=["claude"]),
            patch("open_orchestrator.core.batch.plan_tasks", return_value=plan_path),
        ):
            result = runner.invoke(cli, ["plan", "Build", "auth"])

        assert result.exit_code == 0
        assert "owt batch" in result.output or "--execute" in result.output


# ---------------------------------------------------------------------------
# batch command
# ---------------------------------------------------------------------------


class TestBatchCommand:
    def test_help(self, runner: CliRunner, cli: click.Group) -> None:
        result = runner.invoke(cli, ["batch", "--help"])
        assert result.exit_code == 0
        assert "--auto-ship" in result.output
        assert "--resume" in result.output

    def test_batch_requires_file_or_resume(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        # No tasks_file + no --resume → ClickException
        with patch(
            "open_orchestrator.commands.orchestrate_cmds.get_worktree_manager",
            return_value=wt_mgr,
        ):
            result = runner.invoke(cli, ["batch"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "resume" in combined.lower() or "tasks file" in combined.lower()

    def test_batch_resume_no_state(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.batch.BatchRunner.resume", side_effect=FileNotFoundError),
        ):
            result = runner.invoke(cli, ["batch", "--resume"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "no batch state" in combined.lower() or "start with" in combined.lower()

    def test_batch_executes_plan(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        plan_path = _toml_plan(tmp_path, n_tasks=2)
        wt_mgr = _wt_manager(tmp_path)

        fake_runner = MagicMock()
        fake_runner.run.return_value = []

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.batch.BatchRunner", return_value=fake_runner),
            patch("open_orchestrator.commands.orchestrate_cmds.print_batch_results"),
        ):
            result = runner.invoke(cli, ["batch", str(plan_path), "--auto-ship", "--max-concurrent", "4"])

        assert result.exit_code == 0, result.output
        fake_runner.run.assert_called_once()

    def test_batch_json_output(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        plan_path = _toml_plan(tmp_path, n_tasks=1)
        wt_mgr = _wt_manager(tmp_path)

        from open_orchestrator.core.batch_models import BatchStatus

        result_record = MagicMock()
        result_record.task.description = "Task 0"
        result_record.worktree_name = "wt-0"
        result_record.status = BatchStatus.COMPLETED
        result_record.error = None

        fake_runner = MagicMock()
        fake_runner.run.return_value = [result_record]

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.batch.BatchRunner", return_value=fake_runner),
        ):
            result = runner.invoke(cli, ["batch", str(plan_path), "--json"])

        assert result.exit_code == 0, result.output
        # Output should be parseable JSON
        import json as _json

        # Strip Rich's "Batch: ..." preamble line(s) by finding the first '['
        json_start = result.output.find("[")
        parsed = _json.loads(result.output[json_start:])
        assert parsed[0]["task"] == "Task 0"

    def test_batch_keyboard_interrupt(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        plan_path = _toml_plan(tmp_path, n_tasks=1)
        wt_mgr = _wt_manager(tmp_path)

        fake_runner = MagicMock()
        fake_runner.run.side_effect = KeyboardInterrupt

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.batch.BatchRunner", return_value=fake_runner),
        ):
            result = runner.invoke(cli, ["batch", str(plan_path)])

        assert result.exit_code == 0
        assert "interrupt" in result.output.lower()


# ---------------------------------------------------------------------------
# orchestrate command
# ---------------------------------------------------------------------------


class TestOrchestrateCommand:
    def test_help(self, runner: CliRunner, cli: click.Group) -> None:
        result = runner.invoke(cli, ["orchestrate", "--help"])
        assert result.exit_code == 0
        assert "--branch" in result.output
        assert "--resume" in result.output
        assert "--stop" in result.output
        assert "--status" in result.output

    def test_orchestrate_no_args_raises(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        with patch(
            "open_orchestrator.commands.orchestrate_cmds.get_worktree_manager",
            return_value=wt_mgr,
        ):
            result = runner.invoke(cli, ["orchestrate"])
        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "plan file" in combined.lower() or "--resume" in combined.lower()

    def test_orchestrate_status_no_state(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.orchestrator.Orchestrator._state_path", return_value=tmp_path / "missing.json"),
        ):
            result = runner.invoke(cli, ["orchestrate", "--status"])

        assert result.exit_code == 0
        assert "no orchestrator state" in result.output.lower()

    def test_orchestrate_stop_no_running(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.orchestrator.Orchestrator.resume", side_effect=FileNotFoundError),
        ):
            result = runner.invoke(cli, ["orchestrate", "--stop"])

        assert result.exit_code == 0
        assert "no running" in result.output.lower()

    def test_orchestrate_stop_success(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        fake_orch = MagicMock()

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.orchestrator.Orchestrator.resume", return_value=fake_orch),
        ):
            result = runner.invoke(cli, ["orchestrate", "--stop"])

        assert result.exit_code == 0
        fake_orch.stop.assert_called_once()
        assert "stopped" in result.output.lower()

    def test_orchestrate_resume_no_state(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        wt_mgr = _wt_manager(tmp_path)
        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.orchestrator.Orchestrator.resume", side_effect=FileNotFoundError),
        ):
            result = runner.invoke(cli, ["orchestrate", "--resume"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "no orchestrator state" in combined.lower()

    def test_orchestrate_plan_file_starts(self, runner: CliRunner, cli: click.Group, tmp_path: Path) -> None:
        from open_orchestrator.core.orchestrator import TaskPhase

        plan_path = _toml_plan(tmp_path, n_tasks=1)
        wt_mgr = _wt_manager(tmp_path)

        fake_state = MagicMock()
        fake_state.tasks = [MagicMock(status=TaskPhase.SHIPPED)]
        fake_state.feature_branch = "feat/my-feat"

        fake_orch = MagicMock()
        fake_orch.run.return_value = fake_state

        with (
            patch("open_orchestrator.commands.orchestrate_cmds.get_worktree_manager", return_value=wt_mgr),
            patch("open_orchestrator.core.orchestrator.Orchestrator.from_plan", return_value=fake_orch),
        ):
            result = runner.invoke(
                cli,
                ["orchestrate", str(plan_path), "--branch", "feat/my-feat", "--max-concurrent", "2"],
            )

        assert result.exit_code == 0, result.output
        fake_orch.run.assert_called_once()
        assert "shipped" in result.output.lower()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


class TestStatusPrinters:
    def test_print_orchestrator_status_counts(self, capsys: pytest.CaptureFixture) -> None:
        from open_orchestrator.commands.orchestrate_cmds import _print_orchestrator_status

        state = MagicMock()
        state.tasks = [
            MagicMock(status="shipped"),
            MagicMock(status="shipped"),
            MagicMock(status="failed"),
            MagicMock(status="running"),
        ]
        _print_orchestrator_status(state)
        captured = capsys.readouterr()
        assert "2 shipped" in captured.out
        assert "1 failed" in captured.out
        assert "1 running" in captured.out

    def test_show_orchestrator_status_no_state(self, capsys: pytest.CaptureFixture, tmp_path: Path) -> None:
        from open_orchestrator.commands.orchestrate_cmds import _show_orchestrator_status

        with patch(
            "open_orchestrator.core.orchestrator.Orchestrator._state_path",
            return_value=tmp_path / "missing.json",
        ):
            _show_orchestrator_status(str(tmp_path))
        captured = capsys.readouterr()
        assert "no orchestrator state" in captured.out.lower()
