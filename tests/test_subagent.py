"""Tests for subagent fork-join lifecycle management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from open_orchestrator.core.subagent import MAX_CONTEXT_CHARS, SubagentManager
from open_orchestrator.models.subagent import (
    ForkJoinRequest,
    ForkSpec,
    SubagentRole,
    SubagentState,
    SubagentStatus,
)


# ── Model Tests ─────────────────────────────────────────────────────


class TestSubagentState:
    def test_is_terminal_completed(self) -> None:
        state = SubagentState(
            id="test:worker:0",
            parent_name="test",
            role=SubagentRole.WORKER,
            prompt="Do stuff",
            status=SubagentStatus.COMPLETED,
        )
        assert state.is_terminal is True

    def test_is_terminal_running(self) -> None:
        state = SubagentState(
            id="test:worker:0",
            parent_name="test",
            role=SubagentRole.WORKER,
            prompt="Do stuff",
            status=SubagentStatus.RUNNING,
        )
        assert state.is_terminal is False

    def test_is_terminal_failed(self) -> None:
        state = SubagentState(
            id="test:worker:0",
            parent_name="test",
            role=SubagentRole.WORKER,
            prompt="Do stuff",
            status=SubagentStatus.FAILED,
        )
        assert state.is_terminal is True

    def test_is_terminal_timed_out(self) -> None:
        state = SubagentState(
            id="test:worker:0",
            parent_name="test",
            role=SubagentRole.WORKER,
            prompt="Do stuff",
            status=SubagentStatus.TIMED_OUT,
        )
        assert state.is_terminal is True

    def test_elapsed_seconds_not_started(self) -> None:
        state = SubagentState(
            id="test:worker:0",
            parent_name="test",
            role=SubagentRole.WORKER,
            prompt="Do stuff",
        )
        assert state.elapsed_seconds == 0.0


class TestSubagentRole:
    def test_all_roles(self) -> None:
        assert len(SubagentRole) == 5
        assert SubagentRole.RESEARCH.value == "research"
        assert SubagentRole.SYNTHESIS.value == "synthesis"
        assert SubagentRole.CRITIC.value == "critic"
        assert SubagentRole.WORKER.value == "worker"
        assert SubagentRole.PLANNER.value == "planner"


# ── SubagentManager Fork Tests ──────────────────────────────────────


class TestFork:
    def test_fork_creates_agent(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork("parent-1", SubagentRole.RESEARCH, "Find auth patterns")
        assert state.id == "parent-1:research:0"
        assert state.role == SubagentRole.RESEARCH
        assert state.status == SubagentStatus.PENDING
        assert "Find auth patterns" in state.prompt

    def test_fork_increments_index(self) -> None:
        mgr = SubagentManager()
        s1 = mgr.fork("parent-1", SubagentRole.WORKER, "Task 1")
        s2 = mgr.fork("parent-1", SubagentRole.WORKER, "Task 2")
        assert s1.id == "parent-1:worker:0"
        assert s2.id == "parent-1:worker:1"

    def test_fork_with_context(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork(
            "parent-1",
            SubagentRole.CRITIC,
            "Review code",
            context="The project uses Pydantic for models.",
        )
        assert "Parent Context" in state.prompt
        assert "Pydantic" in state.prompt

    def test_fork_context_trimmed(self) -> None:
        mgr = SubagentManager()
        long_context = "x" * (MAX_CONTEXT_CHARS + 1000)
        state = mgr.fork(
            "parent-1",
            SubagentRole.RESEARCH,
            "Research",
            context=long_context,
        )
        assert "[context trimmed]" in state.prompt

    def test_fork_role_preamble(self) -> None:
        mgr = SubagentManager()
        for role in SubagentRole:
            state = mgr.fork("test", role, "Task")
            assert role.value in state.prompt.lower() or "subagent" in state.prompt.lower()

    def test_active_count(self) -> None:
        mgr = SubagentManager()
        assert mgr.active_count == 0
        mgr.fork("p", SubagentRole.WORKER, "t1")
        # PENDING doesn't count as active
        assert mgr.active_count == 0


# ── Join & Collect Tests ─────────────────────────────────────────────


class TestJoin:
    def test_join_completed(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork("p", SubagentRole.WORKER, "Do work")
        mgr.mark_completed(state.id, output="Done!")
        result = mgr.join(state.id)
        assert result is not None
        assert result.status == SubagentStatus.COMPLETED
        assert result.output == "Done!"

    def test_join_not_found(self) -> None:
        mgr = SubagentManager()
        assert mgr.join("nonexistent") is None

    def test_join_all(self) -> None:
        mgr = SubagentManager()
        s1 = mgr.fork("p1", SubagentRole.RESEARCH, "Research A")
        s2 = mgr.fork("p1", SubagentRole.SYNTHESIS, "Synthesize")
        s3 = mgr.fork("p2", SubagentRole.WORKER, "Other parent")
        mgr.mark_completed(s1.id, "Research done")
        mgr.mark_completed(s2.id, "Synthesis done")
        mgr.mark_completed(s3.id, "Other done")
        results = mgr.join_all("p1")
        assert len(results) == 2
        assert all(r.status == SubagentStatus.COMPLETED for r in results)

    def test_mark_completed(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork("p", SubagentRole.WORKER, "Task")
        assert mgr.mark_completed(state.id, "Output here") is True
        agent = mgr.get_agent(state.id)
        assert agent is not None
        assert agent.status == SubagentStatus.COMPLETED
        assert agent.output == "Output here"
        assert agent.completed_at is not None

    def test_mark_completed_already_terminal(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork("p", SubagentRole.WORKER, "Task")
        mgr.mark_completed(state.id, "First")
        assert mgr.mark_completed(state.id, "Second") is False

    def test_mark_failed(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork("p", SubagentRole.WORKER, "Task")
        assert mgr.mark_failed(state.id, "Crashed") is True
        agent = mgr.get_agent(state.id)
        assert agent is not None
        assert agent.status == SubagentStatus.FAILED
        assert agent.error == "Crashed"

    def test_mark_failed_not_found(self) -> None:
        mgr = SubagentManager()
        assert mgr.mark_failed("nope") is False


# ── Timeout Tests ────────────────────────────────────────────────────


class TestTimeout:
    def test_check_timeouts_no_running(self) -> None:
        mgr = SubagentManager()
        mgr.fork("p", SubagentRole.WORKER, "Task")
        timed_out = mgr._check_timeouts()
        assert timed_out == []

    def test_timeout_agent(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork("p", SubagentRole.WORKER, "Task", timeout_seconds=0)
        # Manually set to running with a past start time
        agent = mgr.get_agent(state.id)
        assert agent is not None
        agent.status = SubagentStatus.RUNNING
        from datetime import datetime, timedelta

        agent.started_at = datetime.now() - timedelta(seconds=10)

        timed_out = mgr._check_timeouts()
        assert state.id in timed_out
        assert agent.status == SubagentStatus.TIMED_OUT

    def test_is_timed_out_property(self) -> None:
        from datetime import datetime, timedelta

        state = SubagentState(
            id="test:worker:0",
            parent_name="test",
            role=SubagentRole.WORKER,
            prompt="Task",
            status=SubagentStatus.RUNNING,
            timeout_seconds=5,
            started_at=datetime.now() - timedelta(seconds=10),
        )
        assert state.is_timed_out is True

    def test_is_timed_out_not_running(self) -> None:
        state = SubagentState(
            id="test:worker:0",
            parent_name="test",
            role=SubagentRole.WORKER,
            prompt="Task",
            status=SubagentStatus.COMPLETED,
            timeout_seconds=0,
        )
        assert state.is_timed_out is False


# ── Cleanup Tests ────────────────────────────────────────────────────


class TestCleanup:
    def test_cleanup_removes_terminal(self) -> None:
        mgr = SubagentManager()
        s1 = mgr.fork("p1", SubagentRole.WORKER, "Task 1")
        s2 = mgr.fork("p1", SubagentRole.WORKER, "Task 2")
        mgr.mark_completed(s1.id, "Done")
        mgr.mark_failed(s2.id, "Error")
        removed = mgr.cleanup("p1")
        assert removed == 2
        assert mgr.list_agents(parent="p1") == []

    def test_cleanup_keeps_running(self) -> None:
        mgr = SubagentManager()
        s1 = mgr.fork("p1", SubagentRole.WORKER, "Task 1")
        mgr.mark_completed(s1.id, "Done")
        s2 = mgr.fork("p1", SubagentRole.WORKER, "Task 2")
        # s2 stays PENDING (not terminal)
        removed = mgr.cleanup("p1")
        assert removed == 1
        assert len(mgr.list_agents(parent="p1")) == 1

    def test_cleanup_other_parent_unaffected(self) -> None:
        mgr = SubagentManager()
        s1 = mgr.fork("p1", SubagentRole.WORKER, "Task")
        s2 = mgr.fork("p2", SubagentRole.WORKER, "Task")
        mgr.mark_completed(s1.id, "Done")
        mgr.mark_completed(s2.id, "Done")
        mgr.cleanup("p1")
        assert len(mgr.list_agents(parent="p2")) == 1

    def test_cleanup_all(self) -> None:
        mgr = SubagentManager()
        for i in range(5):
            s = mgr.fork(f"p{i}", SubagentRole.WORKER, f"Task {i}")
            mgr.mark_completed(s.id, "Done")
        removed = mgr.cleanup_all()
        assert removed == 5
        assert mgr.list_agents() == []


# ── Context Inheritance Tests ────────────────────────────────────────


class TestContextInheritance:
    def test_build_prompt_includes_role(self) -> None:
        prompt = SubagentManager._build_prompt(SubagentRole.CRITIC, "Review this", "")
        assert "critic" in prompt.lower()

    def test_build_prompt_includes_context(self) -> None:
        prompt = SubagentManager._build_prompt(
            SubagentRole.WORKER,
            "Do task",
            "The project uses Python 3.12",
        )
        assert "Parent Context" in prompt
        assert "Python 3.12" in prompt

    def test_build_prompt_trims_context(self) -> None:
        long = "x\n" * (MAX_CONTEXT_CHARS + 100)
        prompt = SubagentManager._build_prompt(SubagentRole.WORKER, "Task", long)
        assert "[context trimmed]" in prompt

    def test_build_prompt_empty_context(self) -> None:
        prompt = SubagentManager._build_prompt(SubagentRole.WORKER, "Task", "")
        assert "Parent Context" not in prompt
        assert "Task" in prompt

    def test_build_context_from_worktree(self, tmp_path: Path) -> None:
        # Set up a minimal worktree with CLAUDE.md
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("# Test Project\nThis is a test.")
        context = SubagentManager.build_context_from_worktree(tmp_path)
        assert "Test Project" in context

    def test_build_context_no_claude_md(self, tmp_path: Path) -> None:
        context = SubagentManager.build_context_from_worktree(tmp_path)
        # Should not crash, may be empty or have git log only
        assert isinstance(context, str)


# ── List & Get Tests ─────────────────────────────────────────────────


class TestListGet:
    def test_list_agents_empty(self) -> None:
        mgr = SubagentManager()
        assert mgr.list_agents() == []

    def test_list_agents_by_parent(self) -> None:
        mgr = SubagentManager()
        mgr.fork("p1", SubagentRole.WORKER, "Task 1")
        mgr.fork("p2", SubagentRole.WORKER, "Task 2")
        assert len(mgr.list_agents(parent="p1")) == 1
        assert len(mgr.list_agents(parent="p2")) == 1
        assert len(mgr.list_agents()) == 2

    def test_get_agent(self) -> None:
        mgr = SubagentManager()
        state = mgr.fork("p", SubagentRole.RESEARCH, "Research")
        retrieved = mgr.get_agent(state.id)
        assert retrieved is not None
        assert retrieved.id == state.id

    def test_get_agent_not_found(self) -> None:
        mgr = SubagentManager()
        assert mgr.get_agent("nonexistent") is None


# ── ForkJoinRequest Model Tests ──────────────────────────────────────


class TestForkJoinRequest:
    def test_request_model(self) -> None:
        req = ForkJoinRequest(
            parent_name="test",
            agents=[
                ForkSpec(role=SubagentRole.RESEARCH, prompt="Find patterns"),
                ForkSpec(role=SubagentRole.SYNTHESIS, prompt="Combine results"),
            ],
            timeout_seconds=120,
            context="Project uses Python",
        )
        assert len(req.agents) == 2
        assert req.timeout_seconds == 120

    def test_fork_spec_defaults(self) -> None:
        spec = ForkSpec(role=SubagentRole.WORKER, prompt="Task")
        assert spec.timeout_seconds is None
