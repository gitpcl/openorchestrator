"""Agno-powered intelligence layer for OWT.

Provides AI-powered planning, quality gating, and conflict resolution
using Agno agents with structured outputs. All features are optional —
if agno is not installed, callers fall back to existing behavior.

Usage:
    pip install open-orchestrator[agno]
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import toml

from open_orchestrator.config import AgnoConfig
from open_orchestrator.core.batch import _build_task_index, _parse_tasks, _validate_dag
from open_orchestrator.models.intelligence import (
    ConflictResolution,
    CoordinationAction,
    QualityVerdict,
    TaskPlan,
)

logger = logging.getLogger(__name__)


# ─── Model Resolution ─────────────────────────────────────────────────────


def _resolve_model(  # type: ignore[no-untyped-def]
    model_id: str,
    max_tokens: int = 4096,
    temperature: float = 0.2,
):
    """Resolve a model ID string to an Agno model instance.

    Agno is model-agnostic — this maps provider prefixes to the right class.
    max_tokens and temperature are passed directly to the model constructor.
    """
    kwargs = {"id": model_id, "max_tokens": max_tokens, "temperature": temperature}

    if model_id.startswith("claude"):
        from agno.models.anthropic import Claude  # type: ignore[import-not-found]

        return Claude(**kwargs)
    if model_id.startswith("gpt") or model_id.startswith("o"):
        from agno.models.openai import OpenAIChat  # type: ignore[import-not-found]

        return OpenAIChat(**kwargs)
    if model_id.startswith("gemini"):
        from agno.models.google import Gemini  # type: ignore[import-not-found]

        return Gemini(**kwargs)
    # Default to Claude
    from agno.models.anthropic import Claude

    return Claude(**kwargs)


# ─── Memory Helpers ───────────────────────────────────────────────────────


def _get_memory_db(agno_config: AgnoConfig, repo_path: str) -> Any | None:
    """Create Agno SqliteDb for persistent memory. Returns None if disabled."""
    if not agno_config.memory_enabled:
        return None
    try:
        from agno.db.sqlite import SqliteDb  # type: ignore[import-not-found]
    except ImportError:
        return None
    db_path = agno_config.memory_db_path or str(
        Path.home() / ".open-orchestrator" / "agno_memory.db"
    )
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteDb(db_file=db_path)


def _get_repo_name(repo_path: str) -> str:
    """Derive a stable repo identifier for memory scoping."""
    return Path(repo_path).resolve().name


def _build_memory_context(
    config: AgnoConfig,
    repo_path: str | None,
    session_id: str,
    instruction: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build memory kwargs for Agent constructor, run(), and prompt instruction.

    Returns:
        (agent_kwargs, run_kwargs, memory_instruction) — all empty if disabled.
    """
    if not repo_path:
        return {}, {}, ""
    db = _get_memory_db(config, repo_path)
    if not db:
        return {}, {}, ""
    return (
        {"db": db, "enable_agentic_memory": True},
        {"user_id": _get_repo_name(repo_path), "session_id": session_id},
        instruction,
    )


# ─── Codebase Tools (exposed to Agno agents) ──────────────────────────────


def _read_file(path: str, max_lines: int = 200) -> str:
    """Read a file from the repository.

    Args:
        path: Absolute or relative path to the file.
        max_lines: Maximum number of lines to return. Defaults to 200.

    Returns:
        File contents (truncated to max_lines).
    """
    try:
        p = Path(path)
        if not p.exists():
            return f"Error: File not found: {path}"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n\n... ({len(lines) - max_lines} more lines)"
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading file: {e}"


