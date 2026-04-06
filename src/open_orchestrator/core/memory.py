"""Memory system for persistent cross-worktree knowledge.

Manages a 3-layer memory system:
1. MEMORY.md — always-loaded index with pointers to topic files
2. Topic files — on-demand knowledge storage in .owt/memory/
3. Transcript search — grep-based search across agent session logs

The .owt/memory/ directory persists across harness updates.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from open_orchestrator.models.memory import (
    MemoryEntry,
    MemoryType,
    SearchResult,
    TopicFile,
)

logger = logging.getLogger(__name__)

# Classification keywords for auto-detecting memory type
_TYPE_KEYWORDS: dict[MemoryType, list[str]] = {
    MemoryType.DECISION: [
        "decided",
        "chose",
        "picked",
        "went with",
        "decision",
        "trade-off",
        "tradeoff",
        "instead of",
        "rather than",
    ],
    MemoryType.ARCHITECTURE: [
        "architecture",
        "module",
        "layer",
        "pattern",
        "structure",
        "component",
        "service",
        "interface",
        "protocol",
        "schema",
    ],
    MemoryType.CONVENTION: [
        "convention",
        "rule",
        "always",
        "never",
        "must",
        "standard",
        "naming",
        "format",
        "style",
        "lint",
        "prefix",
        "suffix",
    ],
    MemoryType.REFERENCE: [
        "url",
        "link",
        "docs",
        "documentation",
        "api",
        "endpoint",
        "dashboard",
        "wiki",
        "readme",
        "jira",
        "linear",
        "slack",
    ],
}

MAX_INDEX_LINES = 200
INDEX_HEADER = "# Memory Index\n"
_FILENAME_RE = re.compile(r"\]\((.+?)\)")


class MemoryManager:
    """Manages the .owt/memory/ directory and MEMORY.md index."""

    def __init__(self, repo_root: Path | None = None) -> None:
        self._root = (repo_root or Path.cwd()).resolve()
        self._memory_dir = self._root / ".owt" / "memory"
        self._index_path = self._memory_dir / "MEMORY.md"

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    @property
    def index_path(self) -> Path:
        return self._index_path

    # ── Initialization ──────────────────────────────────────────────

    def ensure_dirs(self) -> None:
        """Create .owt/memory/ directory if it doesn't exist."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        if not self._index_path.exists():
            self._index_path.write_text(INDEX_HEADER)
            logger.info("Created %s", self._index_path)

    # ── Index CRUD ──────────────────────────────────────────────────

    def read_index(self) -> str:
        """Read the MEMORY.md index content."""
        if not self._index_path.exists():
            return INDEX_HEADER
        return self._index_path.read_text()

    def _parse_index_entries(self) -> list[str]:
        """Parse MEMORY.md into individual entry lines."""
        content = self.read_index()
        lines: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ["):
                lines.append(stripped)
        return lines

    def _write_index(self, entry_lines: list[str]) -> None:
        """Write entry lines back to MEMORY.md, enforcing 200-line limit."""
        self.ensure_dirs()
        truncated = False
        if len(entry_lines) > MAX_INDEX_LINES:
            entry_lines = entry_lines[:MAX_INDEX_LINES]
            truncated = True

        content = INDEX_HEADER + "\n".join(entry_lines) + "\n"

        if truncated:
            content += f"\n<!-- Truncated: index exceeds {MAX_INDEX_LINES} entries -->\n"

        self._index_path.write_text(content)

    def add_to_index(self, entry: MemoryEntry) -> None:
        """Add an entry to MEMORY.md. Replaces existing entry with same filename."""
        entries = self._parse_index_entries()

        # Remove existing entry for this filename
        link_pattern = f"]({entry.filename})"
        entries = [e for e in entries if link_pattern not in e]

        entries.append(entry.index_line)
        self._write_index(entries)
        logger.info("Added '%s' to memory index", entry.name)

    def remove_from_index(self, filename: str) -> bool:
        """Remove an entry from MEMORY.md by filename. Returns True if found."""
        entries = self._parse_index_entries()
        link_pattern = f"]({filename})"
        new_entries = [e for e in entries if link_pattern not in e]
        if len(new_entries) == len(entries):
            return False
        self._write_index(new_entries)
        logger.info("Removed '%s' from memory index", filename)
        return True

    def list_entries(self) -> list[MemoryEntry]:
        """Parse MEMORY.md and return structured entries."""
        entries: list[MemoryEntry] = []
        # Pattern: - [Name](filename.md) — description
        pattern = re.compile(r"^- \[(.+?)\]\((.+?)\)\s*—\s*(.+)$")
        for line in self._parse_index_entries():
            match = pattern.match(line)
            if match:
                name, filename, description = match.groups()
                # Determine type from topic file if available
                memory_type = MemoryType.REFERENCE
                topic = self.read_topic(filename)
                if topic:
                    memory_type = topic.memory_type
                entries.append(
                    MemoryEntry(
                        name=name.strip(),
                        description=description.strip(),
                        memory_type=memory_type,
                        filename=filename.strip(),
                    )
                )
        return entries

    # ── Topic Files ─────────────────────────────────────────────────

    def write_topic(self, topic: TopicFile) -> Path:
        """Write a topic file to .owt/memory/ and update the index."""
        self.ensure_dirs()
        path = self._memory_dir / topic.filename
        path.write_text(topic.to_frontmatter())
        self.add_to_index(topic.to_entry())
        logger.info("Wrote topic file: %s", path)
        return path

    def read_topic(self, filename: str) -> TopicFile | None:
        """Read and parse a topic file from .owt/memory/."""
        path = self._memory_dir / filename
        if not path.exists():
            return None
        return self._parse_topic_file(path)

    def delete_topic(self, filename: str) -> bool:
        """Delete a topic file and its index entry. Returns True if found."""
        path = self._memory_dir / filename
        removed_index = self.remove_from_index(filename)
        if path.exists():
            path.unlink()
            logger.info("Deleted topic file: %s", path)
            return True
        return removed_index

    def list_topics(self) -> list[TopicFile]:
        """List all topic files in .owt/memory/."""
        topics: list[TopicFile] = []
        if not self._memory_dir.exists():
            return topics
        for path in sorted(self._memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            topic = self._parse_topic_file(path)
            if topic:
                topics.append(topic)
        return topics

    @staticmethod
    def _parse_topic_file(path: Path) -> TopicFile | None:
        """Parse a markdown file with YAML frontmatter into a TopicFile."""
        content = path.read_text()
        if not content.startswith("---"):
            return None

        # Split frontmatter and body
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        frontmatter = parts[1].strip()
        body = parts[2].strip()

        # Parse simple YAML frontmatter (key: value)
        meta: dict[str, str] = {}
        for line in frontmatter.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()

        name = meta.get("name", path.stem)
        description = meta.get("description", "")
        type_str = meta.get("type", "reference")

        try:
            memory_type = MemoryType(type_str)
        except ValueError:
            memory_type = MemoryType.REFERENCE

        return TopicFile(
            name=name,
            description=description,
            memory_type=memory_type,
            body=body,
            filename=path.name,
        )

    # ── Auto-Classification ─────────────────────────────────────────

    @staticmethod
    def classify_fact(text: str) -> MemoryType:
        """Auto-classify a fact string into a MemoryType.

        Uses keyword matching to determine the most likely type.
        Falls back to REFERENCE if no strong signal.
        """
        text_lower = text.lower()
        scores: dict[MemoryType, int] = {t: 0 for t in MemoryType}

        for memory_type, keywords in _TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    scores[memory_type] += 1

        best = max(scores, key=lambda t: scores[t])
        if scores[best] == 0:
            return MemoryType.REFERENCE
        return best

    # ── Search ──────────────────────────────────────────────────────

    def search(self, query: str, *, include_transcripts: bool = True) -> list[SearchResult]:
        """Search across index, topic files, and optionally transcripts."""
        results: list[SearchResult] = []

        # Search MEMORY.md index
        results.extend(self._search_file(self._index_path, query, source="index"))

        # Search topic files
        if self._memory_dir.exists():
            for path in self._memory_dir.glob("*.md"):
                if path.name == "MEMORY.md":
                    continue
                results.extend(self._search_file(path, query, source="topic"))

        # Search transcripts via grep
        if include_transcripts:
            results.extend(self._search_transcripts(query))

        return results

    @staticmethod
    def _search_file(path: Path, query: str, *, source: str) -> list[SearchResult]:
        """Search a single file for a query pattern (case-insensitive)."""
        results: list[SearchResult] = []
        if not path.exists():
            return results

        try:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        except re.error:
            return results

        lines = path.read_text().splitlines()
        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                # Gather context (1 line before + after)
                start = max(0, i - 2)
                end = min(len(lines), i + 1)
                context_lines = lines[start:end]
                results.append(
                    SearchResult(
                        source=source,
                        filename=path.name,
                        line_number=i,
                        line=line.strip(),
                        context="\n".join(context_lines),
                    )
                )
        return results

    def _search_transcripts(self, query: str) -> list[SearchResult]:
        """Search agent session transcripts using grep subprocess.

        Searches common transcript locations:
        - .owt/transcripts/
        - .claude/projects/*/sessions/
        """
        results: list[SearchResult] = []
        search_dirs: list[Path] = []

        # OWT transcript directory
        owt_transcripts = self._root / ".owt" / "transcripts"
        if owt_transcripts.exists():
            search_dirs.append(owt_transcripts)

        # Claude sessions directory
        claude_sessions = self._root / ".claude" / "sessions"
        if claude_sessions.exists():
            search_dirs.append(claude_sessions)

        for search_dir in search_dirs:
            try:
                proc = subprocess.run(
                    [
                        "grep",
                        "-r",
                        "-i",
                        "-n",
                        "--include=*.md",
                        "--include=*.txt",
                        "--include=*.log",
                        query,
                        str(search_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                # Output format: filepath:line_num:content
                hits_per_file: dict[str, int] = {}
                for match_line in proc.stdout.strip().splitlines():
                    if ":" not in match_line:
                        continue
                    filepath, _, rest = match_line.partition(":")
                    if ":" not in rest:
                        continue
                    num_str, _, content = rest.partition(":")
                    # Limit to 5 hits per file
                    hits_per_file[filepath] = hits_per_file.get(filepath, 0) + 1
                    if hits_per_file[filepath] > 5:
                        continue
                    try:
                        line_num = int(num_str)
                    except ValueError:
                        line_num = 0
                    results.append(
                        SearchResult(
                            source="transcript",
                            filename=Path(filepath).name,
                            line_number=line_num,
                            line=content.strip(),
                        )
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                logger.debug("Transcript search failed for %s: %s", search_dir, exc)

        return results

    # ── Consolidation ───────────────────────────────────────────────

    def consolidate(self) -> dict[str, int]:
        """Deduplicate and prune the memory index and topic files.

        Returns a summary dict with counts of actions taken:
        - orphaned_removed: index entries with no matching topic file
        - unindexed_added: topic files not in the index
        - duplicates_removed: duplicate index entries
        """
        stats = {"orphaned_removed": 0, "unindexed_added": 0, "duplicates_removed": 0}

        if not self._memory_dir.exists():
            return stats

        entries = self._parse_index_entries()

        # 1. Deduplicate index entries (keep last occurrence)
        seen_files: dict[str, str] = {}
        deduped: list[str] = []
        for entry in entries:
            match = _FILENAME_RE.search(entry)
            if match:
                filename = match.group(1)
                if filename in seen_files:
                    stats["duplicates_removed"] += 1
                    deduped = [e for e in deduped if f"]({filename})" not in e]
                seen_files[filename] = entry
            deduped.append(entry)

        # 2. Remove orphaned index entries (no matching topic file)
        clean: list[str] = []
        for entry in deduped:
            match = _FILENAME_RE.search(entry)
            if match:
                filename = match.group(1)
                if (self._memory_dir / filename).exists():
                    clean.append(entry)
                else:
                    stats["orphaned_removed"] += 1
                    logger.info("Removed orphaned index entry: %s", filename)
            else:
                clean.append(entry)

        # 3. Add unindexed topic files
        indexed_files = {m.group(1) for entry in clean if (m := _FILENAME_RE.search(entry))}

        for path in sorted(self._memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            if path.name not in indexed_files:
                topic = self._parse_topic_file(path)
                if topic:
                    clean.append(topic.to_entry().index_line)
                    stats["unindexed_added"] += 1
                    logger.info("Added unindexed topic to index: %s", path.name)

        self._write_index(clean)
        return stats

    # ── Utility ─────────────────────────────────────────────────────

    def slugify(self, text: str) -> str:
        """Convert text to a filename-safe slug."""
        slug = text.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug[:60].strip("-") + ".md"
