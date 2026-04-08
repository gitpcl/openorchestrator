"""Tests for swarm-mode multi-agent coordination."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.prompt_builder import build_swarm_prompt
from open_orchestrator.core.swarm import DEFAULT_ROLES, SwarmError, SwarmManager
from open_orchestrator.models.swarm import (
    SwarmRole,
    SwarmState,
    SwarmWorker,
    SwarmWorkerStatus,
)


@pytest.fixture
def manager() -> SwarmManager:
    return SwarmManager()


class TestSwarmRolePrompts:
    def test_coordinator_prompt_includes_goal_and_roster(self) -> None:
        prompt = build_swarm_prompt(
            "coordinator",
            goal="Implement JWT auth",
            swarm_id="swarm-abc",
            worker_roster="- researcher\n- implementer\n- reviewer\n- tester",
        )
        assert "Implement JWT auth" in prompt
        assert "coordinator" in prompt.lower()
        assert "decompose" in prompt.lower()
        assert "researcher" in prompt
        assert "implementer" in prompt
        assert "swarm-abc" in prompt

    def test_researcher_prompt_read_only(self) -> None:
        prompt = build_swarm_prompt("researcher", goal="Implement JWT auth")
        assert "researcher" in prompt.lower()
        assert "read-only" in prompt.lower() or "do not edit" in prompt.lower()

    def test_implementer_prompt_mentions_production(self) -> None:
        prompt = build_swarm_prompt("implementer", goal="Add caching")
        assert "implementer" in prompt.lower()
        assert "production" in prompt.lower() or "write" in prompt.lower()

    def test_reviewer_prompt_read_only(self) -> None:
        prompt = build_swarm_prompt("reviewer", goal="Add caching")
        assert "read-only" in prompt.lower()
        assert "git diff" in prompt.lower() or "review" in prompt.lower()

    def test_tester_prompt_constrains_to_test_files(self) -> None:
        prompt = build_swarm_prompt("tester", goal="Add caching")
        assert "tests/" in prompt
        assert "tester" in prompt.lower()

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(KeyError):
            build_swarm_prompt("designer", goal="Add caching")

    def test_distinct_prompts_per_role(self) -> None:
        """Each role must produce a meaningfully different prompt."""
        prompts = {
            role: build_swarm_prompt(role, goal="Add feature X")
            for role in ("coordinator", "researcher", "implementer", "reviewer", "tester")
        }
        # All prompts unique
        assert len(set(prompts.values())) == 5


class TestSwarmManager:
    def test_start_swarm_default_roles(self, manager: SwarmManager) -> None:
        state = manager.start_swarm(
            goal="Implement auth",
            worktree="feature-auth",
            dry_run=True,
        )
        assert state.goal == "Implement auth"
        assert state.worktree == "feature-auth"
        assert len(state.workers) == len(DEFAULT_ROLES) + 1  # +1 for coordinator
        roles = [w.role for w in state.workers]
        assert SwarmRole.COORDINATOR in roles
        for default_role in DEFAULT_ROLES:
            assert default_role in roles

    def test_start_swarm_custom_roles(self, manager: SwarmManager) -> None:
        state = manager.start_swarm(
            goal="Research JWT",
            worktree="research",
            roles=[SwarmRole.RESEARCHER, SwarmRole.REVIEWER],
            dry_run=True,
        )
        assert len(state.workers) == 3  # coordinator + 2 specialists
        roles = {w.role for w in state.workers}
        assert roles == {
            SwarmRole.COORDINATOR,
            SwarmRole.RESEARCHER,
            SwarmRole.REVIEWER,
        }

    def test_start_swarm_empty_goal_raises(self, manager: SwarmManager) -> None:
        with pytest.raises(SwarmError):
            manager.start_swarm(goal="", worktree="x", dry_run=True)
        with pytest.raises(SwarmError):
            manager.start_swarm(goal="   ", worktree="x", dry_run=True)

    def test_swarm_id_unique(self, manager: SwarmManager) -> None:
        s1 = manager.start_swarm("goal 1", "wt-a", dry_run=True)
        s2 = manager.start_swarm("goal 2", "wt-b", dry_run=True)
        assert s1.swarm_id != s2.swarm_id
        assert s1.swarm_id.startswith("swarm-")

    def test_workers_get_role_scoped_prompts(self, manager: SwarmManager) -> None:
        state = manager.start_swarm(
            goal="Implement auth",
            worktree="feature-auth",
            roles=[SwarmRole.IMPLEMENTER, SwarmRole.TESTER],
            dry_run=True,
        )
        for worker in state.workers:
            assert worker.role.value in worker.prompt.lower()
            assert "Implement auth" in worker.prompt

    def test_coordinator_deduplicated(self, manager: SwarmManager) -> None:
        """Passing COORDINATOR in roles must not double-add it."""
        state = manager.start_swarm(
            goal="X",
            worktree="x",
            roles=[SwarmRole.COORDINATOR, SwarmRole.RESEARCHER],
            dry_run=True,
        )
        coords = [w for w in state.workers if w.role == SwarmRole.COORDINATOR]
        assert len(coords) == 1

    def test_coordinator_id_points_to_valid_worker(self, manager: SwarmManager) -> None:
        state = manager.start_swarm("goal", "wt", dry_run=True)
        assert state.coordinator is not None
        assert state.coordinator.role == SwarmRole.COORDINATOR

    def test_specialists_excludes_coordinator(self, manager: SwarmManager) -> None:
        state = manager.start_swarm("goal", "wt", dry_run=True)
        assert len(state.specialists) == len(DEFAULT_ROLES)
        assert all(w.role != SwarmRole.COORDINATOR for w in state.specialists)

    def test_list_swarms(self, manager: SwarmManager) -> None:
        assert manager.list_swarms() == []
        manager.start_swarm("g1", "w1", dry_run=True)
        manager.start_swarm("g2", "w2", dry_run=True)
        assert len(manager.list_swarms()) == 2

    def test_find_by_worktree(self, manager: SwarmManager) -> None:
        s1 = manager.start_swarm("g1", "w1", dry_run=True)
        manager.start_swarm("g2", "w2", dry_run=True)
        found = manager.find_swarm_by_worktree("w1")
        assert found is not None
        assert found.swarm_id == s1.swarm_id
        assert manager.find_swarm_by_worktree("missing") is None

    def test_stop_swarm(self, manager: SwarmManager) -> None:
        state = manager.start_swarm("g", "w", dry_run=True)
        assert manager.stop_swarm(state.swarm_id) is True
        assert manager.get_swarm(state.swarm_id) is None
        assert manager.stop_swarm(state.swarm_id) is False  # already gone

    def test_get_unknown_swarm_returns_none(self, manager: SwarmManager) -> None:
        assert manager.get_swarm("nope") is None


class TestBroadcast:
    def test_broadcast_unknown_swarm_raises(self, manager: SwarmManager) -> None:
        with pytest.raises(SwarmError):
            manager.broadcast("missing", "hello")

    def test_broadcast_includes_all_by_default(self, manager: SwarmManager) -> None:
        state = manager.start_swarm("g", "w", dry_run=True)
        targets = manager.broadcast(state.swarm_id, "hi")
        assert len(targets) == len(state.workers)

    def test_broadcast_can_exclude_coordinator(self, manager: SwarmManager) -> None:
        state = manager.start_swarm("g", "w", dry_run=True)
        targets = manager.broadcast(state.swarm_id, "hi", include_coordinator=False)
        assert len(targets) == len(state.workers) - 1
        assert all(t.id != state.coordinator_id for t in targets)


class TestSwarmModels:
    def test_worker_defaults(self) -> None:
        worker = SwarmWorker(
            id="swarm-1:researcher:0",
            role=SwarmRole.RESEARCHER,
            prompt="do research",
        )
        assert worker.status == SwarmWorkerStatus.PENDING
        assert worker.tmux_session is None

    def test_state_specialists_property(self) -> None:
        state = SwarmState(
            swarm_id="s1",
            goal="g",
            worktree="w",
            coordinator_id="s1:coordinator:0",
            workers=[
                SwarmWorker(id="s1:coordinator:0", role=SwarmRole.COORDINATOR, prompt="p"),
                SwarmWorker(id="s1:researcher:0", role=SwarmRole.RESEARCHER, prompt="p"),
                SwarmWorker(id="s1:implementer:0", role=SwarmRole.IMPLEMENTER, prompt="p"),
            ],
        )
        assert len(state.specialists) == 2
        assert state.coordinator is not None
        assert state.coordinator.role == SwarmRole.COORDINATOR
        assert state.worker_ids == [
            "s1:coordinator:0",
            "s1:researcher:0",
            "s1:implementer:0",
        ]


class TestSwarmCLI:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_swarm_start_dry_run(self, runner: CliRunner) -> None:
        result = runner.invoke(
            main,
            [
                "swarm",
                "start",
                "implement auth",
                "-w",
                "feature-auth",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Started swarm" in result.output
        assert "feature-auth" in result.output
        assert "coordinator" in result.output
        assert "researcher" in result.output

    def test_swarm_start_custom_roles(self, runner: CliRunner) -> None:
        result = runner.invoke(
            main,
            [
                "swarm",
                "start",
                "research only",
                "-w",
                "wt",
                "--role",
                "researcher",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "researcher" in result.output

    def test_swarm_start_empty_goal_errors(self, runner: CliRunner) -> None:
        result = runner.invoke(
            main,
            ["swarm", "start", "  ", "-w", "wt", "--dry-run"],
        )
        assert result.exit_code != 0

    def test_swarm_start_missing_worktree(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["swarm", "start", "goal", "--dry-run"])
        assert result.exit_code != 0

    def test_swarm_list_empty(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["swarm", "list"])
        assert result.exit_code == 0
        # Either lists previously-spawned swarms or shows empty
        assert "No active swarms" in result.output or "active swarm" in result.output

    def test_send_swarm_unknown_errors(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["send", "--swarm", "missing-id", "hello"])
        assert result.exit_code != 0


class TestSwarmGrouping:
    def test_group_cards_by_swarm(self) -> None:
        from open_orchestrator.core.switchboard_cards import (
            Card,
            group_cards_by_swarm,
        )
        from open_orchestrator.models.status import AIActivityStatus

        def make_card(name: str, swarm_id: str | None, role: str | None) -> Card:
            return Card(
                name=name,
                status=AIActivityStatus.WORKING,
                branch="main",
                ai_tool="claude",
                task=None,
                elapsed="1s",
                tmux_session=None,
                swarm_id=swarm_id,
                swarm_role=role,
            )

        cards = [
            make_card("solo", None, None),
            make_card("swarm-1-coord", "swarm-1", "coordinator"),
            make_card("swarm-1-r", "swarm-1", "researcher"),
            make_card("swarm-1-i", "swarm-1", "implementer"),
            make_card("swarm-2-coord", "swarm-2", "coordinator"),
        ]
        groups, standalone = group_cards_by_swarm(cards)
        assert len(standalone) == 1
        assert standalone[0].name == "solo"
        assert len(groups) == 2
        swarm_1 = next(g for g in groups if g.swarm_id == "swarm-1")
        assert swarm_1.coordinator is not None
        assert swarm_1.coordinator.name == "swarm-1-coord"
        assert len(swarm_1.workers) == 2
        assert swarm_1.total_cards == 3

    def test_group_no_swarm_cards(self) -> None:
        from open_orchestrator.core.switchboard_cards import (
            Card,
            group_cards_by_swarm,
        )
        from open_orchestrator.models.status import AIActivityStatus

        cards = [
            Card(
                name="a",
                status=AIActivityStatus.IDLE,
                branch="main",
                ai_tool="claude",
                task=None,
                elapsed="",
                tmux_session=None,
            )
        ]
        groups, standalone = group_cards_by_swarm(cards)
        assert groups == []
        assert len(standalone) == 1
