"""Tests for DAG-based batch execution (owt plan)."""

from pathlib import Path

import pytest
import toml

from open_orchestrator.core.batch import (
    BatchConfig,
    BatchStatus,
    BatchTask,
    _build_task_index,
    _extract_toml,
    _validate_dag,
    load_batch_config,
)

# ─── _build_task_index ─────────────────────────────────────────────────────


class TestBuildTaskIndex:
    def test_auto_assigns_ids(self):
        tasks = [BatchTask(description="A"), BatchTask(description="B")]
        index = _build_task_index(tasks)
        assert tasks[0].id == "task-0"
        assert tasks[1].id == "task-1"
        assert index == {"task-0": 0, "task-1": 1}

    def test_preserves_explicit_ids(self):
        tasks = [
            BatchTask(description="A", id="alpha"),
            BatchTask(description="B", id="beta"),
        ]
        index = _build_task_index(tasks)
        assert index == {"alpha": 0, "beta": 1}

    def test_mixed_explicit_and_auto(self):
        tasks = [
            BatchTask(description="A", id="alpha"),
            BatchTask(description="B"),
        ]
        index = _build_task_index(tasks)
        assert index == {"alpha": 0, "task-1": 1}

    def test_duplicate_ids_raises(self):
        tasks = [
            BatchTask(description="A", id="same"),
            BatchTask(description="B", id="same"),
        ]
        with pytest.raises(ValueError, match="Duplicate task ID"):
            _build_task_index(tasks)


# ─── _validate_dag ─────────────────────────────────────────────────────────


class TestValidateDag:
    def test_no_deps_is_valid(self):
        tasks = [
            BatchTask(description="A", id="a"),
            BatchTask(description="B", id="b"),
        ]
        index = {"a": 0, "b": 1}
        order = _validate_dag(tasks, index)
        assert set(order) == {0, 1}

    def test_linear_chain(self):
        tasks = [
            BatchTask(description="A", id="a"),
            BatchTask(description="B", id="b", depends_on=["a"]),
            BatchTask(description="C", id="c", depends_on=["b"]),
        ]
        index = {"a": 0, "b": 1, "c": 2}
        order = _validate_dag(tasks, index)
        assert order == [0, 1, 2]

    def test_diamond_dag(self):
        tasks = [
            BatchTask(description="root", id="root"),
            BatchTask(description="left", id="left", depends_on=["root"]),
            BatchTask(description="right", id="right", depends_on=["root"]),
            BatchTask(description="merge", id="merge", depends_on=["left", "right"]),
        ]
        index = {"root": 0, "left": 1, "right": 2, "merge": 3}
        order = _validate_dag(tasks, index)
        # root must come first, merge must come last
        assert order[0] == 0
        assert order[-1] == 3
        assert set(order) == {0, 1, 2, 3}

    def test_circular_deps_raises(self):
        tasks = [
            BatchTask(description="A", id="a", depends_on=["b"]),
            BatchTask(description="B", id="b", depends_on=["a"]),
        ]
        index = {"a": 0, "b": 1}
        with pytest.raises(ValueError, match="Circular dependency"):
            _validate_dag(tasks, index)

    def test_self_referencing_cycle(self):
        tasks = [
            BatchTask(description="A", id="a", depends_on=["a"]),
        ]
        index = {"a": 0}
        with pytest.raises(ValueError, match="Circular dependency"):
            _validate_dag(tasks, index)

    def test_missing_dep_raises(self):
        tasks = [
            BatchTask(description="A", id="a", depends_on=["nonexistent"]),
        ]
        index = {"a": 0}
        with pytest.raises(ValueError, match="unknown ID"):
            _validate_dag(tasks, index)


# ─── _extract_toml ─────────────────────────────────────────────────────────


class TestExtractToml:
    def test_extracts_from_fenced_block(self):
        text = """Here is your plan:

```toml
[batch]
max_concurrent = 3

[[tasks]]
id = "test"
description = "Test task"
depends_on = []
```

Let me know if you want changes.
"""
        result = _extract_toml(text)
        parsed = toml.loads(result)
        assert parsed["batch"]["max_concurrent"] == 3
        assert len(parsed["tasks"]) == 1

    def test_extracts_unfenced_with_batch_header(self):
        text = """[batch]
max_concurrent = 2

[[tasks]]
id = "a"
description = "Task A"
depends_on = []
"""
        result = _extract_toml(text)
        parsed = toml.loads(result)
        assert parsed["batch"]["max_concurrent"] == 2

    def test_extracts_unfenced_tasks_only(self):
        text = """[[tasks]]
id = "a"
description = "Task A"
depends_on = []
"""
        result = _extract_toml(text)
        parsed = toml.loads(result)
        assert len(parsed["tasks"]) == 1

    def test_no_toml_raises(self):
        text = "This response has no TOML content at all."
        with pytest.raises(ValueError, match="No TOML block found"):
            _extract_toml(text)


