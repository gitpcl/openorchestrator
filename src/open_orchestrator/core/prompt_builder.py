"""Context-aware prompt assembly for AI coding agents.

Provides PromptBuilder for priority-based section assembly with token
budget control, and classify_task() for deterministic task-type heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class TaskType(str, Enum):
    """Deterministic task-type classification."""

    BUGFIX = "bugfix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST = "test"
    DOCS = "docs"


# Keyword patterns: checked in order, first match wins.
_TASK_PATTERNS: list[tuple[re.Pattern[str], TaskType]] = [
    (re.compile(r"\b(fix|bug|broken|crash|error|issue|regression|hotfix)\b", re.I), TaskType.BUGFIX),
    (re.compile(r"\b(test|spec|coverage|assert)\b", re.I), TaskType.TEST),
    (re.compile(r"\b(doc|readme|changelog|comment|guide|tutorial)\b", re.I), TaskType.DOCS),
    (re.compile(r"\b(refactor|clean|reorganize|simplify|extract|rename|move)\b", re.I), TaskType.REFACTOR),
    (re.compile(r"\b(add|implement|create|build|feature|integrate|support|enable)\b", re.I), TaskType.FEATURE),
]


def classify_task(description: str) -> TaskType:
    """Classify a task description into a TaskType using keyword heuristics.

    Deterministic and fast — no AI calls. First matching pattern wins.
    Falls back to FEATURE for unrecognized descriptions.
    """
    for pattern, task_type in _TASK_PATTERNS:
        if pattern.search(description):
            return task_type
    return TaskType.FEATURE


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: word count * 1.3."""
    return int(len(text.split()) * 1.3)


@dataclass(frozen=True)
class _Section:
    """A single prompt section with priority for budget-based assembly."""

    name: str
    content: str
    priority: int  # higher = more important, kept first


@dataclass(frozen=True)
class PromptBuilder:
    """Immutable prompt builder with priority-based section assembly.

    Sections are assembled in descending priority order. If a max_tokens
    budget is specified, lowest-priority sections are dropped first.

    Usage:
        prompt = (
            PromptBuilder()
            .add_section("role", "You are a coding agent.", priority=100)
            .add_section("task", "Implement JWT auth.", priority=90)
            .add_section("context", "The codebase uses Express.", priority=50)
            .build(max_tokens=2000)
        )
    """

    sections: tuple[_Section, ...] = ()

    def add_section(self, name: str, content: str, priority: int = 50) -> PromptBuilder:
        """Return a new builder with the section added."""
        return PromptBuilder(sections=(*self.sections, _Section(name, content, priority)))

    def build(self, max_tokens: int | None = None) -> str:
        """Assemble sections by descending priority, dropping lowest if over budget."""
        sorted_sections = sorted(self.sections, key=lambda s: s.priority, reverse=True)

        if max_tokens is None:
            return "\n\n".join(s.content for s in sorted_sections)

        parts: list[str] = []
        used = 0
        for section in sorted_sections:
            tokens = _estimate_tokens(section.content)
            if used + tokens > max_tokens:
                continue
            parts.append(section.content)
            used += tokens

        return "\n\n".join(parts)


# ─── Cross-cutting prompt sections ────────────────────────────────────────

COMMIT_SAFETY = (
    "## Commit Safety\n"
    "- NEVER use /commit or interactive commit — blocks indefinitely in automated mode\n"
    "- NEVER amend a previous commit — always create a NEW commit\n"
    "- If a pre-commit hook fails, fix the issue and create a NEW commit (not --amend)\n"
    "- Commit early and often — uncommitted work is lost when this session ends\n"
    "- Use: git add -A && git commit -m 'type: description'"
)

TURN_EFFICIENCY = (
    "## Turn Efficiency\n"
    "- Batch your reads: read all relevant files in a single turn\n"
    "- Batch your writes: make all edits in a single turn after reading\n"
    "- DO NOT: read a file, edit it, read the next file, edit it (wastes turns)\n"
    "- If multiple independent operations are needed, run them in parallel"
)

# ─── Task-type protocol templates ──────────────────────────────────────────

