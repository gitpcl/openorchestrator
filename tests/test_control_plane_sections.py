"""Tests for the pure section builders (3-lane control plane)."""

from __future__ import annotations

from datetime import datetime

import pytest

from open_orchestrator.core.control_plane_sections import (
    build_all_sections,
    in_flight_section,
    needs_you_section,
    ready_to_ship_section,
)
from open_orchestrator.models.control_plane import (
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

    def test_blocked_status_surfaces(self) -> None:
        s = _status("wt-blocked", activity_status=AIActivityStatus.BLOCKED, current_task="needs input")
        rows = needs_you_section(statuses=[s])
        assert len(rows) == 1
        assert "blocked" in rows[0].summary
        assert RowAction.ATTACH in rows[0].actions

    def test_error_status_surfaces(self) -> None:
        s = _status("wt-err", activity_status=AIActivityStatus.ERROR)
        rows = needs_you_section(statuses=[s])
        assert len(rows) == 1
        assert rows[0].meta["reason"] == "error"

    def test_conflict_takes_precedence_over_status(self) -> None:
        s = _status("wt", activity_status=AIActivityStatus.BLOCKED)
        rows = needs_you_section(
            statuses=[s],
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

    def test_overlap_hint_rendered(self) -> None:
        rows = ready_to_ship_section([("a", 1, 2)])
        assert "2 overlap" in rows[0].summary

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


class TestBuildAllSections:
    def test_returns_three_keys(self) -> None:
        sections = build_all_sections(statuses=[])
        assert set(sections.keys()) == set(SectionKind)
        assert len(sections) == 3

    def test_populates_each_lane(self) -> None:
        statuses = [
            _status("flying", activity_status=AIActivityStatus.WORKING, updated_at=datetime.now()),
            _status("stuck", activity_status=AIActivityStatus.BLOCKED),
        ]
        sections = build_all_sections(
            statuses=statuses,
            merge_queue=[("ready", 3, 0)],
            conflict_worktrees=["fighting"],
        )
        assert any(r.name == "fighting" for r in sections[SectionKind.NEEDS_YOU])
        assert any(r.name == "stuck" for r in sections[SectionKind.NEEDS_YOU])
        assert any(r.name == "ready" for r in sections[SectionKind.READY_TO_SHIP])
        assert any(r.name == "flying" for r in sections[SectionKind.IN_FLIGHT])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
