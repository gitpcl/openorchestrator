"""Subagent fork-join lifecycle management.

Provides SubagentManager for spawning lightweight child agents
within existing tmux sessions. Subagents run in tmux panes (not
separate worktrees), making them cheap to create and tear down.

Used by:
- Critic pattern: spawn a critic subagent before ship/merge
- Dream mode: consolidation agent reviews across worktrees
- Swarm mode: specialized workers for parallel tasks
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from open_orchestrator.models.subagent import (
    ForkJoinRequest,
    SubagentResult,
    SubagentRole,
    SubagentState,
    SubagentStatus,
)

logger = logging.getLogger(__name__)

# Context trimming: max chars inherited from parent
MAX_CONTEXT_CHARS = 4000


class SubagentError(Exception):
    """Raised when a subagent operation fails."""


class SubagentManager:
    """Manages the lifecycle of forked subagents.

    Subagents are tmux panes within an existing session, not full
    worktrees. This makes fork/join cheap (~100ms per agent).
    """

    def __init__(self) -> None:
        self._agents: dict[str, SubagentState] = {}

    @property
    def active_count(self) -> int:
        """Number of currently running subagents."""
        return sum(1 for a in self._agents.values() if a.status == SubagentStatus.RUNNING)

    def list_agents(self, *, parent: str | None = None) -> list[SubagentState]:
        """List all tracked subagents, optionally filtered by parent."""
        agents = list(self._agents.values())
        if parent:
            agents = [a for a in agents if a.parent_name == parent]
        return agents

    def get_agent(self, agent_id: str) -> SubagentState | None:
        """Get a subagent by ID."""
        return self._agents.get(agent_id)

    # ── Fork ────────────────────────────────────────────────────────

    def fork(
        self,
        parent_name: str,
        role: SubagentRole,
        prompt: str,
        *,
        context: str = "",
        timeout_seconds: int = 300,
        tmux_session: str | None = None,
    ) -> SubagentState:
        """Fork a new subagent.

        Creates a SubagentState and optionally starts it in a tmux pane.
        If tmux_session is provided, the agent is started immediately.
        Otherwise it stays in PENDING state for manual start.

        Args:
            parent_name: Name of the parent worktree/session.
            role: Role specialization for the subagent.
            prompt: Task prompt to send.
            context: Parent context to inject (trimmed to MAX_CONTEXT_CHARS).
            timeout_seconds: Max runtime before timeout.
            tmux_session: tmux session to create pane in (None = dry run).

        Returns:
            The created SubagentState.
        """
        # Generate unique ID
        role_count = sum(1 for a in self._agents.values() if a.parent_name == parent_name and a.role == role)
        agent_id = f"{parent_name}:{role.value}:{role_count}"

        # Build full prompt with context inheritance
        full_prompt = self._build_prompt(role, prompt, context)

        state = SubagentState(
            id=agent_id,
            parent_name=parent_name,
            role=role,
            prompt=full_prompt,
            timeout_seconds=timeout_seconds,
        )

        if tmux_session:
            state = self._start_in_tmux(state, tmux_session)

        self._agents[agent_id] = state
        logger.info("Forked subagent '%s' (role=%s, timeout=%ds)", agent_id, role.value, timeout_seconds)
        return state

    def fork_join(self, request: ForkJoinRequest) -> list[SubagentResult]:
        """Fork multiple subagents and wait for all to complete.

        This is a synchronous blocking call that:
        1. Forks all specified agents
        2. Polls until all reach terminal state or timeout
        3. Collects and returns results

        Args:
            request: ForkJoinRequest with agent specifications.

        Returns:
            List of SubagentResult for each forked agent.
        """
        # Fork all agents
        forked: list[SubagentState] = []
        for spec in request.agents:
            timeout = spec.timeout_seconds or request.timeout_seconds
            state = self.fork(
                parent_name=request.parent_name,
                role=spec.role,
                prompt=spec.prompt,
                context=request.context,
                timeout_seconds=timeout,
            )
            forked.append(state)

        # Poll until all terminal
        deadline = time.monotonic() + request.timeout_seconds
        while time.monotonic() < deadline:
            self._check_timeouts()
            if all(self._agents[a.id].is_terminal for a in forked):
                break
            time.sleep(1)
        else:
            # Force-timeout any still running
            for agent in forked:
                current = self._agents[agent.id]
                if not current.is_terminal:
                    self._timeout_agent(current)

        return [self._collect_result(self._agents[a.id]) for a in forked]

    # ── Join & Collect ──────────────────────────────────────────────

    def join(self, agent_id: str) -> SubagentResult | None:
        """Collect the result from a single subagent. Returns None if not found."""
        agent = self._agents.get(agent_id)
        if agent is None:
            return None
        return self._collect_result(agent)

    def join_all(self, parent_name: str) -> list[SubagentResult]:
        """Collect results from all subagents of a parent."""
        agents = [a for a in self._agents.values() if a.parent_name == parent_name]
        return [self._collect_result(a) for a in agents]

    def mark_completed(self, agent_id: str, output: str = "") -> bool:
        """Mark a subagent as completed with output. Returns False if not found."""
        agent = self._agents.get(agent_id)
        if agent is None or agent.is_terminal:
            return False
        agent.status = SubagentStatus.COMPLETED
        agent.output = output
        agent.completed_at = datetime.now()
        logger.info("Subagent '%s' completed (%.1fs)", agent_id, agent.elapsed_seconds)
        return True

    def mark_failed(self, agent_id: str, error: str = "") -> bool:
        """Mark a subagent as failed. Returns False if not found."""
        agent = self._agents.get(agent_id)
        if agent is None or agent.is_terminal:
            return False
        agent.status = SubagentStatus.FAILED
        agent.error = error
        agent.completed_at = datetime.now()
        logger.warning("Subagent '%s' failed: %s", agent_id, error)
        return True

    # ── Timeout & Cleanup ───────────────────────────────────────────

    def _check_timeouts(self) -> list[str]:
        """Check all running agents for timeout. Returns IDs of timed-out agents."""
        timed_out: list[str] = []
        for agent in self._agents.values():
            if agent.is_timed_out:
                self._timeout_agent(agent)
                timed_out.append(agent.id)
        return timed_out

    def _timeout_agent(self, agent: SubagentState) -> None:
        """Mark an agent as timed out and kill its tmux pane."""
        agent.status = SubagentStatus.TIMED_OUT
        agent.error = f"Exceeded {agent.timeout_seconds}s timeout"
        agent.completed_at = datetime.now()
        logger.warning("Subagent '%s' timed out after %.1fs", agent.id, agent.elapsed_seconds)

        if agent.tmux_pane_id:
            self._kill_pane(agent.tmux_pane_id)

    def cleanup(self, parent_name: str) -> int:
        """Remove all terminal subagents for a parent. Returns count removed.

        Kills any remaining tmux panes and removes agent state.
        """
        to_remove = [
            agent_id for agent_id, agent in self._agents.items() if agent.parent_name == parent_name and agent.is_terminal
        ]
        for agent_id in to_remove:
            agent = self._agents.pop(agent_id)
            if agent.tmux_pane_id:
                self._kill_pane(agent.tmux_pane_id)
        if to_remove:
            logger.info("Cleaned up %d subagent(s) for '%s'", len(to_remove), parent_name)
        return len(to_remove)

    def cleanup_all(self) -> int:
        """Remove all terminal subagents across all parents."""
        terminal_ids = [agent_id for agent_id, agent in self._agents.items() if agent.is_terminal]
        for agent_id in terminal_ids:
            agent = self._agents.pop(agent_id)
            if agent.tmux_pane_id:
                self._kill_pane(agent.tmux_pane_id)
        return len(terminal_ids)

    # ── Context Inheritance ─────────────────────────────────────────

    @staticmethod
    def _build_prompt(role: SubagentRole, prompt: str, context: str) -> str:
        """Build a role-specific prompt with inherited context.

        Trims parent context to MAX_CONTEXT_CHARS to minimize token usage.
        """
        parts: list[str] = []

        # Role preamble
        role_preambles = {
            SubagentRole.RESEARCH: "You are a research subagent. Gather information and report findings concisely.",
            SubagentRole.SYNTHESIS: "You are a synthesis subagent. Combine inputs into a coherent output.",
            SubagentRole.CRITIC: "You are a critic subagent. Review the work and identify issues, risks, and improvements.",
            SubagentRole.WORKER: "You are a worker subagent. Execute the assigned task efficiently.",
            SubagentRole.PLANNER: "You are a planning subagent. Break down the goal into actionable steps.",
        }
        parts.append(role_preambles.get(role, f"You are a {role.value} subagent."))

        # Trimmed parent context
        if context:
            trimmed = context[:MAX_CONTEXT_CHARS]
            if len(context) > MAX_CONTEXT_CHARS:
                trimmed = trimmed.rsplit("\n", 1)[0] + "\n[context trimmed]"
            parts.append(f"\n## Parent Context\n\n{trimmed}")

        # Task prompt
        parts.append(f"\n## Task\n\n{prompt}")

        return "\n\n".join(parts)

    @staticmethod
    def build_context_from_worktree(worktree_path: str | Path) -> str:
        """Extract context from a worktree for injection into subagents.

        Reads CLAUDE.md and recent git log to build a context summary.
        """
        path = Path(worktree_path)
        parts: list[str] = []

        # CLAUDE.md content (trimmed)
        claude_md = path / ".claude" / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text()[:2000]
            parts.append(f"### Project Context\n{content}")

        # Recent git log
        try:
            import subprocess

            result = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                cwd=str(path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts.append(f"### Recent Commits\n{result.stdout.strip()}")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.debug("Could not read git log: %s", exc)

        return "\n\n".join(parts)

    # ── TMux Integration ───────────────────────────────────────────

    def _start_in_tmux(self, state: SubagentState, session_name: str) -> SubagentState:
        """Start a subagent in a tmux pane within the given session.

        Creates a split pane via subprocess, sends the prompt, and updates state.
        """
        import subprocess as sp

        state.tmux_session = session_name
        state.status = SubagentStatus.RUNNING
        state.started_at = datetime.now()
        state.last_heartbeat = datetime.now()

        try:
            # Split the current window to create a new pane
            result = sp.run(
                ["tmux", "split-window", "-t", session_name, "-P", "-F", "#{pane_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip())
            pane_id = result.stdout.strip()
            state.tmux_pane_id = pane_id

            # Paste the prompt into the new pane
            from open_orchestrator.core.tmux_manager import TmuxManager

            tmux = TmuxManager()
            tmux.paste_to_pane(session_name=session_name, text=state.prompt)
        except Exception as exc:
            logger.warning("Could not start subagent '%s' in tmux: %s", state.id, exc)
            state.status = SubagentStatus.FAILED
            state.error = f"tmux start failed: {exc}"
            state.completed_at = datetime.now()

        return state

    @staticmethod
    def _kill_pane(pane_id: str) -> None:
        """Kill a tmux pane by ID."""
        import subprocess as sp

        try:
            sp.run(
                ["tmux", "kill-pane", "-t", pane_id],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:
            logger.debug("Could not kill pane %s: %s", pane_id, exc)

    # ── Utility ─────────────────────────────────────────────────────

    @staticmethod
    def _collect_result(agent: SubagentState) -> SubagentResult:
        """Convert a SubagentState to a SubagentResult."""
        return SubagentResult(
            id=agent.id,
            role=agent.role,
            status=agent.status,
            output=agent.output or "",
            elapsed_seconds=agent.elapsed_seconds,
        )