_PROTOCOLS: dict[TaskType, str] = {
    TaskType.BUGFIX: (
        "## Protocol: Bug Fix\n"
        "1. **REPRODUCE** — Write a failing test that demonstrates the bug\n"
        "   - DO: Isolate the minimal reproduction case\n"
        "   - DON'T: Skip reproduction — fixing without a test leads to regressions\n"
        "2. **DIAGNOSE** — Trace root cause (read error logs, stack traces)\n"
        "   - DO: Read the failing test output and trace to the source\n"
        "   - DON'T: Guess at the fix without reading the relevant code\n"
        "   - PARALLEL: Read all suspect files in one turn\n"
        "3. **FIX** — Make the minimal code change to fix the root cause\n"
        "   - DO: Change only what's necessary — smallest diff possible\n"
        "   - DON'T: Refactor surrounding code or add unrelated improvements\n"
        "4. **VERIFY** — Run the failing test — it should now pass\n"
        "   - DO: Run the specific test first, then the full suite\n"
        "   - DON'T: Skip the full suite — your fix may break something else\n"
        "5. **REGRESSION** — Run full test suite to ensure no regressions\n"
        "6. **COMMIT** — Commit with `fix:` prefix"
    ),
    TaskType.FEATURE: (
        "## Protocol: Feature Implementation\n"
        "1. **ORIENT** — Read .claude/CLAUDE.md and README.md\n"
        "   - DO: Check for project-specific test commands, conventions, dependencies\n"
        "   - DON'T: Skip this even if the task seems simple — missing context causes rework\n"
        "2. **EXPLORE** — Read source files relevant to your task\n"
        "   - DO: Read 2-3 files to understand patterns before writing any code\n"
        "   - DON'T: Start implementing before understanding the existing code style\n"
        "   - PARALLEL: If multiple files need reading, read them all in one turn\n"
        "3. **IMPLEMENT** — Write the feature following existing patterns\n"
        "   - DO: Follow existing naming conventions, file organization, and patterns\n"
        "   - DON'T: Introduce new patterns or abstractions not already in the codebase\n"
        "4. **TEST** — Write tests (aim for 80%+ coverage of new code)\n"
        "   - DO: Follow existing test patterns (fixtures, mocking style)\n"
        "   - DON'T: Write tests that depend on execution order or external state\n"
        "5. **VERIFY** — Run linter, type checker, full test suite\n"
        "   - DO: Fix all lint and type errors before committing\n"
        "6. **COMMIT** — Commit with `feat:` prefix"
    ),
    TaskType.REFACTOR: (
        "## Protocol: Refactoring\n"
        "1. **BASELINE** — Run tests to confirm current behavior works\n"
        "   - DO: Record the test count and pass rate before changing anything\n"
        "   - DON'T: Start refactoring without a green test suite\n"
        "2. **PLAN** — Identify what to change and verify no behavior change\n"
        "   - DO: List the specific files and functions you'll modify\n"
        "   - DON'T: Change behavior — refactoring is structure-only\n"
        "3. **REFACTOR** — Make structural changes in small, safe steps\n"
        "   - DO: Make one logical change at a time, verify tests after each\n"
        "   - DON'T: Make multiple unrelated changes in one commit\n"
        "   - PARALLEL: Read all files you plan to modify in one turn first\n"
        "4. **VERIFY** — Run tests after each step — no regressions\n"
        "5. **CLEANUP** — Remove dead code, update imports\n"
        "   - DO: Run the linter to catch unused imports\n"
        "6. **COMMIT** — Commit with `refactor:` prefix"
    ),
    TaskType.TEST: (
        "## Protocol: Test Writing\n"
        "1. **SURVEY** — Find existing test patterns and fixtures\n"
        "   - DO: Read conftest.py and 1-2 existing test files for patterns\n"
        "   - DON'T: Invent new test patterns when the project has established ones\n"
        "   - PARALLEL: Read conftest.py and test files in one turn\n"
        "2. **IDENTIFY** — List untested code paths and edge cases\n"
        "   - DO: Focus on the most critical paths first (happy path, error cases)\n"
        "3. **WRITE** — Write tests following project conventions\n"
        "   - DO: Use descriptive test names that explain what's being tested\n"
        "   - DON'T: Write tests that depend on file system state or network\n"
        "4. **RUN** — Execute tests, fix any failures\n"
        "   - DO: Run only your new tests first, then the full suite\n"
        "5. **COVERAGE** — Check coverage, add tests for gaps\n"
        "6. **COMMIT** — Commit with `test:` prefix"
    ),
    TaskType.DOCS: (
        "## Protocol: Documentation\n"
        "1. **READ** — Understand the code/feature to document\n"
        "   - DO: Read the actual implementation, not just function signatures\n"
        "   - PARALLEL: Read all relevant source files in one turn\n"
        "2. **DRAFT** — Write clear, concise documentation\n"
        "   - DO: Include usage examples and common pitfalls\n"
        "   - DON'T: Write vague descriptions — be specific about behavior\n"
        "3. **EXAMPLES** — Add code examples where helpful\n"
        "   - DO: Make examples runnable and self-contained\n"
        "4. **REVIEW** — Check for accuracy and completeness\n"
        "5. **COMMIT** — Commit with `docs:` prefix"
    ),
}