def _list_directory(path: str, max_depth: int = 2) -> str:
    """List files and directories in a path.

    Args:
        path: Directory path to list.
        max_depth: Maximum depth to recurse. Defaults to 2.

    Returns:
        Tree-formatted directory listing.
    """
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".ruff_cache", "dist", "build"}
    result: list[str] = []

    def _walk(p: Path, depth: int, prefix: str) -> None:
        if depth > max_depth or len(result) >= 500:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            return
        for entry in entries:
            if len(result) >= 500:
                return
            if entry.name in skip_dirs:
                continue
            if entry.name.startswith(".") and entry.name not in (".env.example",):
                continue
            result.append(f"{prefix}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                _walk(entry, depth + 1, prefix + "  ")

    root = Path(path)
    if not root.is_dir():
        return f"Error: Not a directory: {path}"
    result.append(f"{root.name}/")
    _walk(root, 1, "  ")
    return "\n".join(result)


def _git_log(repo_path: str, count: int = 20) -> str:
    """Get recent git commit log.

    Args:
        repo_path: Path to the git repository.
        count: Number of commits to show. Defaults to 20.

    Returns:
        One-line commit log.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{count}"],
            capture_output=True, text=True, cwd=repo_path, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr}"
    except Exception as e:
        return f"Error: {e}"


def _git_diff_stat(repo_path: str, branch: str, base: str = "main") -> str:
    """Get diff stat between two branches.

    Args:
        repo_path: Path to the git repository.
        branch: The feature branch.
        base: The base branch. Defaults to main.

    Returns:
        Diff stat summary.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"{base}...{branch}"],
            capture_output=True, text=True, cwd=repo_path, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr}"
    except Exception as e:
        return f"Error: {e}"


# ─── AgnoPlanner ───────────────────────────────────────────────────────────


class AgnoPlanner:
    """AI-powered task decomposition using Agno with structured outputs."""

    def __init__(self, agno_config: AgnoConfig, repo_path: str | None = None):
        self.config = agno_config
        self._repo_path = repo_path

    def plan(
        self,
        goal: str,
        repo_path: str,
        output_path: str | Path | None = None,
        ai_tool: str = "claude",
    ) -> Path:
        """Decompose a goal into a dependency-aware task DAG.

        Args:
            goal: The feature/goal description.
            repo_path: Path to the repository.
            output_path: Where to write the TOML file.
            ai_tool: AI tool for task execution (not for planning).

        Returns:
            Path to the generated plan.toml.
        """
        from agno.agent import Agent  # type: ignore[import-not-found]

        output_path = Path(output_path) if output_path else Path(repo_path) / "plan.toml"

        # Gather context
        file_tree = _list_directory(repo_path, max_depth=2)
        recent_commits = _git_log(repo_path, count=15)

        model_id = self.config.planner_model_id or self.config.model_id
        model = _resolve_model(model_id, self.config.max_tokens, self.config.temperature)

        agent_mem, run_mem, mem_instr = _build_memory_context(
            self.config, repo_path, "planner",
            "\n\nMEMORY: If you have memories of past plans for this repo, "
            "use them to avoid decompositions that caused merge conflicts "
            "and reuse successful file-grouping patterns.",
        )

        agent = Agent(
            model=model,
            tools=[_read_file, _list_directory, _git_log],
            output_schema=TaskPlan,
            description="You are a software architect that decomposes goals into parallel tasks for AI coding agents.",
            instructions=f"""Decompose the goal into tasks that can run in parallel git worktrees.

GOAL: {goal}

CODEBASE STRUCTURE:
{file_tree}

RECENT COMMITS:
{recent_commits}

Rules:
- Each task runs in its own git branch with its own AI agent
- Keep tasks focused (1-3 files each)
- Maximize parallelism — only add depends_on when truly needed
- Use short, descriptive IDs (lowercase, hyphens)
- 3-8 tasks is ideal
- Description should be a complete instruction an AI agent can act on
- Set ai_tool = "{ai_tool}" for every task
- estimated_files should list files likely to be modified (helps detect overlaps)
- Use the tools to explore the codebase if you need more context{mem_instr}""",
            add_datetime_to_context=True,
            **agent_mem,
        )

        response = agent.run(goal, **run_mem)
        task_plan: TaskPlan = response.content

        # Convert to TOML format matching existing batch config
        toml_data = self._plan_to_toml(task_plan)

        # Validate via existing DAG machinery
        tasks = _parse_tasks(toml_data)
        index = _build_task_index(list(tasks))
        _validate_dag(list(tasks), index)

        output_path.write_text(toml.dumps(toml_data))
        return output_path

    def _plan_to_toml(self, plan: TaskPlan) -> dict[str, Any]:
        """Convert a TaskPlan to TOML-compatible dict."""
        tasks = []
        for t in plan.tasks:
            task_dict: dict[str, Any] = {
                "id": t.id,
                "description": t.description,
                "ai_tool": t.ai_tool,
                "depends_on": t.depends_on,
            }
            tasks.append(task_dict)
        return {
            "batch": {
                "max_concurrent": plan.max_concurrent,
                "auto_ship": True,
            },
            "tasks": tasks,
        }


