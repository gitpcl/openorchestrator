"""Sprint 024: dream/memory/critic ``recent_events`` integration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from open_orchestrator.core.critic import CriticAgent
from open_orchestrator.core.dream import DreamDaemon
from open_orchestrator.core.memory import MemoryManager
from open_orchestrator.models.control_plane import BackgroundEvent
from open_orchestrator.models.memory import MemoryType, TopicFile


def test_dream_recent_events(tmp_path: Path) -> None:
    daemon = DreamDaemon(tmp_path)
    reports_dir = tmp_path / ".owt" / "dream_reports"
    reports_dir.mkdir(parents=True)
    report = {
        "timestamp": datetime.now().isoformat(),
        "findings": [{"category": "memory", "message": "consolidated 5", "worktree": ""}],
        "memory_actions": 5,
        "stale_worktrees": 0,
        "duration_seconds": 0.1,
    }
    (reports_dir / "dream-1.json").write_text(json.dumps(report))
    events = daemon.recent_events(limit=5)
    assert events
    assert events[0].source == "dream"


def test_memory_recent_events(tmp_path: Path) -> None:
    mgr = MemoryManager(tmp_path)
    mgr.ensure_dirs()
    topic = TopicFile(
        name="auth-flow",
        description="how the OAuth flow works",
        memory_type=MemoryType.ARCHITECTURE,
        body="...",
        filename="auth-flow.md",
    )
    mgr.write_topic(topic)
    events = mgr.recent_events(limit=5)
    assert events
    assert events[0].source == "memory"
    assert "architecture" in events[0].summary


def test_critic_records_verdicts(tmp_path: Path) -> None:
    critic = CriticAgent(tmp_path)
    # Manually emit a verdict log entry via the private path, since
    # review_ship() would need a real git tree.
    from open_orchestrator.core.critic import CriticVerdict

    verdict = CriticVerdict(action="ship", target="wt", findings=())
    critic._record_verdict(verdict)  # noqa: SLF001 — intentional white-box

    events = critic.recent_events(limit=5)
    assert events
    assert events[0].source == "critic"
    assert events[0].worktree_name == "wt"


def test_background_event_to_row_is_dismissable() -> None:
    event = BackgroundEvent(
        timestamp=datetime.now(),
        source="dream",
        summary="x",
        worktree_name="wt",
    )
    row = event.to_row()
    from open_orchestrator.models.control_plane import RowAction, SectionKind

    assert row.section == SectionKind.BACKGROUND
    assert RowAction.DISMISS in row.actions
