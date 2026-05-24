"""Unit tests for the batch scheduler (DAG + dependency resolution)."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_orchestrator.core.batch import BatchConfig
from open_orchestrator.core.batch_models import BatchResult, BatchStatus, BatchTask
from open_orchestrator.core.batch_scheduler import (
    BatchScheduler,
    BatchStateStore,
    build_task_index,
    validate_dag,
)
from open_orchestrator.core.status import StatusTracker, runtime_status_config

# ─── pure DAG helpers ───────────────────────────────────────────────────────


class TestValidateDagTopology:
    def test_topological_order_for_chain(self) -> None:
        tasks = [
            BatchTask(description="A", id="a"),
            BatchTask(description="B", id="b", depends_on=["a"]),
            BatchTask(description="C", id="c", depends_on=["b"]),
        ]
        index = build_task_index(tasks)
        order = validate_dag(tasks, index)
        # Must be strictly a -> b -> c
        assert order == [0, 1, 2]

    def test_topological_order_diamond_respects_parents(self) -> None:
        tasks = [
            BatchTask(description="root", id="root"),
            BatchTask(description="left", id="left", depends_on=["root"]),
            BatchTask(description="right", id="right", depends_on=["root"]),
            BatchTask(description="join", id="join", depends_on=["left", "right"]),
        ]
        index = build_task_index(tasks)
        order = validate_dag(tasks, index)

        # Each task must appear after all its parents
        positions = {idx: pos for pos, idx in enumerate(order)}
        assert positions[0] < positions[1]  # root before left
        assert positions[0] < positions[2]  # root before right
        assert positions[1] < positions[3]  # left before join
        assert positions[2] < positions[3]  # right before join

    def test_cycle_detection_raises(self) -> None:
        tasks = [
            BatchTask(description="A", id="a", depends_on=["b"]),
            BatchTask(description="B", id="b", depends_on=["a"]),
        ]
        index = build_task_index(tasks)
        with pytest.raises(ValueError, match="Circular dependency"):
            validate_dag(tasks, index)

    def test_missing_dep_raises(self) -> None:
        tasks = [BatchTask(description="A", id="a", depends_on=["ghost"])]
        index = build_task_index(tasks)
        with pytest.raises(ValueError, match="unknown ID"):
            validate_dag(tasks, index)


# ─── BatchScheduler dependency resolution ───────────────────────────────────


def _make_scheduler(tmp_path: Path, tasks: list[BatchTask]) -> BatchScheduler:
    results = [BatchResult(task=t) for t in tasks]
    tracker = StatusTracker(runtime_status_config(str(tmp_path)))
    return BatchScheduler(tasks, results, tracker)


class TestBatchSchedulerDeps:
    def test_select_ready_skips_blocked_tasks(self, tmp_path: Path) -> None:
        tasks = [
            BatchTask(description="A", id="a"),
            BatchTask(description="B", id="b", depends_on=["a"]),
            BatchTask(description="C", id="c"),
        ]
        scheduler = _make_scheduler(tmp_path, tasks)
        pending = list(scheduler.topo_order)

        # First selectable task should be one without unsatisfied deps (a or c)
        idx = scheduler.select_ready(pending)
        assert idx is not None
        assert scheduler.results[idx].task.id in {"a", "c"}

        # After 'a' completes, 'b' becomes selectable
        for r in scheduler.results:
            if r.task.id == "a":
                r.status = BatchStatus.COMPLETED

        # Drain remaining pending — every selected idx must have its deps met
        while pending:
            picked = scheduler.select_ready(pending)
            if picked is None:
                pytest.fail("Scheduler deadlocked with satisfiable deps")
            assert scheduler.deps_satisfied(picked)
            scheduler.results[picked].status = BatchStatus.COMPLETED

    def test_ship_failed_satisfies_deps_but_not_a_failure(self, tmp_path: Path) -> None:
        tasks = [
            BatchTask(description="A", id="a"),
            BatchTask(description="B", id="b", depends_on=["a"]),
        ]
        scheduler = _make_scheduler(tmp_path, tasks)

        # Work succeeded but merge failed: dependents may still proceed
        scheduler.results[0].status = BatchStatus.FAILED
        scheduler.results[0].ship_failed = True

        assert scheduler.deps_satisfied(1)
        assert not scheduler.deps_failed(1)


# ─── BatchStateStore round-trip ─────────────────────────────────────────────


class TestBatchStateStore:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        config = BatchConfig(
            tasks=[
                BatchTask(description="A", id="a"),
                BatchTask(description="B", id="b", depends_on=["a"]),
            ],
            max_concurrent=2,
        )
        results = [BatchResult(task=t) for t in config.tasks]
        results[0].status = BatchStatus.COMPLETED
        results[0].worktree_name = "wt-a"

        BatchStateStore.save(str(tmp_path), config, results)
        assert BatchStateStore.state_path(str(tmp_path)).exists()

        loaded_config, loaded_results = BatchStateStore.load(str(tmp_path))
        assert loaded_config.max_concurrent == 2
        assert len(loaded_results) == 2
        assert loaded_results[0].status == BatchStatus.COMPLETED
        assert loaded_results[0].worktree_name == "wt-a"
        assert loaded_results[1].task.depends_on == ["a"]

        BatchStateStore.clear(str(tmp_path))
        assert not BatchStateStore.state_path(str(tmp_path)).exists()
