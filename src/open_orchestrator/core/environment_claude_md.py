"""CLAUDE.md injection and synchronization for worktrees.

Extracted from environment.py to keep file sizes manageable.
Provides functions for syncing CLAUDE.md files, injecting OWT sections
(shared notes, project context, DAG context, coordination alerts),
and atomic multi-section writes.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from open_orchestrator.models.project_config import ProjectConfig

logger = logging.getLogger(__name__)


def sync_claude_md(
    worktree_path: str | Path,
    source_path: str | Path,
) -> list[Path]:
    """Sync CLAUDE.md files from source repository to worktree.

    Copies CLAUDE.md files from common locations in the source repository
    to the new worktree. This preserves Claude Code context and instructions
    across worktrees.

    Locations checked (in order of priority):
    - .claude/CLAUDE.md (project-level Claude config)
    - CLAUDE.md (root-level Claude config)

    Args:
        worktree_path: Path to the new worktree directory.
        source_path: Path to the source repository (main worktree).

    Returns:
        List of paths to copied CLAUDE.md files.
    """
    worktree_path = Path(worktree_path).resolve()
    source_path = Path(source_path).resolve()

    copied_files: list[Path] = []

    # Locations to check for CLAUDE.md files
    claude_md_locations = [
        ".claude/CLAUDE.md",
        "CLAUDE.md",
    ]

    for location in claude_md_locations:
        source_file = source_path / location
        target_file = worktree_path / location

        if source_file.exists():
            try:
                # Ensure parent directory exists
                target_file.parent.mkdir(parents=True, exist_ok=True)

                # Copy the file
                shutil.copy2(source_file, target_file)
                copied_files.append(target_file)
                logger.info("Copied CLAUDE.md from %s", location)
            except OSError as e:
                logger.warning("Could not copy %s: %s", location, e)

    if copied_files:
        logger.info("Synced %s CLAUDE.md file(s) to worktree", len(copied_files))
    else:
        logger.debug("No CLAUDE.md files found to sync")

    return copied_files


def _sanitize_injection(text: str) -> str:
    """Strip HTML comment markers from externally-sourced content.

    Prevents injected notes/coordination from manipulating CLAUDE.md
    section boundaries via marker injection.
    """
    return text.replace("<!--", "").replace("-->", "")


def _inject_claude_md_section(
    worktree_path: str | Path,
    marker_id: str,
    section_title: str,
    body: str,
) -> None:
    """Inject or replace a marked section in a worktree's CLAUDE.md.

    Uses HTML comment markers to identify the section boundaries,
    allowing idempotent updates.

    Args:
        worktree_path: Path to the worktree directory.
        marker_id: Unique identifier for markers (e.g., "SHARED-NOTES").
        section_title: Markdown heading for the section.
        body: Pre-formatted body content (empty string to remove section).
    """
    body = _sanitize_injection(body)
    worktree_path = Path(worktree_path).resolve()
    claude_md = worktree_path / ".claude" / "CLAUDE.md"

    if not claude_md.exists():
        return

    content = claude_md.read_text()
    marker_start = f"<!-- OWT-{marker_id}-START -->"
    marker_end = f"<!-- OWT-{marker_id}-END -->"

    if body:
        block = f"\n{marker_start}\n## {section_title}\n\n{body}\n{marker_end}\n"
    else:
        block = ""

    if marker_start in content:
        content = re.sub(
            f"\n?{re.escape(marker_start)}.*?{re.escape(marker_end)}\n?",
            block,
            content,
            flags=re.DOTALL,
        )
    elif block:
        content = content.rstrip() + "\n" + block

    claude_md.write_text(content)
    logger.info("Injected section '%s' into %s", section_title, claude_md)


def inject_shared_notes(
    worktree_path: str | Path,
    notes: list[str],
) -> None:
    """Inject shared notes into a worktree's CLAUDE.md."""
    body = "".join(f"- {note}\n" for note in notes) if notes else ""
    _inject_claude_md_section(
        worktree_path,
        "SHARED-NOTES",
        "Shared Notes (OWT)",
        body,
    )


