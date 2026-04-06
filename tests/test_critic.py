"""Tests for the critic pattern: CriticAgent, CriticVerdict, and CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.critic import CriticAgent, CriticFinding, CriticVerdict, Severity


# ── CriticFinding Tests ──────────────────────────────────────────────


class TestCriticFinding:
    def test_blocking_finding(self) -> None:
        f = CriticFinding(
            severity=Severity.BLOCKING,
            category="file-overlap",
            message="auth.py also modified in feature-auth",
        )
        assert f.severity == Severity.BLOCKING
        assert "auth.py" in f.message

    def test_warning_finding_with_details(self) -> None:
        f = CriticFinding(
            severity=Severity.WARNING,
            category="uncommitted-changes",
            message="3 uncommitted files",
            details="file1.py\nfile2.py\nfile3.py",
        )
        assert f.details.count("\n") == 2


# ── CriticVerdict Tests ──────────────────────────────────────────────


class TestCriticVerdict:
    def test_empty_verdict_is_safe(self) -> None:
        v = CriticVerdict(action="ship", target="my-feature")
        assert v.is_safe is True
        assert v.blocking_count == 0
        assert v.warning_count == 0
        assert v.info_count == 0

    def test_blocking_verdict_is_not_safe(self) -> None:
        v = CriticVerdict(
            action="merge",
            target="my-feature",
            findings=(CriticFinding(Severity.BLOCKING, "overlap", "Conflict detected"),),
        )
        assert v.is_safe is False
        assert v.blocking_count == 1

    def test_warning_only_is_safe(self) -> None:
        v = CriticVerdict(
            action="ship",
            target="my-feature",
            findings=(CriticFinding(Severity.WARNING, "uncommitted", "3 uncommitted files"),),
        )
        assert v.is_safe is True
        assert v.warning_count == 1

    def test_mixed_findings(self) -> None:
        v = CriticVerdict(
            action="ship",
            target="feat",
            findings=(
                CriticFinding(Severity.INFO, "commits", "5 commits ahead"),
                CriticFinding(Severity.WARNING, "uncommitted", "2 files"),
                CriticFinding(Severity.BLOCKING, "overlap", "conflict"),
            ),
        )
        assert v.is_safe is False
        assert v.blocking_count == 1
        assert v.warning_count == 1
        assert v.info_count == 1

    def test_summary_safe(self) -> None:
        v = CriticVerdict(action="ship", target="my-feature")
        assert "Safe" in v.summary

    def test_summary_blocked(self) -> None:
        v = CriticVerdict(
            action="merge",
            target="feat",
            findings=(CriticFinding(Severity.BLOCKING, "overlap", "conflict"),),
        )
        assert "BLOCKED" in v.summary


# ── CriticAgent Tests ────────────────────────────────────────────────


class TestCriticAgentUncommitted:
    @patch("open_orchestrator.core.merge.MergeManager")
    def test_uncommitted_changes_warning(self, mock_merge_cls: MagicMock) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = ["file1.py", "file2.py"]
        mock_mgr.check_file_overlaps.return_value = {}
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 3
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/test")
            mock_wt_cls.return_value = mock_wt

            critic = CriticAgent()
            verdict = critic.review_ship("test-worktree")

        assert verdict.is_safe is True
        assert verdict.warning_count >= 1
        warnings = [f for f in verdict.findings if f.category == "uncommitted-changes"]
        assert len(warnings) == 1

    @patch("open_orchestrator.core.merge.MergeManager")
    def test_no_uncommitted_changes(self, mock_merge_cls: MagicMock) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = []
        mock_mgr.check_file_overlaps.return_value = {}
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 5
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/test")
            mock_wt_cls.return_value = mock_wt

            critic = CriticAgent()
            verdict = critic.review_ship("clean-worktree")

        uncommitted = [f for f in verdict.findings if f.category == "uncommitted-changes"]
        assert len(uncommitted) == 0


class TestCriticAgentOverlaps:
    @patch("open_orchestrator.core.merge.MergeManager")
    def test_file_overlaps_blocking(self, mock_merge_cls: MagicMock) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = []
        mock_mgr.check_file_overlaps.return_value = {
            "src/auth.py": ["feature-auth"],
            "src/config.py": ["feature-config"],
        }
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 3
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/test")
            mock_wt_cls.return_value = mock_wt

            critic = CriticAgent()
            verdict = critic.review_merge("test-worktree")

        assert verdict.is_safe is False
        assert verdict.blocking_count == 2
        overlaps = [f for f in verdict.findings if f.category == "file-overlap"]
        assert len(overlaps) == 2


class TestCriticAgentEmptyBranch:
    @patch("open_orchestrator.core.merge.MergeManager")
    def test_empty_branch_blocking(self, mock_merge_cls: MagicMock) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = []
        mock_mgr.check_file_overlaps.return_value = {}
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 0
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/empty")
            mock_wt_cls.return_value = mock_wt

            critic = CriticAgent()
            verdict = critic.review_ship("empty-feature")

        assert verdict.is_safe is False
        empty = [f for f in verdict.findings if f.category == "empty-branch"]
        assert len(empty) == 1


class TestCriticAgentDelete:
    @patch("open_orchestrator.core.merge.MergeManager")
    def test_delete_with_unmerged_commits(self, mock_merge_cls: MagicMock) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = []
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 5
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/old")
            mock_wt_cls.return_value = mock_wt

            critic = CriticAgent()
            verdict = critic.review_delete("old-feature")

        warnings = [f for f in verdict.findings if f.category == "unmerged-commits"]
        assert len(warnings) == 1
        assert verdict.is_safe is True  # warnings don't block


class TestCriticAgentReviewAction:
    @patch("open_orchestrator.core.merge.MergeManager")
    def test_review_action_dispatch(self, mock_merge_cls: MagicMock) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = []
        mock_mgr.check_file_overlaps.return_value = {}
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 3
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/test")
            mock_wt_cls.return_value = mock_wt

            critic = CriticAgent()
            verdict = critic.review_action("ship", "test")

        assert verdict.action == "ship"


# ── CLI Command Tests ────────────────────────────────────────────────


class TestCriticCLI:
    @patch("open_orchestrator.core.merge.MergeManager")
    def test_critic_safe_exit_0(self, mock_merge_cls: MagicMock, cli_runner: CliRunner) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = []
        mock_mgr.check_file_overlaps.return_value = {}
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 3
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/test")
            mock_wt_cls.return_value = mock_wt

            result = cli_runner.invoke(main, ["critic", "ship", "my-feature"])

        assert result.exit_code == 0
        assert "Safe" in result.output or "info" in result.output.lower()

    @patch("open_orchestrator.core.merge.MergeManager")
    def test_critic_blocked_exit_1(self, mock_merge_cls: MagicMock, cli_runner: CliRunner) -> None:
        mock_mgr = MagicMock()
        mock_mgr.check_uncommitted_changes.return_value = []
        mock_mgr.check_file_overlaps.return_value = {"auth.py": ["other-wt"]}
        mock_mgr.get_base_branch.return_value = "main"
        mock_mgr.count_commits_ahead.return_value = 3
        mock_merge_cls.return_value = mock_mgr

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock(branch="feature/test")
            mock_wt_cls.return_value = mock_wt

            result = cli_runner.invoke(main, ["critic", "merge", "my-feature"])

        assert result.exit_code == 1

    def test_critic_invalid_action(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(main, ["critic", "invalid", "my-feature"])
        assert result.exit_code != 0


# ── Config Integration ───────────────────────────────────────────────


class TestCriticConfig:
    def test_critic_enabled_default(self) -> None:
        from open_orchestrator.config import Config

        config = Config()
        assert config.critic_enabled is True

    def test_critic_disabled(self) -> None:
        from open_orchestrator.config import Config

        config = Config(critic_enabled=False)
        assert config.critic_enabled is False