def get_protocol_for_task(description: str) -> str:
    """Get the task-type-specific protocol for a task description."""
    task_type = classify_task(description)
    return _PROTOCOLS[task_type]


# ─── Failure classification ───────────────────────────────────────────────

_FAILURE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"syntax\s*error|indentation|unexpected\s+token|parse\s*error", re.I), "syntax"),
    (re.compile(r"assert|test.*fail|expected.*got|!=|mismatch", re.I), "test_failure"),
    (re.compile(r"timeout|timed?\s*out|deadline\s*exceeded", re.I), "timeout"),
    (re.compile(r"import\s*error|module\s*not\s*found|no\s*module|dependency|package", re.I), "dependency"),
    (re.compile(r"runtime|traceback|exception|raise|error.*line\s+\d+", re.I), "runtime"),
]


def classify_failure(error: str) -> str:
    """Classify an error message into a failure category.

    Categories: syntax, test_failure, timeout, dependency, runtime, logic.
    Falls back to 'logic' for unrecognized errors.
    """
    for pattern, category in _FAILURE_PATTERNS:
        if pattern.search(error):
            return category
    return "logic"


# ─── Swarm role prompts (Sprint 018) ──────────────────────────────────────

_SWARM_COORDINATOR_TEMPLATE = (
    "## Role: Swarm Coordinator\n"
    "You own the goal and delegate work to specialized workers in your swarm.\n"
    "\n"
    "## Goal\n"
    "{goal}\n"
    "\n"
    "## Your Workers\n"
    "{worker_roster}\n"
    "\n"
    "## Protocol\n"
    "1. **DECOMPOSE** — Break the goal into discrete tasks per worker role:\n"
    "   - researcher: gather facts, APIs, libraries, related code\n"
    "   - implementer: write the production code\n"
    "   - reviewer: read-only review of implementer output\n"
    "   - tester: write and run tests for the implementation\n"
    "2. **DELEGATE** — Send role-scoped instructions to each worker via\n"
    "   `owt send --swarm {swarm_id} '<message>'` or direct messages.\n"
    "3. **MONITOR** — Poll worker status; re-delegate if a worker is idle.\n"
    "4. **SYNTHESIZE** — Combine worker outputs into the final deliverable.\n"
    "5. **VERIFY** — Ensure tests pass before declaring the goal met.\n"
    "6. **REPORT** — Summarize what each worker produced.\n"
    "\n"
    "## Constraints\n"
    "- Do NOT write production code yourself — that's the implementer's job.\n"
    "- Do NOT review code yourself — delegate to the reviewer.\n"
    "- Keep decomposition small and actionable; no more than 3 tasks per worker.\n"
)

_SWARM_RESEARCHER_TEMPLATE = (
    "## Role: Swarm Researcher\n"
    "You gather information the implementer will need. You do NOT write code.\n"
    "\n"
    "## Goal\n"
    "{goal}\n"
    "\n"
    "## Protocol\n"
    "1. Read 3-5 relevant files, docs, and related tests\n"
    "2. Identify existing patterns that should be reused\n"
    "3. List APIs, libraries, or modules the implementer should use\n"
    "4. Report findings in a concise structured summary:\n"
    "   - **Existing patterns:** ...\n"
    "   - **APIs to use:** ...\n"
    "   - **Pitfalls:** ...\n"
    "\n"
    "## Constraints\n"
    "- Read-only: do NOT edit or create files\n"
    "- Keep reports under 500 words\n"
    "- Report back to the coordinator when done\n"
)