# ─── AgnoQualityGate ──────────────────────────────────────────────────────


class AgnoQualityGate:
    """AI-powered quality review before shipping worktree changes."""

    def __init__(self, agno_config: AgnoConfig, repo_path: str | None = None):
        self.config = agno_config
        self.repo_path = repo_path

    def review(
        self,
        diff: str,
        task_description: str | None = None,
        active_worktrees: list[dict[str, str]] | None = None,
    ) -> QualityVerdict:
        """Review a diff for quality and cross-worktree conflicts.

        Args:
            diff: The git diff to review.
            task_description: What the worktree was working on.
            active_worktrees: List of dicts with 'name', 'branch', 'task' for other worktrees.

        Returns:
            QualityVerdict with score, pass/fail, and feedback.
        """
        from agno.agent import Agent

        model_id = self.config.quality_gate_model_id or self.config.model_id
        model = _resolve_model(model_id, self.config.max_tokens, self.config.temperature)

        worktree_context = ""
        if active_worktrees:
            lines = [f"- {wt.get('name', '?')}: {wt.get('task', 'unknown')}" for wt in active_worktrees]
            worktree_context = "\n\nOTHER ACTIVE WORKTREES:\n" + "\n".join(lines)

        task_context = f"\nTASK: {task_description}" if task_description else ""

        agent_mem, run_mem, mem_instr = _build_memory_context(
            self.config, self.repo_path, "quality-gate",
            "\n\nMEMORY: Reduce false positives based on past reviews. "
            "Apply repo-specific coding standards you've learned.",
        )

        agent = Agent(
            model=model,
            output_schema=QualityVerdict,
            description="You are a senior code reviewer evaluating changes before they ship to main.",
            instructions=f"""Review this diff for quality. Check for:
1. Code completeness — are there TODOs, partial implementations, or debug code?
2. Obvious bugs or logic errors
3. Security issues (hardcoded secrets, injection vulnerabilities)
4. Whether the changes seem consistent and purposeful

Score 0.0-1.0 where:
- 0.0-0.3: Critical issues, should not ship
- 0.3-0.7: Issues found, needs attention
- 0.7-1.0: Good quality, safe to ship
{task_context}{worktree_context}

DIFF:
{diff[:8000]}{mem_instr}""",
            **agent_mem,
        )

        response = agent.run("Review the diff above and provide your quality verdict.", **run_mem)
        verdict: QualityVerdict = response.content

        # Override passed based on threshold
        verdict.passed = verdict.score >= self.config.quality_gate_threshold
        return verdict


# ─── AgnoConflictResolver ─────────────────────────────────────────────────


