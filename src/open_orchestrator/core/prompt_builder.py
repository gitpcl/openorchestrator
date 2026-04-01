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
