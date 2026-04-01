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


# ─── Task-type protocol templates ──────────────────────────────────────────

_PROTOCOLS: dict[TaskType, str] = {
    TaskType.BUGFIX: (
        "## Protocol: Bug Fix\n"
        "1. **REPRODUCE** — Write a failing test that demonstrates the bug\n"
        "2. **DIAGNOSE** — Trace root cause (read error logs, stack traces)\n"
        "3. **FIX** — Make the minimal code change to fix the root cause\n"
        "4. **VERIFY** — Run the failing test — it should now pass\n"
        "5. **REGRESSION** — Run full test suite to ensure no regressions\n"
        "6. **COMMIT** — Commit with `fix:` prefix"
    ),
    TaskType.FEATURE: (
        "## Protocol: Feature Implementation\n"
        "1. **ORIENT** — Read CLAUDE.md, understand project structure\n"
        "2. **EXPLORE** — Find related code, understand patterns in use\n"
        "3. **IMPLEMENT** — Write the feature following existing patterns\n"
        "4. **TEST** — Write tests (aim for 80%+ coverage of new code)\n"
        "5. **VERIFY** — Run linter, type checker, full test suite\n"
        "6. **COMMIT** — Commit with `feat:` prefix"
    ),
    TaskType.REFACTOR: (
        "## Protocol: Refactoring\n"
        "1. **BASELINE** — Run tests to confirm current behavior works\n"
        "2. **PLAN** — Identify what to change and verify no behavior change\n"
        "3. **REFACTOR** — Make structural changes in small, safe steps\n"
        "4. **VERIFY** — Run tests after each step — no regressions\n"
        "5. **CLEANUP** — Remove dead code, update imports\n"
        "6. **COMMIT** — Commit with `refactor:` prefix"
    ),
    TaskType.TEST: (
        "## Protocol: Test Writing\n"
        "1. **SURVEY** — Find existing test patterns and fixtures\n"
        "2. **IDENTIFY** — List untested code paths and edge cases\n"
        "3. **WRITE** — Write tests following project conventions\n"
        "4. **RUN** — Execute tests, fix any failures\n"
        "5. **COVERAGE** — Check coverage, add tests for gaps\n"
        "6. **COMMIT** — Commit with `test:` prefix"
    ),
    TaskType.DOCS: (
        "## Protocol: Documentation\n"
        "1. **READ** — Understand the code/feature to document\n"
        "2. **DRAFT** — Write clear, concise documentation\n"
        "3. **EXAMPLES** — Add code examples where helpful\n"
        "4. **REVIEW** — Check for accuracy and completeness\n"
        "5. **COMMIT** — Commit with `docs:` prefix"
    ),
}


def get_protocol_for_task(description: str) -> str:
    """Get the task-type-specific protocol for a task description."""
    task_type = classify_task(description)
    return _PROTOCOLS[task_type]
