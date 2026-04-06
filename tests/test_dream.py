"""Tests for the dream daemon: DreamDaemon, DreamReport, and CLI commands."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.dream import (
    DEFAULT_IDLE_SECONDS,
    DreamDaemon,
    DreamFinding,
    DreamReport,
    DreamStatus,
)


# ── Model Tests ──────────────────────────────────────────────────────


class TestDreamFinding:
    def test_fields(self) -> None:
        f = DreamFinding(category="stale", message="Idle for 48h", worktree="old-feature")
        assert f.category == "stale"
        assert f.worktree == "old-feature"

    def test_default_worktree(self) -> None:
        f = DreamFinding(category="memory", message="Consolidated 3 entries")
        assert f.worktree == ""


class TestDreamReport:
    def test_to_dict(self) -> None:
        report = DreamReport(
            timestamp="2026-04-05T22:00:00",
            findings=(DreamFinding("memory", "Cleaned 2 entries"),),
            memory_actions=2,
            stale_worktrees=0,
            duration_seconds=1.5,
        )
        d = report.to_dict()
        assert d["timestamp"] == "2026-04-05T22:00:00"
        assert len(d["findings"]) == 1
        assert d["memory_actions"] == 2
        assert d["duration_seconds"] == 1.5

    def test_empty_report(self) -> None:
        report = DreamReport(timestamp="2026-04-05T22:00:00")
        d = report.to_dict()
        assert d["findings"] == []
        assert d["memory_actions"] == 0


class TestDreamStatus:
    def test_defaults(self) -> None:
        status = DreamStatus()
        assert status.running is False
        assert status.pid is None
        assert status.enabled is False


# ── DreamDaemon Lifecycle Tests ──────────────────────────────────────


class TestDreamDaemonLifecycle:
    def test_is_running_no_pid_file(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        assert daemon.is_running() is False

    def test_is_running_stale_pid(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        owt_dir = tmp_path / ".owt"
        owt_dir.mkdir(parents=True)
        (owt_dir / "dream.pid").write_text("99999999")  # Almost certainly dead
        assert daemon.is_running() is False
        # Stale PID file should be cleaned up
        assert not (owt_dir / "dream.pid").exists()

    def test_status_not_running(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        status = daemon.status()
        assert status.running is False
        assert status.pid is None

    def test_status_with_heartbeat(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        owt_dir = tmp_path / ".owt"
        owt_dir.mkdir(parents=True)
        now = datetime.now()
        (owt_dir / "dream.heartbeat").write_text(now.isoformat())
        status = daemon.status()
        assert status.last_heartbeat is not None

    def test_stop_not_running(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        assert daemon.stop() is False

    def test_stop_stale_pid(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        owt_dir = tmp_path / ".owt"
        owt_dir.mkdir(parents=True)
        (owt_dir / "dream.pid").write_text("99999999")
        (owt_dir / "dream.heartbeat").write_text(datetime.now().isoformat())
        assert daemon.stop() is False
        assert not (owt_dir / "dream.pid").exists()


# ── Consolidation Tests ──────────────────────────────────────────────


class TestConsolidation:
    def test_consolidate_now_creates_report(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        report = daemon.consolidate_now()
        assert report.timestamp
        assert report.duration_seconds >= 0

        # Report file should exist
        reports = list((tmp_path / ".owt" / "dream_reports").glob("*.json"))
        assert len(reports) == 1

    def test_consolidate_with_memory(self, tmp_path: Path) -> None:
        # Set up memory directory
        memory_dir = tmp_path / ".owt" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text("# Memory Index\n- [Ghost](ghost.md) — orphan\n")

        daemon = DreamDaemon(tmp_path)
        report = daemon.consolidate_now()
        # Should detect orphaned entry
        assert report.memory_actions >= 1

    def test_consolidate_stale_detection(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)

        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker_cls:
            mock_tracker = MagicMock()
            stale_status = MagicMock()
            stale_status.activity_status = MagicMock()
            stale_status.activity_status.__eq__ = lambda self, other: str(other) == "idle"
            # Use string comparison since we mock the enum
            from open_orchestrator.models.status import AIActivityStatus

            stale_status.activity_status = AIActivityStatus.IDLE
            stale_status.updated_at = datetime.now() - timedelta(hours=48)
            stale_status.worktree_name = "old-feature"
            mock_tracker.get_all_statuses.return_value = [stale_status]
            mock_tracker_cls.return_value = mock_tracker

            report = daemon._consolidate()

        assert report.stale_worktrees == 1
        stale_findings = [f for f in report.findings if f.category == "stale"]
        assert len(stale_findings) == 1

    def test_list_reports_empty(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        assert daemon.list_reports() == []

    def test_list_reports_ordered(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        reports_dir = tmp_path / ".owt" / "dream_reports"
        reports_dir.mkdir(parents=True)
        for i in range(3):
            (reports_dir / f"dream-2026040{i}-120000.json").write_text("{}")
        reports = daemon.list_reports()
        assert len(reports) == 3
        # Most recent first
        assert "0402" in reports[0].name


# ── Heartbeat Tests ──────────────────────────────────────────────────


class TestHeartbeat:
    def test_write_heartbeat(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        (tmp_path / ".owt").mkdir(parents=True)
        daemon._write_heartbeat()
        hb_file = tmp_path / ".owt" / "dream.heartbeat"
        assert hb_file.exists()
        ts = datetime.fromisoformat(hb_file.read_text().strip())
        assert (datetime.now() - ts).total_seconds() < 5

    def test_last_activity_age_no_statuses(self, tmp_path: Path) -> None:
        daemon = DreamDaemon(tmp_path)
        with patch("open_orchestrator.core.status.StatusTracker") as mock_cls:
            mock_tracker = MagicMock()
            mock_tracker.get_all_statuses.return_value = []
            mock_cls.return_value = mock_tracker
            age = daemon._last_activity_age()
        assert age == float("inf")


# ── CLI Command Tests ────────────────────────────────────────────────


class TestDreamCLI:
    def test_dream_status_not_running(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(main, ["dream", "status"])
        assert result.exit_code == 0
        assert "Not running" in result.output

    def test_dream_disable_not_running(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(main, ["dream", "disable"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower()

    def test_dream_consolidate(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(main, ["dream", "consolidate"])
        assert result.exit_code == 0

    def test_dream_reports_empty(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(main, ["dream", "reports"])
        assert result.exit_code == 0
        assert "No dream reports" in result.output

    def test_dream_reports_with_data(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        reports_dir = tmp_path / ".owt" / "dream_reports"
        reports_dir.mkdir(parents=True)
        (reports_dir / "dream-20260405-220000.json").write_text(
            json.dumps({"findings": [{"category": "test", "message": "ok", "worktree": ""}], "duration_seconds": 0.5})
        )
        result = cli_runner.invoke(main, ["dream", "reports"])
        assert result.exit_code == 0
        assert "1 finding" in result.output


# ── Config Integration ───────────────────────────────────────────────


class TestDreamConfig:
    def test_dream_config_defaults(self) -> None:
        from open_orchestrator.config import Config

        config = Config()
        assert config.dream_enabled is False
        assert config.dream_idle_seconds == 3600

    def test_dream_config_custom(self) -> None:
        from open_orchestrator.config import Config

        config = Config(dream_enabled=True, dream_idle_seconds=1800)
        assert config.dream_enabled is True
        assert config.dream_idle_seconds == 1800
