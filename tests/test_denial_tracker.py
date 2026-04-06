"""Tests for the denial tracking system."""

from __future__ import annotations

from pathlib import Path

from open_orchestrator.core.denial_tracker import (
    CONSECUTIVE_THRESHOLD,
    TOTAL_THRESHOLD,
    DenialState,
    DenialTracker,
)


class TestDenialState:
    def test_should_confirm_false(self) -> None:
        state = DenialState("s1", 0, 0, False)
        assert state.should_confirm is False

    def test_should_confirm_true(self) -> None:
        state = DenialState("s1", 3, 3, True)
        assert state.should_confirm is True


class TestSessionManagement:
    def test_start_session(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        state = tracker.start_session("test-session")
        assert state.session_id == "test-session"
        assert state.consecutive_denials == 0
        assert state.total_denials == 0
        assert state.confirmation_mode is False
        tracker.close()

    def test_start_session_resets(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        tracker.record_denial("s1", "ship")
        tracker.record_denial("s1", "ship")
        state = tracker.start_session("s1")
        assert state.consecutive_denials == 0
        assert state.total_denials == 0
        tracker.close()

    def test_get_state_unknown_session(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        state = tracker.get_state("nonexistent")
        assert state.consecutive_denials == 0
        assert state.confirmation_mode is False
        tracker.close()


class TestDenialRecording:
    def test_record_denial_increments(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        state = tracker.record_denial("s1", "merge")
        assert state.consecutive_denials == 1
        assert state.total_denials == 1
        tracker.close()

    def test_consecutive_threshold_triggers_confirmation(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        for i in range(CONSECUTIVE_THRESHOLD):
            state = tracker.record_denial("s1", "ship", f"denied {i}")
        assert state.confirmation_mode is True
        assert state.consecutive_denials == CONSECUTIVE_THRESHOLD
        tracker.close()

    def test_total_threshold_triggers_confirmation(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        state = DenialState("s1", 0, 0, False)
        for i in range(TOTAL_THRESHOLD):
            # Reset consecutive each time to test total independently
            if i % 2 == 0:
                tracker.record_approval("s1")
            state = tracker.record_denial("s1", "action", f"reason {i}")
        assert state.confirmation_mode is True
        tracker.close()

    def test_below_threshold_no_confirmation(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        for i in range(CONSECUTIVE_THRESHOLD - 1):
            state = tracker.record_denial("s1", "ship")
        assert state.confirmation_mode is False
        tracker.close()


class TestApprovalReset:
    def test_approval_resets_consecutive(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        tracker.record_denial("s1", "ship")
        tracker.record_denial("s1", "ship")
        state = tracker.record_approval("s1")
        assert state.consecutive_denials == 0
        assert state.total_denials == 2  # Total not reset
        tracker.close()

    def test_approval_after_denial_prevents_confirmation(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        tracker.record_denial("s1", "ship")
        tracker.record_denial("s1", "ship")
        tracker.record_approval("s1")
        state = tracker.record_denial("s1", "ship")
        assert state.consecutive_denials == 1
        assert state.confirmation_mode is False
        tracker.close()


class TestDenialHistory:
    def test_get_history(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        tracker.record_denial("s1", "ship", "no tests")
        tracker.record_denial("s1", "merge", "conflicts")
        history = tracker.get_denial_history("s1")
        assert len(history) == 2
        assert history[0]["action"] == "merge"  # Most recent first
        assert history[1]["reason"] == "no tests"
        tracker.close()

    def test_get_history_limit(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        for i in range(10):
            tracker.record_denial("s1", f"action-{i}")
        history = tracker.get_denial_history("s1", limit=3)
        assert len(history) == 3
        tracker.close()

    def test_get_history_empty(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        history = tracker.get_denial_history("s1")
        assert history == []
        tracker.close()


class TestResetSession:
    def test_reset_clears_all(self, tmp_path: Path) -> None:
        tracker = DenialTracker(tmp_path / "denials.db")
        tracker.start_session("s1")
        for _ in range(5):
            tracker.record_denial("s1", "ship")
        state = tracker.reset_session("s1")
        assert state.consecutive_denials == 0
        assert state.total_denials == 0
        assert state.confirmation_mode is False
        tracker.close()