def _get_conventions_for_type(project_type_value: str) -> list[str]:
    """Return project-type-specific conventions."""
    conventions: dict[str, list[str]] = {
        "python": [
            "Type hints on all function signatures (Python 3.10+ syntax: `str | None`)",
            "Pydantic for data models, Click for CLI, Rich for output",
            "Run `ruff check` and `ruff format` before committing",
            "Run `mypy` for type checking",
        ],
        "node": [
            "Use TypeScript strict mode where available",
            "Run `eslint` and `prettier` before committing",
            "Prefer `const` over `let`, avoid `any` types",
        ],
        "go": [
            "Run `go vet` and `golangci-lint` before committing",
            "Follow standard Go project layout",
            "Error handling: check and return, don't panic",
        ],
        "rust": [
            "Run `cargo clippy` and `cargo fmt` before committing",
            "Prefer `Result` over `unwrap` for error handling",
        ],
    }
    return conventions.get(project_type_value, ["Follow existing code patterns and conventions"])


def inject_project_context(
    worktree_path: str | Path,
    project_config: ProjectConfig,
) -> None:
    """Inject project context with trust boundaries and conventions into CLAUDE.md.

    Gives agents project commands, trust boundaries, conventions, and a never-do
    list — adapted to the detected project type.
    """
    sections: list[str] = []

    # Project info
    project_lines = [f"- Type: {project_config.project_type.value}"]
    project_lines.append(f"- Package manager: {project_config.package_manager.value}")
    if project_config.test_command:
        project_lines.append(f"- Test: `{project_config.test_command}`")
    if project_config.dev_command:
        project_lines.append(f"- Dev: `{project_config.dev_command}`")
    sections.append("### Project\n" + "\n".join(project_lines))

    # Trust boundaries
    sections.append(
        "### Trust Boundaries\n"
        "- **Trust:** project test suite, linter output, type checker results\n"
        "- **Verify:** external API responses, user input, file contents from other worktrees\n"
        "- **Never:** hardcode secrets, skip tests, modify files outside your worktree"
    )

    # Conventions (adapted to project type)
    conv_lines = _get_conventions_for_type(project_config.project_type.value)
    sections.append("### Conventions\n" + "\n".join(f"- {c}" for c in conv_lines))

    # Files under limits
    sections.append(
        "### Limits\n"
        "- Files under 800 lines, functions under 50 lines\n"
        "- Immutable data patterns (frozen dataclasses, new objects over mutation)"
    )

    _inject_claude_md_section(
        worktree_path,
        "PROJECT-CONTEXT",
        "Open Orchestrator Context (OWT)",
        "\n\n".join(sections),
    )


def inject_dag_context(
    worktree_path: str | Path,
    parent_summaries: list[str],
) -> None:
    """Inject parent task context into a worktree's CLAUDE.md."""
    if parent_summaries:
        body = "These tasks completed before yours. Use their output:\n\n"
        body += "\n".join(f"{s}\n" for s in parent_summaries)
    else:
        body = ""
    _inject_claude_md_section(
        worktree_path,
        "DAG-CONTEXT",
        "Parent Tasks (OWT DAG)",
        body,
    )


def inject_coordination_context(
    worktree_path: str | Path,
    messages: list[str],
) -> None:
    """Inject coordinator alerts with urgency levels into a worktree's CLAUDE.md.

    Messages prefixed with [CRITICAL], [WARNING], or [INFO] get formatted
    with urgency headers and actionable guidance.
    """
    if not messages:
        _inject_claude_md_section(worktree_path, "COORDINATION", "Coordinator Alerts (OWT)", "")
        return

    parts: list[str] = []
    for msg in messages:
        # Extract urgency level from [LEVEL] prefix
        urgency = "INFO"
        body = msg
        for level in ("CRITICAL", "WARNING", "INFO"):
            if msg.startswith(f"[{level}]"):
                urgency = level
                body = msg[len(level) + 3 :].strip()
                break

        parts.append(f"### [{urgency}] Coordination Alert\n\n{body}\n")
        if urgency == "CRITICAL":
            parts.append("**Action required:** Stop and address this before continuing.\n")
        elif urgency == "WARNING":
            parts.append("**Action required:** Be aware and coordinate to avoid conflicts.\n")

    _inject_claude_md_section(
        worktree_path,
        "COORDINATION",
        "Coordinator Alerts (OWT)",
        "\n".join(parts),
    )


