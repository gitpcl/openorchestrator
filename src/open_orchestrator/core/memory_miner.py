"""Fact mining — extract structured facts from git history, progress files, and code.

FactMiner scans a worktree (or the whole repo) and produces ``MinedFact``
records with source attribution. It never writes to the store directly —
callers pass results to ``MemoryStore.add_fact`` after review.

Mining is opt-in via the ``owt memory mine`` CLI. It never runs automatically
on every commit.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from open_orchestrator.models.memory import MemoryType

logger = logging.getLogger(__name__)


# Matches conventional commit types we care about: feat, fix, refactor, perf
_CONVENTIONAL_COMMIT = re.compile(
    r"^(?P<type>feat|fix|refactor|perf|docs)(?:\([^)]+\))?(?:!)?:\s*(?P<desc>.+)$",
    re.IGNORECASE,
)

# Matches decision/note comments in source files:
#   # TODO: ...  # NOTE: ...  # DECISION: ...  # XXX: ...
_COMMENT_MARKER = re.compile(
    r"(?:#|//|/\*|\*)\s*(?P<tag>TODO|NOTE|DECISION|XXX|FIXME|HACK)\s*:\s*(?P<body>.+?)(?:\*/)?$",
    re.IGNORECASE,
)

# Matches progress-log "decided X because Y" patterns (very loose)
_PROGRESS_DECISION = re.compile(
    r"\b(?:decided|chose|picked|went with)\s+(?P<body>[^\n]+)",
    re.IGNORECASE,
)

# Source file extensions we will scan for comments
_SOURCE_EXTENSIONS = {
    ".py",
    ".pyx",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".php",
    ".rb",
    ".swift",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
}


@dataclass(frozen=True)
class MinedFact:
    """A single mined fact with source attribution."""

    content: str
    kind: MemoryType
    category: str
    source: str  # file:line, commit sha, or logical origin
    worktree: str = "global"


def _commit_kind(commit_type: str) -> MemoryType:
    """Classify a conventional-commit type as a memory kind."""
    t = commit_type.lower()
    if t == "feat":
        return MemoryType.DECISION
    if t == "fix":
        return MemoryType.DECISION
    if t == "refactor":
        return MemoryType.ARCHITECTURE
    if t == "perf":
        return MemoryType.DECISION
    if t == "docs":
        return MemoryType.REFERENCE
    return MemoryType.DECISION


def _comment_kind(tag: str) -> MemoryType:
    """Classify a source comment tag as a memory kind."""
    t = tag.upper()
    if t == "DECISION":
        return MemoryType.DECISION
    if t == "NOTE":
        return MemoryType.REFERENCE
    return MemoryType.CONVENTION  # TODO/XXX/FIXME/HACK are convention-ish reminders


class FactMiner:
    """Mines structured facts from a worktree's git history, progress files, and comments."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path.cwd()).resolve()

    # ------------------------------------------------------------------
    # Git history mining
    # ------------------------------------------------------------------

    def mine_git_log(
        self,
        worktree: str = "global",
        since: str | datetime | None = None,
        limit: int = 100,
    ) -> list[MinedFact]:
        """Extract decisions from conventional-commit messages.

        Parses commit subjects matching ``feat|fix|refactor|perf|docs:``.
        Each match becomes a fact attributed to its commit sha.
        """
        cmd = [
            "git",
            "-C",
            str(self.root),
            "log",
            f"-n{limit}",
            "--pretty=format:%H%x00%s",
        ]
        if since is not None:
            since_str = since.isoformat() if isinstance(since, datetime) else str(since)
            cmd.append(f"--since={since_str}")

        try:
            result = subprocess.run(  # noqa: S603 - trusted git args
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("git log failed: %s", exc)
            return []

        if result.returncode != 0:
            logger.debug("git log returned %d: %s", result.returncode, result.stderr)
            return []

        facts: list[MinedFact] = []
        for line in result.stdout.splitlines():
            sha, _, subject = line.partition("\x00")
            if not sha or not subject:
                continue
            match = _CONVENTIONAL_COMMIT.match(subject.strip())
            if match is None:
                continue
            commit_type = match.group("type").lower()
            desc = match.group("desc").strip()
            facts.append(
                MinedFact(
                    content=f"{commit_type}: {desc}",
                    kind=_commit_kind(commit_type),
                    category=commit_type,
                    source=f"commit:{sha[:12]}",
                    worktree=worktree,
                )
            )
        return facts

    # ------------------------------------------------------------------
    # Progress-file mining
    # ------------------------------------------------------------------

    def mine_progress_files(self, worktree: str = "global") -> list[MinedFact]:
        """Extract decisions and learnings from progress log + CLAUDE.md files."""
        candidates = [
            self.root / ".harness" / "progress_log.md",
            self.root / "CLAUDE.md",
            self.root / ".claude" / "CLAUDE.md",
            self.root / "claude-progress.txt",
        ]
        facts: list[MinedFact] = []
        for path in candidates:
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                logger.debug("Failed to read %s: %s", path, exc)
                continue

            rel_path = self._rel(path)
            for line_no, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                # Bullet-style decisions: "- [cat] ..." or "- decided X"
                if stripped.startswith("- [") and "]" in stripped:
                    close = stripped.index("]")
                    category = stripped[3:close].strip() or "progress"
                    body = stripped[close + 1 :].strip()
                    if body:
                        facts.append(
                            MinedFact(
                                content=body,
                                kind=MemoryType.DECISION,
                                category=category,
                                source=f"{rel_path}:{line_no}",
                                worktree=worktree,
                            )
                        )
                        continue
                match = _PROGRESS_DECISION.search(stripped)
                if match is not None:
                    facts.append(
                        MinedFact(
                            content=match.group(0).strip(),
                            kind=MemoryType.DECISION,
                            category="progress",
                            source=f"{rel_path}:{line_no}",
                            worktree=worktree,
                        )
                    )
        return facts

    # ------------------------------------------------------------------
    # Code-comment mining
    # ------------------------------------------------------------------

    def mine_code_comments(
        self,
        worktree: str = "global",
        tags: tuple[str, ...] = ("TODO", "NOTE", "DECISION", "FIXME", "HACK", "XXX"),
    ) -> list[MinedFact]:
        """Scan source files for TODO/NOTE/DECISION-style comments."""
        facts: list[MinedFact] = []
        upper_tags = {t.upper() for t in tags}

        try:
            source_files = [
                p for p in self.root.rglob("*") if p.is_file() and p.suffix in _SOURCE_EXTENSIONS and not self._is_ignored(p)
            ]
        except OSError as exc:
            logger.warning("Failed to scan %s: %s", self.root, exc)
            return []

        for path in source_files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel_path = self._rel(path)
            for line_no, line in enumerate(text.splitlines(), start=1):
                match = _COMMENT_MARKER.search(line)
                if match is None:
                    continue
                tag = match.group("tag").upper()
                if tag not in upper_tags:
                    continue
                body = match.group("body").strip().rstrip("*/").strip()
                if not body:
                    continue
                facts.append(
                    MinedFact(
                        content=f"{tag}: {body}",
                        kind=_comment_kind(tag),
                        category=tag.lower(),
                        source=f"{rel_path}:{line_no}",
                        worktree=worktree,
                    )
                )
        return facts

    # ------------------------------------------------------------------
    # Combined mining
    # ------------------------------------------------------------------

    def mine_all(
        self,
        worktree: str = "global",
        since: str | datetime | None = None,
        limit: int = 100,
        include_comments: bool = True,
    ) -> list[MinedFact]:
        """Run all mining strategies and return combined results."""
        facts = list(self.mine_git_log(worktree=worktree, since=since, limit=limit))
        facts.extend(self.mine_progress_files(worktree=worktree))
        if include_comments:
            facts.extend(self.mine_code_comments(worktree=worktree))
        return facts

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    @staticmethod
    def _is_ignored(path: Path) -> bool:
        """Skip paths commonly excluded from scans."""
        parts = set(path.parts)
        ignored = {
            ".git",
            "node_modules",
            ".venv",
            "venv",
            "__pycache__",
            "dist",
            "build",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
        }
        return bool(parts & ignored)
