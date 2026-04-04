"""Smoke tests: verify all CLI commands import and respond to --help.

These tests catch import errors, circular dependencies, and broken
command registration without requiring real git repos or tmux.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from open_orchestrator.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCLIHelp:
    """Verify all commands respond to --help without import errors."""

    def test_main_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output

    @pytest.mark.parametrize(
        "command",
        [
            ["new", "--help"],
            ["list", "--help"],
            ["switch", "--help"],
            ["delete", "--help"],
            ["send", "--help"],
            ["wait", "--help"],
            ["note", "--help"],
            ["merge", "--help"],
            ["ship", "--help"],
            ["queue", "--help"],
            ["plan", "--help"],
            ["batch", "--help"],
            ["orchestrate", "--help"],
            ["sync", "--help"],
            ["cleanup", "--help"],
            ["version"],
            ["doctor", "--help"],
            ["config", "validate", "--help"],
            ["config", "show", "--help"],
            ["db", "purge", "--help"],
            ["db", "vacuum", "--help"],
            ["db", "health", "--help"],
        ],
    )
    def test_command_help(self, runner: CliRunner, command: list[str]) -> None:
        """Each command should respond to --help with exit code 0."""
        result = runner.invoke(main, command)
        assert result.exit_code == 0, f"Command {command} failed: {result.output}"


class TestCLICommandRegistration:
    """Verify all expected commands are registered."""

    def test_all_commands_registered(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        output = result.output

        expected_commands = [
            "new",
            "list",
            "switch",
            "delete",
            "send",
            "wait",
            "note",
            "merge",
            "ship",
            "queue",
            "plan",
            "batch",
            "orchestrate",
            "sync",
            "cleanup",
            "version",
            "doctor",
            "config",
            "db",
        ]
        for cmd in expected_commands:
            assert cmd in output, f"Command '{cmd}' not found in --help output"

    def test_config_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["config", "--help"])
        assert result.exit_code == 0
        assert "validate" in result.output
        assert "show" in result.output

    def test_db_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["db", "--help"])
        assert result.exit_code == 0
        assert "purge" in result.output
        assert "vacuum" in result.output
        assert "health" in result.output


class TestCLIModuleImports:
    """Verify core modules import without errors."""

    def test_import_switchboard(self) -> None:
        from open_orchestrator.core.switchboard import SwitchboardApp

        assert SwitchboardApp is not None

    def test_import_switchboard_cards(self) -> None:
        from open_orchestrator.core.switchboard_cards import Card, _build_cards

        assert Card is not None
        assert _build_cards is not None

    def test_import_switchboard_modals(self) -> None:
        from open_orchestrator.core.switchboard_modals import ConfirmModal, InputModal

        assert InputModal is not None
        assert ConfirmModal is not None

    def test_import_batch_models(self) -> None:
        from open_orchestrator.core.batch_models import BatchConfig, BatchStatus, BatchTask

        assert BatchStatus is not None
        assert BatchTask is not None
        assert BatchConfig is not None

    def test_import_environment_claude_md(self) -> None:
        from open_orchestrator.core.environment_claude_md import (
            build_claude_md_context,
            inject_shared_notes,
            sync_claude_md,
        )

        assert sync_claude_md is not None
        assert inject_shared_notes is not None
        assert build_claude_md_context is not None

    def test_import_tool_registry(self) -> None:
        from open_orchestrator.core.tool_registry import CustomTool, ToolRegistry, get_registry

        assert ToolRegistry is not None
        assert CustomTool is not None
        reg = get_registry()
        assert "claude" in reg.list_names()

    def test_backward_compat_environment_reexports(self) -> None:
        """CLAUDE.md functions should still be importable from environment."""
        from open_orchestrator.core.environment import (
            inject_coordination_context,
            inject_dag_context,
            inject_shared_notes,
            sync_claude_md,
        )

        assert sync_claude_md is not None
        assert inject_shared_notes is not None
        assert inject_dag_context is not None
        assert inject_coordination_context is not None

    def test_backward_compat_switchboard_reexports(self) -> None:
        """Card functions should still be importable from switchboard."""
        from open_orchestrator.core.switchboard import (
            HOOK_CAPABLE_TOOLS,
            _build_cards,
            _detect_pane_status,
            launch_switchboard,
        )

        assert _build_cards is not None
        assert _detect_pane_status is not None
        assert HOOK_CAPABLE_TOOLS is not None
        assert launch_switchboard is not None

    def test_backward_compat_batch_reexports(self) -> None:
        """Batch models should still be importable from batch."""
        from open_orchestrator.core.batch import (
            BatchConfig,
            BatchResult,
            BatchStatus,
            BatchTask,
        )

        assert BatchStatus is not None
        assert BatchTask is not None
        assert BatchResult is not None
        assert BatchConfig is not None