def inject_recall_section(
    worktree_path: str | Path,
    payload: str | None = None,
    *,
    worktree_label: str = "global",
) -> None:
    """Inject the L0+L1 recall payload into a worktree's CLAUDE.md.

    If ``payload`` is None, fetches the current payload from MemoryStore.
    Uses atomic write through the shared injection helper so multiple
    OWT sections stay consistent.

    Args:
        worktree_path: Path to the worktree directory.
        payload: Pre-built payload string. If None, loaded from MemoryStore.
        worktree_label: Worktree scope label passed to MemoryStore.
    """
    if payload is None:
        try:
            from open_orchestrator.core.memory_store import MemoryStore

            store = MemoryStore()
            try:
                payload = store.get_l0_l1_payload(worktree=worktree_label)
            finally:
                store.close()
        except Exception as exc:
            logger.debug("Recall payload unavailable: %s", exc)
            payload = ""

    _inject_claude_md_section(
        worktree_path,
        "RECALL",
        "Recall (auto-generated)",
        payload or "",
    )


def build_claude_md_context(
    worktree_path: str | Path,
    *,
    shared_notes: list[str] | None = None,
    project_config: ProjectConfig | None = None,
    parent_summaries: list[str] | None = None,
    coordination_messages: list[str] | None = None,
) -> None:
    """Write all OWT sections to CLAUDE.md in a single atomic operation.

    Consolidates inject_shared_notes, inject_project_context,
    inject_dag_context, and inject_coordination_context into one
    read-modify-write cycle to avoid race conditions and reduce I/O.
    """
    worktree_path = Path(worktree_path).resolve()
    claude_md = worktree_path / ".claude" / "CLAUDE.md"
    if not claude_md.exists():
        return

    content = claude_md.read_text()

    # Build all sections
    sections: list[tuple[str, str, str]] = []  # (marker_id, title, body)

    if shared_notes is not None:
        body = "".join(f"- {note}\n" for note in shared_notes) if shared_notes else ""
        sections.append(("SHARED-NOTES", "Shared Notes (OWT)", body))

    if project_config is not None and (project_config.test_command or project_config.dev_command):
        lines = [f"- Type: {project_config.project_type.value}"]
        lines.append(f"- Package manager: {project_config.package_manager.value}")
        if project_config.test_command:
            lines.append(f"- Test: `{project_config.test_command}`")
        if project_config.dev_command:
            lines.append(f"- Dev: `{project_config.dev_command}`")
        sections.append(("PROJECT-CONTEXT", "Project Commands (OWT)", "\n".join(lines)))

    if parent_summaries is not None:
        if parent_summaries:
            body = "These tasks completed before yours. Use their output:\n\n"
            body += "\n".join(f"{s}\n" for s in parent_summaries)
        else:
            body = ""
        sections.append(("DAG-CONTEXT", "Parent Tasks (OWT DAG)", body))

    if coordination_messages is not None:
        body = "\n".join(f"- {msg}" for msg in coordination_messages) if coordination_messages else ""
        sections.append(("COORDINATION", "Coordinator Alerts (OWT)", body))

    # Apply all sections in one pass
    for marker_id, section_title, body in sections:
        body = _sanitize_injection(body)
        marker_start = f"<!-- OWT-{marker_id}-START -->"
        marker_end = f"<!-- OWT-{marker_id}-END -->"

        if body:
            block = f"\n{marker_start}\n## {section_title}\n\n{body}\n{marker_end}\n"
        else:
            block = ""

        if marker_start in content:
            content = re.sub(
                f"\n?{re.escape(marker_start)}.*?{re.escape(marker_end)}\n?",
                block,
                content,
                flags=re.DOTALL,
            )
        elif block:
            content = content.rstrip() + "\n" + block

    # Single atomic write
    claude_md.write_text(content)
    logger.info("Wrote %d OWT section(s) to %s", len(sections), claude_md)