# ─── load_batch_config with DAG fields ─────────────────────────────────────


class TestLoadBatchConfigDag:
    def test_loads_dag_fields(self, tmp_path: Path):
        toml_content = """
[batch]
max_concurrent = 2

[[tasks]]
id = "models"
description = "Create models"
depends_on = []

[[tasks]]
id = "api"
description = "Create API"
depends_on = ["models"]
"""
        toml_file = tmp_path / "test.toml"
        toml_file.write_text(toml_content)

        config = load_batch_config(str(toml_file))
        assert len(config.tasks) == 2
        assert config.tasks[0].id == "models"
        assert config.tasks[0].depends_on == []
        assert config.tasks[1].id == "api"
        assert config.tasks[1].depends_on == ["models"]

    def test_backward_compat_no_dag_fields(self, tmp_path: Path):
        toml_content = """
[batch]
max_concurrent = 3

[[tasks]]
description = "Old-style task A"

[[tasks]]
description = "Old-style task B"
"""
        toml_file = tmp_path / "test.toml"
        toml_file.write_text(toml_content)

        config = load_batch_config(str(toml_file))
        assert len(config.tasks) == 2
        assert config.tasks[0].id is None
        assert config.tasks[0].depends_on == []
        assert config.tasks[1].id is None
        assert config.tasks[1].depends_on == []


# ─── BatchRunner DAG scheduling ────────────────────────────────────────────


class TestBatchRunnerDag:
    def test_deps_satisfied(self):
        from open_orchestrator.core.batch import BatchRunner

        config = BatchConfig(
            tasks=[
                BatchTask(description="A", id="a"),
                BatchTask(description="B", id="b", depends_on=["a"]),
            ]
        )
        runner = BatchRunner(config, "/tmp/fake")
        # Before A completes, B's deps are not satisfied
        assert not runner._deps_satisfied(1)
        # After A completes, B's deps are satisfied
        runner.results[0].status = BatchStatus.COMPLETED
        assert runner._deps_satisfied(1)

    def test_deps_satisfied_shipped(self):
        from open_orchestrator.core.batch import BatchRunner

        config = BatchConfig(
            tasks=[
                BatchTask(description="A", id="a"),
                BatchTask(description="B", id="b", depends_on=["a"]),
            ]
        )
        runner = BatchRunner(config, "/tmp/fake")
        runner.results[0].status = BatchStatus.SHIPPED
        assert runner._deps_satisfied(1)

    def test_deps_failed_detection(self):
        from open_orchestrator.core.batch import BatchRunner

        config = BatchConfig(
            tasks=[
                BatchTask(description="A", id="a"),
                BatchTask(description="B", id="b", depends_on=["a"]),
            ]
        )
        runner = BatchRunner(config, "/tmp/fake")
        runner.results[0].status = BatchStatus.FAILED
        assert runner._deps_failed(1)

    def test_select_ready_respects_deps(self):
        from open_orchestrator.core.batch import BatchRunner

        config = BatchConfig(
            tasks=[
                BatchTask(description="A", id="a"),
                BatchTask(description="B", id="b", depends_on=["a"]),
                BatchTask(description="C", id="c"),
            ]
        )
        runner = BatchRunner(config, "/tmp/fake")

        # Both A and C have no deps; B depends on A
        pending = list(runner._topo_order)
        idx = runner._select_ready(pending)
        assert idx is not None
        # The selected task should be one without unsatisfied deps
        selected_task = runner.results[idx].task
        assert selected_task.id in ("a", "c")

    def test_topo_order_flat_tasks(self):
        """Old-style tasks with no deps get topological order (all in parallel)."""
        from open_orchestrator.core.batch import BatchRunner

        config = BatchConfig(
            tasks=[
                BatchTask(description="A"),
                BatchTask(description="B"),
                BatchTask(description="C"),
            ]
        )
        runner = BatchRunner(config, "/tmp/fake")
        assert set(runner._topo_order) == {0, 1, 2}
        assert not runner._has_deps

    def test_dag_progress_metadata(self):
        from open_orchestrator.core.batch import BatchRunner

        config = BatchConfig(
            tasks=[
                BatchTask(description="A", id="a"),
                BatchTask(description="B", id="b", depends_on=["a"]),
            ]
        )
        runner = BatchRunner(config, "/tmp/fake")
        runner._update_dag_progress(1, 2)

        row = runner.tracker._conn.execute("SELECT value FROM metadata WHERE key = 'dag_progress'").fetchone()
        assert row is not None
        assert row["value"] == "1/2"

        runner._clear_dag_progress()
        row = runner.tracker._conn.execute("SELECT value FROM metadata WHERE key = 'dag_progress'").fetchone()
        assert row is None


