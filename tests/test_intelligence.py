"""Tests for the Agno intelligence layer.

All tests mock Agno agents — no API calls needed.
Agno is an optional dependency, so tests pre-seed sys.modules with mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.config import AgnoConfig, Config
from open_orchestrator.models.intelligence import (
    ConflictResolution,
    PlannedTask,
    QualityVerdict,
    TaskPlan,
)

# ─── Agno mock fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_agno_modules():
    """Pre-seed sys.modules so 'from agno.xxx import ...' works without install."""
    mock_agno = MagicMock()
    mock_agent_module = MagicMock()
    mock_anthropic_module = MagicMock()
    mock_openai_module = MagicMock()
    mock_google_module = MagicMock()

    mock_sqlite_module = MagicMock()

    modules = {
        "agno": mock_agno,
        "agno.agent": mock_agent_module,
        "agno.models": MagicMock(),
        "agno.models.anthropic": mock_anthropic_module,
        "agno.models.openai": mock_openai_module,
        "agno.models.google": mock_google_module,
        "agno.db": MagicMock(),
        "agno.db.sqlite": mock_sqlite_module,
    }

    with patch.dict(sys.modules, modules):
        yield {
            "agent": mock_agent_module,
            "anthropic": mock_anthropic_module,
            "openai": mock_openai_module,
            "google": mock_google_module,
            "sqlite": mock_sqlite_module,
        }


# ─── Pydantic Models ──────────────────────────────────────────────────────


class TestPlannedTask:
    def test_defaults(self):
        task = PlannedTask(id="auth", description="Add auth")
        assert task.depends_on == []
        assert task.estimated_files == []
        assert task.ai_tool == "claude"

    def test_full_fields(self):
        task = PlannedTask(
            id="api",
            description="Create API",
            depends_on=["models"],
            estimated_files=["api.py", "routes.py"],
            ai_tool="opencode",
        )
        assert task.depends_on == ["models"]
        assert len(task.estimated_files) == 2


class TestTaskPlan:
    def test_valid_plan(self):
        plan = TaskPlan(
            goal="Build auth",
            tasks=[PlannedTask(id="a", description="Task A")],
            max_concurrent=2,
            rationale="Simple decomposition",
        )
        assert plan.goal == "Build auth"
        assert len(plan.tasks) == 1

    def test_empty_tasks_rejected(self):
        with pytest.raises(Exception):
            TaskPlan(goal="Build auth", tasks=[], rationale="No tasks")

    def test_max_tasks_limit(self):
        tasks = [PlannedTask(id=f"t{i}", description=f"Task {i}") for i in range(13)]
        with pytest.raises(Exception):
            TaskPlan(goal="Too many", tasks=tasks, rationale="Over limit")


class TestQualityVerdict:
    def test_passing_verdict(self):
        v = QualityVerdict(
            score=0.9,
            passed=True,
            summary="Looks good",
            issues=[],
            suggestions=["Add docstring"],
        )
        assert v.passed
        assert v.score == 0.9

    def test_failing_verdict(self):
        v = QualityVerdict(
            score=0.3,
            passed=False,
            summary="Issues found",
            issues=["Hardcoded API key"],
            cross_worktree_conflicts=["auth-jwt modifies the same file"],
        )
        assert not v.passed
        assert len(v.cross_worktree_conflicts) == 1

    def test_score_bounds(self):
        with pytest.raises(Exception):
            QualityVerdict(score=1.5, passed=True, summary="Invalid")
        with pytest.raises(Exception):
            QualityVerdict(score=-0.1, passed=False, summary="Invalid")


class TestConflictResolution:
    def test_confident_resolution(self):
        r = ConflictResolution(
            confidence=0.95,
            resolutions={"auth.py": "resolved content"},
            explanation="Combined both changes",
            requires_human=False,
        )
        assert r.confidence > 0.8
        assert not r.requires_human

    def test_low_confidence(self):
        r = ConflictResolution(
            confidence=0.3,
            resolutions={},
            explanation="Too ambiguous",
            requires_human=True,
        )
        assert r.requires_human
        assert r.resolutions == {}


# ─── Codebase Tools ───────────────────────────────────────────────────────


class TestCodebaseTools:
    def test_read_file(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _read_file

        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\n")
        result = _read_file(str(f))
        assert "line1" in result
        assert "line3" in result

    def test_read_file_truncation(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _read_file

        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line{i}" for i in range(500)))
        result = _read_file(str(f), max_lines=10)
        assert "490 more lines" in result

    def test_read_file_not_found(self):
        from open_orchestrator.core.intelligence import _read_file

        result = _read_file("/nonexistent/path.py")
        assert "Error" in result

    def test_list_directory(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _list_directory

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        (tmp_path / "README.md").touch()

        result = _list_directory(str(tmp_path))
        assert "src/" in result
        assert "main.py" in result
        assert "README.md" in result

    def test_list_directory_skips_git(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _list_directory

        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "src").mkdir()

        result = _list_directory(str(tmp_path))
        assert ".git" not in result
        assert "node_modules" not in result
        assert "src/" in result

    def test_list_directory_not_a_dir(self):
        from open_orchestrator.core.intelligence import _list_directory

        result = _list_directory("/nonexistent")
        assert "Error" in result

    def test_git_log(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _git_log

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123 Initial commit")
            result = _git_log(str(tmp_path))
            assert "Initial commit" in result

    def test_git_diff_stat(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _git_diff_stat

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=" src/main.py | 5 +++++")
            result = _git_diff_stat(str(tmp_path), "feat/auth", "main")
            assert "main.py" in result


# ─── Model Resolution ─────────────────────────────────────────────────────


class TestResolveModel:
    def test_claude_model(self):
        from open_orchestrator.core.intelligence import _resolve_model

        mock_cls = MagicMock()
        with patch.dict(sys.modules, {"agno.models.anthropic": MagicMock(Claude=mock_cls)}):
            _resolve_model("claude-sonnet-4-20250514", max_tokens=2048, temperature=0.5)
            mock_cls.assert_called_once_with(id="claude-sonnet-4-20250514", max_tokens=2048, temperature=0.5)

    def test_gpt_model(self):
        from open_orchestrator.core.intelligence import _resolve_model

        mock_cls = MagicMock()
        with patch.dict(sys.modules, {"agno.models.openai": MagicMock(OpenAIChat=mock_cls)}):
            _resolve_model("gpt-4o", max_tokens=4096, temperature=0.2)
            mock_cls.assert_called_once_with(id="gpt-4o", max_tokens=4096, temperature=0.2)

    def test_gemini_model(self):
        from open_orchestrator.core.intelligence import _resolve_model

        mock_cls = MagicMock()
        with patch.dict(sys.modules, {"agno.models.google": MagicMock(Gemini=mock_cls)}):
            _resolve_model("gemini-2.0-flash")
            mock_cls.assert_called_once_with(id="gemini-2.0-flash", max_tokens=4096, temperature=0.2)

    def test_default_to_claude(self):
        from open_orchestrator.core.intelligence import _resolve_model

        mock_cls = MagicMock()
        with patch.dict(sys.modules, {"agno.models.anthropic": MagicMock(Claude=mock_cls)}):
            _resolve_model("unknown-model-123")
            mock_cls.assert_called_once_with(id="unknown-model-123", max_tokens=4096, temperature=0.2)


# ─── AgnoPlanner ───────────────────────────────────────────────────────────


class TestAgnoPlanner:
    def test_plan_produces_valid_toml(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import AgnoPlanner

        config = AgnoConfig(enabled=True, model_id="claude-sonnet-4-20250514")

        mock_plan = TaskPlan(
            goal="Add auth",
            tasks=[
                PlannedTask(id="models", description="Create user model", estimated_files=["models.py"]),
                PlannedTask(id="api", description="Create API routes", depends_on=["models"]),
            ],
            max_concurrent=2,
            rationale="Models first, then API",
        )

        mock_response = MagicMock()
        mock_response.content = mock_plan

        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run.return_value = mock_response

        with (
            patch("open_orchestrator.core.intelligence._resolve_model"),
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            planner = AgnoPlanner(config)
            output = planner.plan("Add auth", str(tmp_path))

            assert output.exists()
            import toml

            data = toml.load(str(output))
            assert len(data["tasks"]) == 2
            assert data["tasks"][0]["id"] == "models"
            assert data["tasks"][1]["depends_on"] == ["models"]
            assert data["batch"]["max_concurrent"] == 2

    def test_plan_to_toml_conversion(self):
        from open_orchestrator.core.intelligence import AgnoPlanner

        config = AgnoConfig()
        planner = AgnoPlanner(config)

        plan = TaskPlan(
            goal="Test",
            tasks=[PlannedTask(id="a", description="Task A", ai_tool="opencode")],
            max_concurrent=3,
            rationale="Simple",
        )

        result = planner._plan_to_toml(plan)
        assert result["batch"]["max_concurrent"] == 3
        assert result["tasks"][0]["ai_tool"] == "opencode"


# ─── AgnoQualityGate ──────────────────────────────────────────────────────


class TestAgnoQualityGate:
    def _run_gate(self, config, mock_verdict, diff="diff", task_desc=None, active_wts=None):
        from open_orchestrator.core.intelligence import AgnoQualityGate

        mock_response = MagicMock()
        mock_response.content = mock_verdict

        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run.return_value = mock_response

        with (
            patch("open_orchestrator.core.intelligence._resolve_model"),
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            gate = AgnoQualityGate(config)
            return gate.review(diff, task_desc, active_wts)

    def test_review_returns_verdict(self):
        config = AgnoConfig(quality_gate_threshold=0.7)
        verdict = QualityVerdict(score=0.85, passed=True, summary="Code looks good")

        result = self._run_gate(config, verdict, diff="diff content", task_desc="Add auth flow")
        assert result.passed
        assert result.score == 0.85

    def test_review_enforces_threshold(self):
        config = AgnoConfig(quality_gate_threshold=0.8)
        verdict = QualityVerdict(score=0.6, passed=True, summary="Marginal quality")

        result = self._run_gate(config, verdict)
        assert not result.passed

    def test_review_with_active_worktrees(self):
        config = AgnoConfig()
        verdict = QualityVerdict(
            score=0.5,
            passed=False,
            summary="Potential conflicts",
            cross_worktree_conflicts=["auth-jwt modifies routes.py"],
        )

        result = self._run_gate(
            config,
            verdict,
            active_wts=[{"name": "auth-jwt", "branch": "feat/jwt", "task": "Add JWT"}],
        )
        assert len(result.cross_worktree_conflicts) == 1


# ─── AgnoConflictResolver ─────────────────────────────────────────────────


class TestAgnoConflictResolver:
    def _run_resolver(self, config, mock_resolution, files=None, src="feat/x", tgt="main"):
        from open_orchestrator.core.intelligence import AgnoConflictResolver

        mock_response = MagicMock()
        mock_response.content = mock_resolution

        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run.return_value = mock_response

        with (
            patch("open_orchestrator.core.intelligence._resolve_model"),
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            resolver = AgnoConflictResolver(config)
            return resolver.resolve(
                conflicted_files=files or {"file.py": "<<<< HEAD\nours\n====\ntheirs\n>>>>"},
                source_branch=src,
                target_branch=tgt,
            )

    def test_resolve_returns_resolution(self):
        resolution = ConflictResolution(
            confidence=0.95,
            resolutions={"auth.py": "resolved auth content"},
            explanation="Combined both auth approaches",
            requires_human=False,
        )

        result = self._run_resolver(
            AgnoConfig(),
            resolution,
            files={"auth.py": "<<<< HEAD\nours\n====\ntheirs\n>>>> feat/x"},
            src="feat/auth",
        )
        assert result.confidence > 0.8
        assert "auth.py" in result.resolutions
        assert not result.requires_human

    def test_resolve_low_confidence(self):
        resolution = ConflictResolution(
            confidence=0.3,
            resolutions={},
            explanation="Too complex to auto-resolve",
            requires_human=True,
        )

        result = self._run_resolver(AgnoConfig(), resolution)
        assert result.requires_human
        assert result.confidence < 0.5


# ─── Fallback behavior ────────────────────────────────────────────────────


# ─── Memory Helpers ──────────────────────────────────────────────────────


class TestGetMemoryDb:
    def test_returns_none_when_disabled(self):
        from open_orchestrator.core.intelligence import _get_memory_db

        config = AgnoConfig(memory_enabled=False)
        assert _get_memory_db(config, "/tmp/repo") is None

    def test_returns_instance_when_enabled(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _get_memory_db

        config = AgnoConfig(memory_enabled=True, memory_db_path=str(tmp_path / "test.db"))
        result = _get_memory_db(config, str(tmp_path))
        assert result is not None

    def test_uses_default_path(self):
        from open_orchestrator.core.intelligence import _get_memory_db

        config = AgnoConfig(memory_enabled=True)
        result = _get_memory_db(config, "/tmp/repo")
        assert result is not None

    def test_returns_none_when_import_fails(self):
        from open_orchestrator.core.intelligence import _get_memory_db

        config = AgnoConfig(memory_enabled=True)
        # Remove the mocked module to simulate ImportError
        with patch.dict(sys.modules, {"agno.db.sqlite": None}):
            result = _get_memory_db(config, "/tmp/repo")
            assert result is None


class TestGetRepoName:
    def test_extracts_directory_name(self):
        from open_orchestrator.core.intelligence import _get_repo_name

        assert _get_repo_name("/home/user/projects/my-app") == "my-app"

    def test_resolves_symlinks(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _get_repo_name

        result = _get_repo_name(str(tmp_path))
        assert result == tmp_path.resolve().name


# ─── Memory Context Builder ──────────────────────────────────────────────


class TestBuildMemoryContext:
    def test_returns_populated_when_enabled(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _build_memory_context

        config = AgnoConfig(memory_enabled=True, memory_db_path=str(tmp_path / "mem.db"))
        agent_kw, run_kw, instr = _build_memory_context(
            config,
            str(tmp_path),
            "planner",
            "test instruction",
        )
        assert "db" in agent_kw
        assert agent_kw["enable_agentic_memory"] is True
        assert "user_id" in run_kw
        assert run_kw["session_id"] == "planner"
        assert instr == "test instruction"

    def test_returns_empty_when_disabled(self):
        from open_orchestrator.core.intelligence import _build_memory_context

        config = AgnoConfig(memory_enabled=False)
        agent_kw, run_kw, instr = _build_memory_context(
            config,
            "/tmp/repo",
            "planner",
            "test",
        )
        assert agent_kw == {}
        assert run_kw == {}
        assert instr == ""

    def test_returns_empty_when_no_repo_path(self):
        from open_orchestrator.core.intelligence import _build_memory_context

        config = AgnoConfig(memory_enabled=True)
        agent_kw, run_kw, instr = _build_memory_context(
            config,
            None,
            "planner",
            "test",
        )
        assert agent_kw == {}
        assert run_kw == {}

    def test_scopes_by_session_id(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import _build_memory_context

        config = AgnoConfig(memory_enabled=True, memory_db_path=str(tmp_path / "mem.db"))
        _, run1, _ = _build_memory_context(config, str(tmp_path), "planner", "")
        _, run2, _ = _build_memory_context(config, str(tmp_path), "quality-gate", "")
        assert run1["session_id"] == "planner"
        assert run2["session_id"] == "quality-gate"
        assert run1["user_id"] == run2["user_id"]


# ─── Memory Integration ─────────────────────────────────────────────────


class TestAgentMemoryIntegration:
    """Verify all three agents thread memory kwargs to Agent() and .run()."""

    def _run_agent(self, cls_name: str, tmp_path: Path, call_fn):
        config = AgnoConfig(memory_enabled=True, memory_db_path=str(tmp_path / "mem.db"))
        mock_agent_cls = MagicMock()
        with (
            patch("open_orchestrator.core.intelligence._resolve_model"),
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            call_fn(config, mock_agent_cls, tmp_path)
        return mock_agent_cls

    def test_planner_passes_memory(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import AgnoPlanner

        mock_plan = TaskPlan(
            goal="Test",
            tasks=[PlannedTask(id="a", description="Task A")],
            max_concurrent=2,
            rationale="Simple",
        )

        def call(config, mock_cls, path):
            mock_cls.return_value.run.return_value = MagicMock(content=mock_plan)
            AgnoPlanner(config, repo_path=str(path)).plan("Test", str(path))

        mock = self._run_agent("AgnoPlanner", tmp_path, call)
        assert "db" in mock.call_args.kwargs
        assert mock.return_value.run.call_args.kwargs["session_id"] == "planner"

    def test_quality_gate_passes_memory(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import AgnoQualityGate

        def call(config, mock_cls, path):
            mock_cls.return_value.run.return_value = MagicMock(
                content=QualityVerdict(score=0.9, passed=True, summary="Good"),
            )
            AgnoQualityGate(config, repo_path=str(path)).review("diff")

        mock = self._run_agent("AgnoQualityGate", tmp_path, call)
        assert "db" in mock.call_args.kwargs
        assert mock.return_value.run.call_args.kwargs["session_id"] == "quality-gate"

    def test_conflict_resolver_passes_memory(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import AgnoConflictResolver

        def call(config, mock_cls, path):
            mock_cls.return_value.run.return_value = MagicMock(
                content=ConflictResolution(
                    confidence=0.9,
                    resolutions={"f.py": "r"},
                    explanation="Done",
                    requires_human=False,
                ),
            )
            AgnoConflictResolver(config, repo_path=str(path)).resolve(
                {"f.py": "conflict"},
                "feat/x",
                "main",
            )

        mock = self._run_agent("AgnoConflictResolver", tmp_path, call)
        assert "db" in mock.call_args.kwargs
        assert mock.return_value.run.call_args.kwargs["session_id"] == "conflict-resolver"

    def test_planner_omits_memory_when_disabled(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import AgnoPlanner

        config = AgnoConfig(memory_enabled=False)
        mock_plan = TaskPlan(
            goal="Test",
            tasks=[PlannedTask(id="a", description="Task A")],
            max_concurrent=2,
            rationale="Simple",
        )
        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run.return_value = MagicMock(content=mock_plan)

        with (
            patch("open_orchestrator.core.intelligence._resolve_model"),
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            AgnoPlanner(config, repo_path=str(tmp_path)).plan("Test", str(tmp_path))
            assert "db" not in mock_agent_cls.call_args.kwargs
            assert "user_id" not in mock_agent_cls.return_value.run.call_args.kwargs


# ─── Fallback behavior ────────────────────────────────────────────────────


class TestFallbackBehavior:
    def test_plan_tasks_falls_back_when_agno_disabled(self, tmp_path: Path):
        """When agno is disabled, plan_tasks() uses subprocess path."""
        config = Config()
        config.agno.enabled = False

        toml_output = '```toml\n[batch]\nmax_concurrent = 2\n\n[[tasks]]\nid = "a"\ndescription = "Task"\ndepends_on = []\n```'

        with patch("open_orchestrator.config.load_config", return_value=config), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=toml_output, stderr="")

            from open_orchestrator.core.batch import plan_tasks

            result = plan_tasks("test goal", str(tmp_path))
            assert result.exists()
            mock_run.assert_called_once()
