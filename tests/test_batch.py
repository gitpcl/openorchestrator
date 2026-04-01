"""Tests for BatchRunner execution paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_orchestrator.core.batch import (
    BatchConfig,
    BatchRunner,
    BatchStatus,
    BatchTask,
    load_batch_config,
)


@pytest.fixture
def simple_config() -> BatchConfig:
    return BatchConfig(
        tasks=[
            BatchTask(description="Task A", id="a"),
            BatchTask(description="Task B", id="b"),
        ],
        max_concurrent=2,
    )


@pytest.fixture
def dag_config() -> BatchConfig:
    return BatchConfig(
        tasks=[
            BatchTask(description="Task A", id="a"),
            BatchTask(description="Task B", id="b", depends_on=["a"]),
            BatchTask(description="Task C", id="c"),
        ],
        max_concurrent=3,
    )


class TestBatchRunnerInit:
    def test_creates_results(self, simple_config: BatchConfig) -> None:
        runner = BatchRunner(simple_config, "/tmp/fake")
        assert len(runner.results) == 2
        assert all(r.status == BatchStatus.PENDING for r in runner.results)

    def test_builds_task_index(self, simple_config: BatchConfig) -> None:
        runner = BatchRunner(simple_config, "/tmp/fake")
        assert runner._task_index == {"a": 0, "b": 1}


class TestBatchRunnerDeps:
    def test_deps_satisfied_no_deps(self, simple_config: BatchConfig) -> None:
        runner = BatchRunner(simple_config, "/tmp/fake")
        assert runner._deps_satisfied(0)
        assert runner._deps_satisfied(1)

    def test_deps_satisfied_with_deps(self, dag_config: BatchConfig) -> None:
        runner = BatchRunner(dag_config, "/tmp/fake")
        assert not runner._deps_satisfied(1)  # b depends on a
        runner.results[0].status = BatchStatus.COMPLETED
        assert runner._deps_satisfied(1)

    def test_deps_failed_detection(self, dag_config: BatchConfig) -> None:
        runner = BatchRunner(dag_config, "/tmp/fake")
        runner.results[0].status = BatchStatus.FAILED
        assert runner._deps_failed(1)


class TestBatchStateResume:
    def test_save_and_resume_roundtrip(self, tmp_path: Path, simple_config: BatchConfig) -> None:
        runner = BatchRunner(simple_config, str(tmp_path))
        runner.results[0].status = BatchStatus.COMPLETED
        runner.results[0].worktree_name = "wt-a"
        runner._save_state()

        state_path = BatchRunner._state_path(str(tmp_path))
        assert state_path.exists()

        resumed = BatchRunner.resume(str(tmp_path))
        assert len(resumed.results) == 2
        assert resumed.results[0].status == BatchStatus.COMPLETED
        assert resumed.results[0].worktree_name == "wt-a"
        assert resumed.results[1].status == BatchStatus.PENDING

    def test_resume_no_state_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            BatchRunner.resume(str(tmp_path))

    def test_state_cleaned_on_success(self, tmp_path: Path, simple_config: BatchConfig) -> None:
        runner = BatchRunner(simple_config, str(tmp_path))
        runner._save_state()
        assert BatchRunner._state_path(str(tmp_path)).exists()
        # Simulate completion by removing the file as run() would
        BatchRunner._state_path(str(tmp_path)).unlink()
        assert not BatchRunner._state_path(str(tmp_path)).exists()


class TestLoadBatchConfig:
    def test_valid_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "tasks.toml"
        toml_file.write_text('[batch]\nmax_concurrent = 2\n\n[[tasks]]\ndescription = "Task A"\n')
        config = load_batch_config(str(toml_file))
        assert len(config.tasks) == 1
        assert config.max_concurrent == 2

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "bad.toml"
        toml_file.write_text('[batch]\nunknown = true\n\n[[tasks]]\ndescription = "A"\n')
        with pytest.raises(Exception):
            load_batch_config(str(toml_file))

    def test_unknown_task_key_rejected(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "bad2.toml"
        toml_file.write_text('[[tasks]]\ndescription = "A"\nunknown_field = true\n')
        with pytest.raises(Exception):
            load_batch_config(str(toml_file))

    def test_backward_compat_no_batch_section(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "simple.toml"
        toml_file.write_text('[[tasks]]\ndescription = "Just a task"\n')
        config = load_batch_config(str(toml_file))
        assert len(config.tasks) == 1
        assert config.max_concurrent == 3  # default

    def test_dag_fields_preserved(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "dag.toml"
        toml_file.write_text(
            '[[tasks]]\nid = "a"\ndescription = "First"\n\n[[tasks]]\nid = "b"\ndescription = "Second"\ndepends_on = ["a"]\n'
        )
        config = load_batch_config(str(toml_file))
        assert config.tasks[0].id == "a"
        assert config.tasks[1].depends_on == ["a"]


class TestBatchHandleFailure:
    def test_retry_resets_to_pending(self, simple_config: BatchConfig) -> None:
        runner = BatchRunner(simple_config, "/tmp/fake")
        runner.results[0].status = BatchStatus.RUNNING
        runner._handle_batch_failure(0, "test error")
        assert runner.results[0].status == BatchStatus.PENDING
        assert runner.results[0].retry_count == 1

    def test_max_retries_marks_failed(self, simple_config: BatchConfig) -> None:
        runner = BatchRunner(simple_config, "/tmp/fake")
        runner.results[0].status = BatchStatus.RUNNING
        runner.results[0].retry_count = 1
        runner.results[0].max_retries = 1
        runner._handle_batch_failure(0, "permanent error")
        assert runner.results[0].status == BatchStatus.FAILED
