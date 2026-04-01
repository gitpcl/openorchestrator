"""Tests for owt config validate and owt config show commands."""

from __future__ import annotations

from click.testing import CliRunner

from open_orchestrator.cli import main


class TestConfigValidate:
    def test_validate_no_config_file(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["config", "validate"])
        assert result.exit_code == 0
        assert "valid" in result.output.lower() or "defaults" in result.output.lower()

    def test_validate_invalid_config(self, cli_runner: CliRunner, tmp_path) -> None:
        cfg = tmp_path / "bad.toml"
        cfg.write_text("[tmux]\nunknown_key = true\n")
        result = cli_runner.invoke(main, ["config", "validate", "--config", str(cfg)])
        assert result.exit_code != 0
        assert "unknown" in result.output.lower() or "invalid" in result.output.lower()

    def test_validate_valid_config(self, cli_runner: CliRunner, tmp_path) -> None:
        cfg = tmp_path / "good.toml"
        cfg.write_text('[tmux]\ndefault_layout = "single"\n')
        result = cli_runner.invoke(main, ["config", "validate", "--config", str(cfg)])
        assert result.exit_code == 0


class TestConfigShow:
    def test_show_defaults(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["config", "show"])
        assert result.exit_code == 0
        assert "worktree" in result.output.lower()
        assert "tmux" in result.output.lower()

    def test_show_contains_sections(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["config", "show"])
        assert "[worktree]" in result.output
        assert "[tmux]" in result.output