class AgnoConflictResolver:
    """AI-powered semantic merge conflict resolution."""

    def __init__(self, agno_config: AgnoConfig, repo_path: str | None = None):
        self.config = agno_config
        self.repo_path = repo_path

    def resolve(
        self,
        conflicted_files: dict[str, str],
        source_branch: str,
        target_branch: str,
        task_description: str | None = None,
    ) -> ConflictResolution:
        """Attempt to resolve merge conflicts semantically.

        Args:
            conflicted_files: Dict of file_path -> conflicted file contents (with markers).
            source_branch: The branch being merged.
            target_branch: The branch being merged into.
            task_description: What the source branch was working on.

        Returns:
            ConflictResolution with resolved contents and confidence.
        """
        from agno.agent import Agent

        model_id = self.config.model_id
        model = _resolve_model(model_id, self.config.max_tokens, self.config.temperature)

        files_context = ""
        for path, content in conflicted_files.items():
            # Truncate very large files
            truncated = content[:4000] if len(content) > 4000 else content
            files_context += f"\n\n--- {path} ---\n{truncated}"

        task_context = f"\nThe source branch was working on: {task_description}" if task_description else ""

        agent_mem, run_mem, mem_instr = _build_memory_context(
            self.config, self.repo_path, "conflict-resolver",
            "\n\nMEMORY: Apply resolution patterns that had high confidence before.",
        )

        agent = Agent(
            model=model,
            output_schema=ConflictResolution,
            description="You are a merge conflict resolution expert.",
            instructions=f"""Resolve the merge conflicts in the files below.

SOURCE BRANCH: {source_branch}
TARGET BRANCH: {target_branch}
{task_context}

CONFLICTED FILES:
{files_context}

Rules:
- Preserve the intent of BOTH branches where possible
- If a conflict is ambiguous, set requires_human=True and confidence low
- In resolutions dict, provide the COMPLETE resolved file content for each file
- Only resolve files you are confident about (confidence > 0.8)
- If you cannot resolve confidently, set requires_human=True{mem_instr}""",
            **agent_mem,
        )

        response = agent.run("Resolve the merge conflicts above.", **run_mem)
        result: ConflictResolution = response.content
        return result


# ─── AgnoCoordinator ──────────────────────────────────────────────────────


class AgnoCoordinator:
    """AI-powered cross-worktree coordination.

    Invoked only when events are detected (file overlaps, status transitions).
    Produces context messages to inject into worktree CLAUDE.md files.
    """

    def __init__(self, agno_config: AgnoConfig, repo_path: str | None = None):
        self.config = agno_config
        self.repo_path = repo_path

    def analyze(
        self,
        events: list[tuple[str, str]],
        running_worktrees: list[dict[str, str]],
    ) -> list[CoordinationAction]:
        """Analyze cross-worktree events and produce coordination actions.

        Args:
            events: List of (event_key, description) pairs to analyze.
            running_worktrees: List of dicts with 'name', 'task', 'branch'.

        Returns:
            List of CoordinationAction with target worktrees and messages.
        """
        from agno.agent import Agent  # type: ignore[import-not-found]

        model_id = self.config.coordinator_model_id or self.config.model_id
        model = _resolve_model(model_id, self.config.max_tokens, self.config.temperature)

        wt_context = "\n".join(
            f"- {wt.get('name', '?')} ({wt.get('branch', '?')}): {wt.get('task', 'unknown')}"
            for wt in running_worktrees
        )
        event_context = "\n".join(f"- [{key}] {desc}" for key, desc in events)

        # Gather diffs for context
        diff_context = ""
        if self.repo_path:
            for wt in running_worktrees:
                branch = wt.get("branch", "")
                if branch:
                    diff = _git_diff_stat(self.repo_path, branch, "main")
                    if diff and "Error" not in diff:
                        diff_context += f"\n--- {wt.get('name', '?')} diff stat ---\n{diff}\n"

        agent_mem, run_mem, mem_instr = _build_memory_context(
            self.config, self.repo_path, "coordinator",
            "\n\nMEMORY: Use past coordination patterns. Avoid repeating messages "
            "that agents already acknowledged.",
        )

        agent = Agent(
            model=model,
            tools=[_read_file, _git_diff_stat],
            output_schema=list[CoordinationAction],
            description="You are a cross-worktree coordinator for parallel AI agents.",
            instructions=f"""Analyze these events and decide what context each worktree needs.

RUNNING WORKTREES:
{wt_context}

DETECTED EVENTS:
{event_context}
{diff_context}

Rules:
- Only send actionable context. Reference specific file names.
- Explain WHAT changed and WHO needs to know.
- critical = agent must stop and read NOW
- warning = be aware, may affect your work
- info = FYI, no action needed
- Target only worktrees that are actually affected
- Be concise: one sentence per message{mem_instr}""",
            **agent_mem,
        )

        response = agent.run("Analyze the events and produce coordination actions.", **run_mem)
        result: list[CoordinationAction] = response.content
        return result
