"""KAIROS-inspired background dream daemon.

After inactivity, the dream daemon wakes up, reviews all worktrees,
consolidates memory, detects stale work, and produces a report.
Runs as a background process with PID file and heartbeat tracking.

Usage:
    daemon = DreamDaemon(repo_root)
    daemon.start()   # Forks to background, writes PID file
    daemon.stop()    # Sends SIGTERM, cleans up
    daemon.status()  # Returns running state + last heartbeat
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 3600  # 1 hour
HEARTBEAT_INTERVAL = 60  # seconds
DEFAULT_WAKE_INTERVAL = 300  # 5 minutes between checks


@dataclass(frozen=True)
class DreamFinding:
    """A single finding from a dream consolidation cycle."""

    category: str
    message: str
    worktree: str = ""


@dataclass(frozen=True)
class DreamReport:
    """Summary of one dream wake cycle."""

    timestamp: str
    findings: tuple[DreamFinding, ...] = ()
    memory_actions: int = 0
    stale_worktrees: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "findings": [{"category": f.category, "message": f.message, "worktree": f.worktree} for f in self.findings],
            "memory_actions": self.memory_actions,
            "stale_worktrees": self.stale_worktrees,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class DreamStatus:
    """Current state of the dream daemon."""

    running: bool = False
    pid: int | None = None
    last_heartbeat: datetime | None = None
    last_report: str | None = None
    enabled: bool = False


class DreamDaemon:
    """Background daemon for proactive worktree review and memory consolidation."""

    def __init__(self, repo_root: Path | None = None) -> None:
        self._root = (repo_root or Path.cwd()).resolve()
        self._owt_dir = self._root / ".owt"
        self._pid_file = self._owt_dir / "dream.pid"
        self._heartbeat_file = self._owt_dir / "dream.heartbeat"
        self._reports_dir = self._owt_dir / "dream_reports"
        self._running = False

    # ── Lifecycle ───────────────────────────────────────────────────

    def start(self, *, foreground: bool = False) -> int:
        """Start the dream daemon.

        Args:
            foreground: If True, run in foreground (for testing). Otherwise fork.

        Returns:
            PID of the daemon process.
        """
        self._owt_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir.mkdir(parents=True, exist_ok=True)

        if self.is_running():
            status = self.status()
            logger.warning("Dream daemon already running (PID %s)", status.pid)
            return status.pid or 0

        if foreground:
            self._pid_file.write_text(str(os.getpid()))
            self._running = True
            self._run_loop()
            return os.getpid()

        # Fork to background
        pid = os.fork()
        if pid > 0:
            # Parent — write PID and return
            self._pid_file.write_text(str(pid))
            logger.info("Dream daemon started (PID %d)", pid)
            return pid

        # Child — run the daemon loop
        os.setsid()
        self._running = True
        self._run_loop()
        os._exit(0)

    def stop(self) -> bool:
        """Stop the dream daemon. Returns True if it was running."""
        if not self._pid_file.exists():
            return False

        try:
            pid = int(self._pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            self._pid_file.unlink(missing_ok=True)
            self._heartbeat_file.unlink(missing_ok=True)
            logger.info("Dream daemon stopped (PID %d)", pid)
            return True
        except (ProcessLookupError, ValueError):
            # Process already gone — clean up stale PID file
            self._pid_file.unlink(missing_ok=True)
            self._heartbeat_file.unlink(missing_ok=True)
            return False
        except OSError as exc:
            logger.warning("Could not stop dream daemon: %s", exc)
            return False

    def is_running(self) -> bool:
        """Check if the daemon is currently running."""
        if not self._pid_file.exists():
            return False
        try:
            pid = int(self._pid_file.read_text().strip())
            os.kill(pid, 0)  # Signal 0 = check existence
            return True
        except (ProcessLookupError, ValueError, OSError):
            # Stale PID file
            self._pid_file.unlink(missing_ok=True)
            return False

    def status(self) -> DreamStatus:
        """Get current daemon status."""
        running = self.is_running()
        pid = None
        last_heartbeat = None
        last_report = None

        if running and self._pid_file.exists():
            try:
                pid = int(self._pid_file.read_text().strip())
            except (ValueError, OSError):
                pass

        if self._heartbeat_file.exists():
            try:
                ts = self._heartbeat_file.read_text().strip()
                last_heartbeat = datetime.fromisoformat(ts)
            except (ValueError, OSError):
                pass

        # Find most recent report
        if self._reports_dir.exists():
            reports = sorted(self._reports_dir.glob("*.json"), reverse=True)
            if reports:
                last_report = reports[0].name

        return DreamStatus(
            running=running,
            pid=pid,
            last_heartbeat=last_heartbeat,
            last_report=last_report,
            enabled=running,
        )

    # ── Heartbeat ───────────────────────────────────────────────────

    def _write_heartbeat(self) -> None:
        """Update heartbeat timestamp."""
        self._heartbeat_file.write_text(datetime.now().isoformat())

    def _last_activity_age(self) -> float:
        """Seconds since last worktree activity (based on status DB)."""
        try:
            from open_orchestrator.core.status import StatusTracker, runtime_status_config

            tracker = StatusTracker(runtime_status_config(self._root))
            statuses = tracker.get_all_statuses()
            if not statuses:
                return float("inf")
            most_recent = max(s.updated_at for s in statuses)
            return (datetime.now() - most_recent).total_seconds()
        except Exception as exc:
            logger.debug("Could not check activity age: %s", exc)
            return float("inf")

    # ── Dream Loop ──────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main daemon loop: heartbeat, check idle, consolidate."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        logger.info("Dream daemon loop started")

        while self._running:
            self._write_heartbeat()

            idle_age = self._last_activity_age()
            if idle_age >= DEFAULT_IDLE_SECONDS:
                report = self._consolidate()
                self._save_report(report)

            time.sleep(DEFAULT_WAKE_INTERVAL)

    def _handle_signal(self, signum: int, frame: object) -> None:
        """Handle SIGTERM for graceful shutdown."""
        logger.info("Dream daemon received signal %d, shutting down", signum)
        self._running = False

    # ── Consolidation ───────────────────────────────────────────────

    def consolidate_now(self) -> DreamReport:
        """Run consolidation immediately (for manual/CLI invocation)."""
        self._owt_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        report = self._consolidate()
        self._save_report(report)
        return report

    def _consolidate(self) -> DreamReport:
        """Run one consolidation cycle."""
        start = time.monotonic()
        findings: list[DreamFinding] = []
        memory_actions = 0

        # 1. Memory consolidation
        try:
            from open_orchestrator.core.memory import MemoryManager

            mgr = MemoryManager(self._root)
            stats = mgr.consolidate()
            memory_actions = sum(stats.values())
            if memory_actions > 0:
                findings.append(
                    DreamFinding(
                        category="memory",
                        message=f"Consolidated memory: {stats}",
                    )
                )
        except Exception as exc:
            logger.debug("Memory consolidation skipped: %s", exc)

        # 2. Detect stale worktrees
        stale_count = 0
        try:
            from open_orchestrator.core.status import StatusTracker, runtime_status_config
            from open_orchestrator.models.status import AIActivityStatus

            tracker = StatusTracker(runtime_status_config(self._root))
            for s in tracker.get_all_statuses():
                if s.activity_status == AIActivityStatus.IDLE:
                    age = (datetime.now() - s.updated_at).total_seconds()
                    if age > 86400:  # 24 hours
                        stale_count += 1
                        findings.append(
                            DreamFinding(
                                category="stale",
                                message=f"Idle for {age / 3600:.0f}h",
                                worktree=s.worktree_name,
                            )
                        )
        except Exception as exc:
            logger.debug("Stale detection skipped: %s", exc)

        elapsed = time.monotonic() - start

        return DreamReport(
            timestamp=datetime.now().isoformat(),
            findings=tuple(findings),
            memory_actions=memory_actions,
            stale_worktrees=stale_count,
            duration_seconds=round(elapsed, 2),
        )

    def _save_report(self, report: DreamReport) -> Path:
        """Save a dream report to .owt/dream_reports/."""
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        filename = f"dream-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        path = self._reports_dir / filename
        path.write_text(json.dumps(report.to_dict(), indent=2))
        logger.info("Dream report saved: %s", path)
        return path

    def list_reports(self, limit: int = 10) -> list[Path]:
        """List recent dream reports."""
        if not self._reports_dir.exists():
            return []
        return sorted(self._reports_dir.glob("*.json"), reverse=True)[:limit]