# ─── inject_dag_context ────────────────────────────────────────────────────


class TestInjectDagContext:
    def test_injects_context_into_claude_md(self, tmp_path: Path):
        from open_orchestrator.core.environment import inject_dag_context

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        claude_md = claude_dir / "CLAUDE.md"
        claude_md.write_text("# Project\n\nExisting content.\n")

        summaries = [
            "**models** (feat/models):\nabc1234 Create User model",
            "**jwt** (feat/jwt):\ndef5678 Add JWT validation",
        ]

        inject_dag_context(str(tmp_path), summaries)

        content = claude_md.read_text()
        assert "## Parent Tasks (OWT DAG)" in content
        assert "**models**" in content
        assert "**jwt**" in content
        assert "Existing content." in content

    def test_replaces_existing_context(self, tmp_path: Path):
        from open_orchestrator.core.environment import inject_dag_context

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        claude_md = claude_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\nContent.\n"
            "\n<!-- OWT-DAG-CONTEXT-START -->\n## Parent Tasks (OWT DAG)\n\nOld context\n\n<!-- OWT-DAG-CONTEXT-END -->\n"
        )

        inject_dag_context(str(tmp_path), ["**new-parent**: updated"])

        content = claude_md.read_text()
        assert "Old context" not in content
        assert "**new-parent**" in content
        # Only one occurrence of the section
        assert content.count("## Parent Tasks (OWT DAG)") == 1

    def test_no_claude_md_is_noop(self, tmp_path: Path):
        from open_orchestrator.core.environment import inject_dag_context

        # No .claude/CLAUDE.md — should not crash
        inject_dag_context(str(tmp_path), ["summary"])


# ─── merge integration ─────────────────────────────────────────────────────


class TestMergeOrderDependency:
    def test_dependency_order_param_sorts(self):
        """Verify plan_merge_order respects dependency_order."""
        from unittest.mock import MagicMock, patch

        from open_orchestrator.core.merge import MergeManager
        from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

        statuses = [
            WorktreeAIStatus(
                worktree_name="c-task",
                worktree_path="/tmp/c",
                branch="feat/c",
                activity_status=AIActivityStatus.COMPLETED,
            ),
            WorktreeAIStatus(
                worktree_name="a-task",
                worktree_path="/tmp/a",
                branch="feat/a",
                activity_status=AIActivityStatus.COMPLETED,
            ),
            WorktreeAIStatus(
                worktree_name="b-task",
                worktree_path="/tmp/b",
                branch="feat/b",
                activity_status=AIActivityStatus.COMPLETED,
            ),
        ]

        with patch.object(MergeManager, "__init__", lambda self, *a, **k: None):
            mgr = MergeManager.__new__(MergeManager)
            mgr.wt_manager = MagicMock()
            mgr.repo = MagicMock()

            # Mock the methods called in loop
            mgr.get_base_branch = MagicMock(return_value="main")
            mgr.count_commits_ahead = MagicMock(return_value=5)
            mgr.check_file_overlaps = MagicMock(return_value={})

            # Patch StatusTracker where it's imported inside the method
            with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker_cls:
                mock_tracker = MagicMock()
                mock_tracker.get_all_statuses.return_value = statuses
                mock_tracker_cls.return_value = mock_tracker

                result = mgr.plan_merge_order(dependency_order=["a-task", "b-task", "c-task"])

            names = [r[0] for r in result]
            assert names == ["a-task", "b-task", "c-task"]
