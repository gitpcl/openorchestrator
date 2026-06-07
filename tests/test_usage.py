"""Tests for the local usage signal: the ``StatusTracker`` wrapper methods
and the ``owt usage`` command.

The conn-level store (``record_usage`` / ``usage_counts`` windowing) is
covered in ``test_status_schema.py``; here we cover the public tracker
surface and the CLI command that the cockpit reposition added.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.status_schema import StatusConfig


def _tracker(tmp_path: Path) -> StatusTracker:
    return StatusTracker(StatusConfig(storage_path=tmp_path / "status.db"))


# ── tracker wrapper (store-level) ──────────────────────────────────────


class TestTrackerUsage:
    def test_record_and_count_roundtrip(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.record_usage("control_plane")
        tracker.record_usage("new")
        tracker.record_usage("new")
        assert tracker.usage_counts(days=30) == {"control_plane": 1, "new": 2}

    def test_zero_state(self, tmp_path: Path) -> None:
        assert _tracker(tmp_path).usage_counts(days=30) == {}

    def test_unknown_event_kind_tolerated(self, tmp_path: Path) -> None:
        """Any string event is accepted — the store does not validate a
        fixed vocabulary, so a future event kind never crashes recording."""
        tracker = _tracker(tmp_path)
        tracker.record_usage("some_future_kind")
        assert tracker.usage_counts(days=30) == {"some_future_kind": 1}

    def test_window_filters_by_days(self, tmp_path: Path) -> None:
        tracker = _tracker(tmp_path)
        tracker.record_usage("new")
        # A zero-day window excludes everything recorded "today" or earlier.
        assert tracker.usage_counts(days=0) == {}
        assert tracker.usage_counts(days=30) == {"new": 1}

    def test_record_usage_never_raises(self, tmp_path: Path) -> None:
        """record_usage is failure-isolated: a closed connection must not
        propagate (the cockpit launch can never be blocked by telemetry)."""
        tracker = _tracker(tmp_path)
        tracker.close()
        tracker.record_usage("control_plane")  # no exception
        assert tracker.usage_counts(days=30) == {}


# ── owt usage command ──────────────────────────────────────────────────


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "status.db"
    monkeypatch.setenv("OWT_DB_PATH", str(db))
    return db


class TestUsageCommand:
    def test_zero_state_text(self, isolated_db: Path) -> None:
        result = CliRunner().invoke(main, ["usage"])
        assert result.exit_code == 0
        assert "control-plane launches : 0" in result.output
        assert "worktrees started : 0" in result.output

    def test_nonzero_after_seeded_events(self, isolated_db: Path) -> None:
        tracker = StatusTracker(StatusConfig(storage_path=isolated_db))
        tracker.record_usage("control_plane")
        tracker.record_usage("control_plane")
        tracker.record_usage("new")
        tracker.close()

        result = CliRunner().invoke(main, ["usage"])
        assert result.exit_code == 0
        assert "control-plane launches : 2" in result.output
        assert "worktrees started : 1" in result.output

    def test_json_shape(self, isolated_db: Path) -> None:
        tracker = StatusTracker(StatusConfig(storage_path=isolated_db))
        tracker.record_usage("workflow")
        tracker.close()

        result = CliRunner().invoke(main, ["usage", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["days"] == 30
        assert payload["counts"] == {"workflow": 1}

    def test_days_window_flag(self, isolated_db: Path) -> None:
        tracker = StatusTracker(StatusConfig(storage_path=isolated_db))
        tracker.record_usage("new")
        tracker.close()

        result = CliRunner().invoke(main, ["usage", "--days", "0", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["days"] == 0
        assert payload["counts"] == {}


# ── control_plane launch recording (dead-metric fix) ───────────────────


class TestControlPlaneLaunchRecord:
    def test_no_subcommand_records_one_launch(self, isolated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`owt` with no subcommand records exactly one control_plane event
        and launches the cockpit (TUI .run is stubbed out)."""
        launched: list[bool] = []
        monkeypatch.setattr(
            "open_orchestrator.core.control_plane_view.ControlPlaneApp.run",
            lambda self: launched.append(True),
        )
        result = CliRunner().invoke(main, [])
        assert result.exit_code == 0
        assert launched == [True]

        counts = StatusTracker(StatusConfig(storage_path=isolated_db)).usage_counts(days=30)
        assert counts.get("control_plane") == 1

    def test_subcommand_does_not_record_launch(self, isolated_db: Path) -> None:
        """`owt <subcommand>` must NOT count toward control-plane launches,
        keeping the usage gauge honest."""
        result = CliRunner().invoke(main, ["usage", "--json"])
        assert result.exit_code == 0

        counts = StatusTracker(StatusConfig(storage_path=isolated_db)).usage_counts(days=30)
        assert "control_plane" not in counts
