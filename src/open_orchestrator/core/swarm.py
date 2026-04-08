"""Swarm-mode multi-agent coordination.

A swarm is a coordinator agent plus N specialized workers (researcher,
implementer, reviewer, tester) that collaborate on a single goal within
one worktree. Workers are spawned as subagents — tmux panes in the
coordinator's session — so fork is cheap (~100ms per worker).

Usage::

    from open_orchestrator.core.swarm import SwarmManager
    from open_orchestrator.models.swarm import SwarmRole

    manager = SwarmManager()
    swarm = manager.start_swarm(
        goal="Implement JWT auth",
        worktree="feature-auth",
        tmux_session="owt-feature-auth",
        roles=[
            SwarmRole.RESEARCHER,
            SwarmRole.IMPLEMENTER,
            SwarmRole.REVIEWER,
            SwarmRole.TESTER,
        ],
    )
    # swarm.swarm_id is the unique id for broadcast targeting
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from open_orchestrator.core.prompt_builder import build_swarm_prompt
from open_orchestrator.models.swarm import (
    SwarmRole,
    SwarmState,
    SwarmWorker,
    SwarmWorkerStatus,
)

logger = logging.getLogger(__name__)


# Default worker roster when none is specified
DEFAULT_ROLES: tuple[SwarmRole, ...] = (
    SwarmRole.RESEARCHER,
    SwarmRole.IMPLEMENTER,
    SwarmRole.REVIEWER,
    SwarmRole.TESTER,
)


class SwarmError(Exception):
    """Raised when a swarm operation fails."""


class SwarmManager:
    """Tracks swarm state across one or more concurrent swarms.

    Wraps the subagent fork/join mechanism with role specialization and
    group-level operations (broadcast to all workers in a swarm).
    """

    def __init__(self) -> None:
        self._swarms: dict[str, SwarmState] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_swarm(
        self,
        goal: str,
        worktree: str,
        tmux_session: str | None = None,
        roles: tuple[SwarmRole, ...] | list[SwarmRole] | None = None,
        *,
        dry_run: bool = False,
    ) -> SwarmState:
        """Create a swarm for ``goal`` in ``worktree``.

        Builds a coordinator + specialized workers with role-specific prompts.
        If ``dry_run`` is False and ``tmux_session`` is provided, workers are
        started in tmux panes; otherwise they remain in PENDING state.

        Args:
            goal: High-level goal (e.g. "Implement JWT auth").
            worktree: Name of the worktree the swarm runs in.
            tmux_session: Tmux session to spawn worker panes in.
            roles: Specialist roles to include (default: researcher, implementer,
                reviewer, tester). The coordinator is always added.
            dry_run: If True, build state without touching tmux.

        Returns:
            The created SwarmState.
        """
        if not goal.strip():
            raise SwarmError("Swarm goal must be non-empty")

        specialist_roles = tuple(roles) if roles is not None else DEFAULT_ROLES

        # Coordinator must not appear twice; filter it out of the specialist list
        specialist_roles = tuple(r for r in specialist_roles if r != SwarmRole.COORDINATOR)

        swarm_id = f"swarm-{uuid.uuid4().hex[:8]}"

        # Build the roster listing that the coordinator prompt references
        roster_lines = [f"- {r.value}" for r in specialist_roles]
        worker_roster = "\n".join(roster_lines) if roster_lines else "- (no specialists)"

        # Build coordinator first so its prompt includes the roster
        coordinator_worker = self._build_worker(
            swarm_id=swarm_id,
            role=SwarmRole.COORDINATOR,
            index=0,
            goal=goal,
            worker_roster=worker_roster,
        )
        workers: list[SwarmWorker] = [coordinator_worker]

        for index, role in enumerate(specialist_roles):
            worker = self._build_worker(
                swarm_id=swarm_id,
                role=role,
                index=index,
                goal=goal,
                worker_roster=worker_roster,
            )
            workers.append(worker)

        state = SwarmState(
            swarm_id=swarm_id,
            goal=goal,
            worktree=worktree,
            coordinator_id=coordinator_worker.id,
            workers=workers,
        )

        if not dry_run and tmux_session:
            for worker in state.workers:
                self._start_worker_in_tmux(worker, tmux_session)

        self._swarms[swarm_id] = state
        logger.info(
            "Started swarm '%s' for goal '%s' in worktree '%s' (%d workers)",
            swarm_id,
            goal,
            worktree,
            len(workers),
        )
        return state

    def stop_swarm(self, swarm_id: str) -> bool:
        """Stop all workers in a swarm and remove it from tracking.

        Returns True if the swarm existed, False otherwise.
        """
        state = self._swarms.pop(swarm_id, None)
        if state is None:
            return False
        for worker in state.workers:
            if worker.tmux_pane_id:
                self._kill_pane(worker.tmux_pane_id)
            worker.status = SwarmWorkerStatus.DONE
        logger.info("Stopped swarm '%s'", swarm_id)
        return True

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_swarm(self, swarm_id: str) -> SwarmState | None:
        return self._swarms.get(swarm_id)

    def list_swarms(self) -> list[SwarmState]:
        return list(self._swarms.values())

    def find_swarm_by_worktree(self, worktree: str) -> SwarmState | None:
        for state in self._swarms.values():
            if state.worktree == worktree:
                return state
        return None

    def find_worker_by_id(self, worker_id: str) -> tuple[SwarmState, SwarmWorker] | None:
        """Look up the swarm and worker for a given worker id."""
        for state in self._swarms.values():
            for worker in state.workers:
                if worker.id == worker_id:
                    return state, worker
        return None

    # ------------------------------------------------------------------
    # Broadcasts
    # ------------------------------------------------------------------

    def broadcast(
        self,
        swarm_id: str,
        message: str,
        *,
        include_coordinator: bool = True,
    ) -> list[SwarmWorker]:
        """Broadcast a message to every worker in the swarm.

        Returns the list of workers that received the message. If the swarm
        has no tmux session (dry-run), this is a no-op that still returns
        the target workers.
        """
        state = self._swarms.get(swarm_id)
        if state is None:
            raise SwarmError(f"Unknown swarm: {swarm_id}")
        targets = state.workers if include_coordinator else [w for w in state.workers if w.id != state.coordinator_id]
        for worker in targets:
            self._send_to_worker(worker, message)
        return targets

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_worker(
        self,
        *,
        swarm_id: str,
        role: SwarmRole,
        index: int,
        goal: str,
        worker_roster: str,
    ) -> SwarmWorker:
        prompt = build_swarm_prompt(
            role.value,
            goal=goal,
            swarm_id=swarm_id,
            worker_roster=worker_roster,
        )
        return SwarmWorker(
            id=f"{swarm_id}:{role.value}:{index}",
            role=role,
            prompt=prompt,
        )

    def _start_worker_in_tmux(self, worker: SwarmWorker, session_name: str) -> None:
        """Start a worker in a tmux pane. Coordinator stays in the main pane."""
        import subprocess as sp

        worker.tmux_session = session_name
        worker.started_at = datetime.now()
        worker.updated_at = datetime.now()

        if worker.role == SwarmRole.COORDINATOR:
            # Coordinator runs in the main pane of the session
            worker.status = SwarmWorkerStatus.WORKING
            try:
                from open_orchestrator.core.tmux_manager import TmuxManager

                tmux = TmuxManager()
                tmux.paste_to_pane(session_name=session_name, text=worker.prompt)
            except Exception as exc:
                logger.warning("Coordinator prompt delivery failed: %s", exc)
                worker.status = SwarmWorkerStatus.FAILED
            return

        # Specialists get their own tmux pane via split-window
        try:
            result = sp.run(  # noqa: S603 - trusted tmux args
                ["tmux", "split-window", "-t", session_name, "-P", "-F", "#{pane_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "tmux split-window failed")
            worker.tmux_pane_id = result.stdout.strip()
            worker.status = SwarmWorkerStatus.WORKING

            from open_orchestrator.core.tmux_manager import TmuxManager

            tmux = TmuxManager()
            tmux.paste_to_pane(session_name=session_name, text=worker.prompt)
        except (sp.SubprocessError, OSError, RuntimeError) as exc:
            logger.warning("Could not start swarm worker '%s': %s", worker.id, exc)
            worker.status = SwarmWorkerStatus.FAILED

    @staticmethod
    def _send_to_worker(worker: SwarmWorker, message: str) -> None:
        if not worker.tmux_session:
            return
        try:
            from open_orchestrator.core.tmux_manager import TmuxManager

            tmux = TmuxManager()
            tmux.paste_to_pane(session_name=worker.tmux_session, text=message)
        except Exception as exc:
            logger.debug("Broadcast to worker '%s' failed: %s", worker.id, exc)

    @staticmethod
    def _kill_pane(pane_id: str) -> None:
        import subprocess as sp

        try:
            sp.run(  # noqa: S603 - trusted tmux args
                ["tmux", "kill-pane", "-t", pane_id],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (sp.SubprocessError, OSError) as exc:
            logger.debug("kill-pane failed for %s: %s", pane_id, exc)
