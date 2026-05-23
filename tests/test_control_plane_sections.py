"""Sprint 024: tests for the pure section builders."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from open_orchestrator.core.control_plane_sections import (
    background_rows,
    background_section,
    build_all_sections,
    compute_orchestration_header,
    in_flight_section,
    needs_you_section,
    ready_to_ship_section,
)
from open_orchestrator.core.critic import CriticFinding, CriticVerdict, Severity
from open_orchestrator.models.control_plane import (
    BackgroundEvent,
    RowAction,
    SectionKind,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus


def _status(name: str, **overrides: object) -> WorktreeAIStatus:
    base = dict(
        worktree_name=name,
        worktree_path=f"/tmp/{name}",
        branch=f"feat/{name}",
        activity_status=AIActivityStatus.WORKING,
        ai_tool="claude",
        current_task="doing things",
    )
    base.update(overrides)  # type: ignore[arg-type]
    return WorktreeAIStatus(**base)  # type: ignore[arg-type]


class TestNeedsYouSection:
    def test_conflicts_take_priority(self) -> None:
        rows = needs_you_section(
            statuses=[],
            conflict_worktrees=["wt-conflict"],
        )
        assert len(rows) == 1
        assert rows[0].section == SectionKind.NEEDS_YOU
        assert RowAction.FIX in rows[0].actions
        assert rows[0].meta["reason"] == "conflict"

    def test_critic_blocker_surfaces(self) -> None:
        verdict = CriticVerdict(
            action="ship",
            target="wt-bad",
            findings=(
                CriticFinding(
                    severity=Severity.BLOCKING,
                    category="file-overlap",
                    message="conflicts with other",
                ),
            ),
        )
        rows = needs_you_section(statuses=[], critic_verdicts={"wt-bad": verdict})
        assert len(rows) == 1
        assert rows[0].name == "wt-bad"
        assert RowAction.REVIEW in rows[0].actions

    def test_safe_critic_verdict_is_filtered(self) -> None:
        verdict = CriticVerdict(action="ship", target="wt-ok", findings=())
        rows = needs_you_section(statuses=[], critic_verdicts={"wt-ok": verdict})
        assert rows == []

    def test_blocked_status_surfaces(self) -> None:
        s = _status("wt-blocked", activity_status=AIActivityStatus.BLOCKED, current_task="needs input")
        rows = needs_you_section(statuses=[s])
        assert len(rows) == 1
        assert "blocked" in rows[0].summary

    def test_dedupes_across_sources(self) -> None:
        s = _status("wt", activity_status=AIActivityStatus.BLOCKED)
        verdict = CriticVerdict(
            action="ship",
            target="wt",
            findings=(CriticFinding(severity=Severity.BLOCKING, category="x", message="m"),),
        )
        rows = needs_you_section(
            statuses=[s],
            critic_verdicts={"wt": verdict},
            conflict_worktrees=["wt"],
        )
        # Only one row, prioritized by conflict
        assert len(rows) == 1
        assert rows[0].meta["reason"] == "conflict"


class TestReadyToShipSection:
    def test_queue_order_preserved(self) -> None:
        queue = [("a", 2, 0), ("b", 5, 1), ("c", 1, 0)]
        rows = ready_to_ship_section(queue)
        assert [r.name for r in rows] == ["a", "b", "c"]
        assert all(RowAction.SHIP in r.actions for r in rows)

    def test_position_metadata(self) -> None:
        queue = [("a", 1, 0), ("b", 2, 0)]
        rows = ready_to_ship_section(queue)
        assert rows[0].meta["position"] == "1"
        assert "queued #1/2" in rows[0].summary

    def test_empty_queue_yields_no_rows(self) -> None:
        assert ready_to_ship_section([]) == []


class TestInFlightSection:
    def test_only_working(self) -> None:
        ss = [
            _status("wt1", activity_status=AIActivityStatus.WORKING),
            _status("wt2", activity_status=AIActivityStatus.IDLE),
            _status("wt3", activity_status=AIActivityStatus.BLOCKED),
        ]
        rows = in_flight_section(ss)
        assert [r.name for r in rows] == ["wt1"]

    def test_attach_is_offered(self) -> None:
        ss = [_status("wt", activity_status=AIActivityStatus.WORKING)]
        rows = in_flight_section(ss)
        assert RowAction.ATTACH in rows[0].actions


class TestBackgroundSection:
    def test_merges_and_caps(self) -> None:
        now = datetime.now()
        dream = [BackgroundEvent(timestamp=now - timedelta(minutes=i), source="dream", summary=f"d{i}") for i in range(6)]
        memory = [BackgroundEvent(timestamp=now - timedelta(minutes=i + 10), source="memory", summary=f"m{i}") for i in range(6)]
        merged = background_section(dream_events=dream, memory_events=memory, cap=10)
        assert len(merged) == 10
        # Newest first
        for prev, nxt in zip(merged, merged[1:]):
            assert prev.timestamp >= nxt.timestamp

    def test_to_row_conversion(self) -> None:
        events = [BackgroundEvent(timestamp=datetime.now(), source="critic", summary="ok", worktree_name="wt")]
        rows = background_rows(events)
        assert rows[0].section == SectionKind.BACKGROUND
        assert RowAction.DISMISS in rows[0].actions


class TestBuildAllSections:
    def test_returns_all_four_keys(self) -> None:
        sections = build_all_sections(statuses=[])
        assert set(sections.keys()) == set(SectionKind)


class TestOrchestrationHeader:
    def test_returns_none_when_no_state(self) -> None:
        assert compute_orchestration_header(None) is None

    def test_renders_header_line(self) -> None:
        class _T:
            status = "completed"

        class _R:
            status = "running"

        class _S:
            goal = "ship feature X"
            feature_branch = "feat/X"
            tasks = [_T(), _T(), _R()]

        header = compute_orchestration_header(_S())
        assert header is not None
        assert header.completed == 2
        assert header.running == 1
        assert "feat/X" in header.line


class TestSafeRecentEvents:
    def test_swallows_exceptions(self) -> None:
        class Boom:
            def recent_events(self, limit: int = 5) -> list[BackgroundEvent]:
                raise RuntimeError("nope")

        sections = build_all_sections(statuses=[], dream=Boom(), memory=Boom(), critic=Boom())
        assert sections[SectionKind.BACKGROUND] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