_SWARM_IMPLEMENTER_TEMPLATE = (
    "## Role: Swarm Implementer\n"
    "You write the production code that addresses the goal.\n"
    "\n"
    "## Goal\n"
    "{goal}\n"
    "\n"
    "## Protocol\n"
    "1. Wait for the researcher's findings before starting implementation\n"
    "2. Follow existing codebase patterns and conventions\n"
    "3. Write clean, focused code — no speculative abstractions\n"
    "4. Keep files under 800 lines and functions under 50 lines\n"
    "5. Commit your changes with a conventional commit message\n"
    "6. Hand off to the tester when implementation is complete\n"
    "\n"
    "## Constraints\n"
    "- Modify only files under src/ and the production tree\n"
    "- Do NOT edit test files — that's the tester's responsibility\n"
    "- Do NOT perform code review — that's the reviewer's responsibility\n"
)

_SWARM_REVIEWER_TEMPLATE = (
    "## Role: Swarm Reviewer\n"
    "You review the implementer's changes. You are read-only.\n"
    "\n"
    "## Goal\n"
    "{goal}\n"
    "\n"
    "## Protocol\n"
    "1. Run `git diff` to inspect implementer changes\n"
    "2. Check for: correctness, security issues, style violations, missing edge cases\n"
    "3. Verify the code matches the goal and researcher's guidance\n"
    "4. Report a structured review to the coordinator:\n"
    "   - **Blockers:** critical issues that must be fixed\n"
    "   - **Suggestions:** nice-to-haves\n"
    "   - **Approval:** yes/no\n"
    "\n"
    "## Constraints\n"
    "- READ-ONLY — do NOT edit any files\n"
    "- Focus review on changed lines, not the whole codebase\n"
    "- Be specific: reference file:line in every comment\n"
)

_SWARM_TESTER_TEMPLATE = (
    "## Role: Swarm Tester\n"
    "You write and run tests for the implementer's changes.\n"
    "\n"
    "## Goal\n"
    "{goal}\n"
    "\n"
    "## Protocol\n"
    "1. Wait for the implementer to signal ready\n"
    "2. Identify untested code paths from the implementer's changes\n"
    "3. Write tests in the appropriate tests/ directory\n"
    "4. Run the full test suite — fix any broken tests caused by the change\n"
    "5. Report pass/fail counts and coverage delta to the coordinator\n"
    "\n"
    "## Constraints\n"
    "- Modify ONLY files under tests/ — never production code\n"
    "- Follow the project's existing test framework and patterns\n"
    "- Aim for 80%+ coverage of the implementer's new code\n"
)


_SWARM_ROLE_TEMPLATES: dict[str, str] = {
    "coordinator": _SWARM_COORDINATOR_TEMPLATE,
    "researcher": _SWARM_RESEARCHER_TEMPLATE,
    "implementer": _SWARM_IMPLEMENTER_TEMPLATE,
    "reviewer": _SWARM_REVIEWER_TEMPLATE,
    "tester": _SWARM_TESTER_TEMPLATE,
}


def build_swarm_prompt(
    role: str,
    goal: str,
    *,
    swarm_id: str = "",
    worker_roster: str = "",
) -> str:
    """Build a role-specific prompt for a swarm worker.

    Args:
        role: Role value (coordinator, researcher, implementer, reviewer, tester).
        goal: High-level swarm goal.
        swarm_id: Swarm identifier, referenced in coordinator prompt.
        worker_roster: Bullet list of workers, used in coordinator prompt.

    Returns:
        Rendered prompt string. Raises KeyError if role is unknown.
    """
    template = _SWARM_ROLE_TEMPLATES[role]
    return template.format(
        goal=goal,
        swarm_id=swarm_id or "<swarm-id>",
        worker_roster=worker_roster or "(no workers listed)",
    )


def build_retry_context(
    retry_count: int,
    max_retries: int,
    error: str,
    summary: str | None = None,
) -> str:
    """Build structured retry context for agent prompts.

    Includes failure category, what was attempted, and actionable guidance.
    """
    category = classify_failure(error)
    parts = [
        f"## RETRY ATTEMPT {retry_count}/{max_retries}\n",
        f"**Previous failure:** {error}\n",
        f"**Root cause category:** {category}\n",
    ]
    if summary:
        parts.append(f"**What was attempted:** {summary}\n")
    parts.append(
        "\n**Guidance:**\n"
        "- Review the error carefully before repeating the same approach\n"
        "- If the same approach failed, try an alternative\n"
        "- If blocked by a dependency issue, document it and commit partial work\n"
        "- Check .claude/CLAUDE.md for project-specific test/build commands\n"
    )
    return "".join(parts)
