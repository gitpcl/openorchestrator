"""Tests for the memory system: models, MemoryManager, and CLI commands."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.memory import MAX_INDEX_LINES, MemoryManager
from open_orchestrator.models.memory import (
    MemoryEntry,
    MemoryType,
    SearchResult,
    TopicFile,
)

# ── Model Tests ─────────────────────────────────────────────────────


class TestMemoryEntry:
    def test_index_line_format(self) -> None:
        entry = MemoryEntry(
            name="Auth flow",
            description="JWT-based authentication decision",
            memory_type=MemoryType.DECISION,
            filename="auth-flow.md",
        )
        line = entry.index_line
        assert line == "- [Auth flow](auth-flow.md) — JWT-based authentication decision"

    def test_index_line_truncation(self) -> None:
        entry = MemoryEntry(
            name="Very long name",
            description="x" * 200,
            memory_type=MemoryType.REFERENCE,
            filename="long.md",
        )
        line = entry.index_line
        assert len(line) <= 150
        assert line.endswith("...")


class TestTopicFile:
    def test_to_frontmatter(self) -> None:
        topic = TopicFile(
            name="Auth flow",
            description="JWT auth decision",
            memory_type=MemoryType.DECISION,
            body="We chose JWT over session cookies.",
            filename="auth-flow.md",
        )
        output = topic.to_frontmatter()
        assert "---" in output
        assert "name: Auth flow" in output
        assert "type: decision" in output
        assert "We chose JWT over session cookies." in output

    def test_to_entry(self) -> None:
        topic = TopicFile(
            name="DB schema",
            description="PostgreSQL schema decisions",
            memory_type=MemoryType.ARCHITECTURE,
            body="Using JSONB for metadata.",
            filename="db-schema.md",
        )
        entry = topic.to_entry()
        assert entry.name == "DB schema"
        assert entry.memory_type == MemoryType.ARCHITECTURE
        assert entry.filename == "db-schema.md"


class TestSearchResult:
    def test_fields(self) -> None:
        result = SearchResult(
            source="topic",
            filename="auth.md",
            line_number=5,
            line="JWT is the standard",
            context="line4\nJWT is the standard\nline6",
        )
        assert result.source == "topic"
        assert result.line_number == 5


# ── MemoryManager Tests ─────────────────────────────────────────────


class TestMemoryManagerInit:
    def test_ensure_dirs_creates_structure(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        assert mgr.memory_dir.exists()
        assert mgr.index_path.exists()
        assert mgr.index_path.read_text().startswith("# Memory Index")

    def test_ensure_dirs_idempotent(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        mgr.ensure_dirs()  # Should not error
        assert mgr.index_path.exists()


class TestIndexCRUD:
    def test_add_to_index(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        entry = MemoryEntry(
            name="Test",
            description="A test entry",
            memory_type=MemoryType.REFERENCE,
            filename="test.md",
        )
        mgr.add_to_index(entry)
        content = mgr.read_index()
        assert "- [Test](test.md)" in content
        assert "A test entry" in content

    def test_add_replaces_existing(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        entry1 = MemoryEntry(
            name="Old name",
            description="Old desc",
            memory_type=MemoryType.REFERENCE,
            filename="same.md",
        )
        entry2 = MemoryEntry(
            name="New name",
            description="New desc",
            memory_type=MemoryType.DECISION,
            filename="same.md",
        )
        mgr.add_to_index(entry1)
        mgr.add_to_index(entry2)
        content = mgr.read_index()
        assert "Old name" not in content
        assert "New name" in content

    def test_remove_from_index(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        entry = MemoryEntry(
            name="To remove",
            description="Will be removed",
            memory_type=MemoryType.CONVENTION,
            filename="remove-me.md",
        )
        mgr.add_to_index(entry)
        assert mgr.remove_from_index("remove-me.md") is True
        assert "remove-me.md" not in mgr.read_index()

    def test_remove_nonexistent_returns_false(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        assert mgr.remove_from_index("nope.md") is False

    def test_list_entries(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        # Write a topic file so type can be read
        topic = TopicFile(
            name="Auth",
            description="Auth decisions",
            memory_type=MemoryType.DECISION,
            body="JWT",
            filename="auth.md",
        )
        mgr.write_topic(topic)
        entries = mgr.list_entries()
        assert len(entries) == 1
        assert entries[0].name == "Auth"
        assert entries[0].memory_type == MemoryType.DECISION

    def test_index_200_line_limit(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        for i in range(210):
            entry = MemoryEntry(
                name=f"Entry {i}",
                description=f"Desc {i}",
                memory_type=MemoryType.REFERENCE,
                filename=f"entry-{i}.md",
            )
            mgr.add_to_index(entry)
        content = mgr.read_index()
        entry_lines = [line for line in content.splitlines() if line.strip().startswith("- [")]
        assert len(entry_lines) <= MAX_INDEX_LINES
        assert "Truncated" in content

    def test_read_index_missing_file(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        content = mgr.read_index()
        assert "# Memory Index" in content


# ── Topic File Tests ─────────────────────────────────────────────────


class TestTopicFiles:
    def test_write_and_read_topic(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        topic = TopicFile(
            name="API versioning",
            description="URL-based versioning decision",
            memory_type=MemoryType.DECISION,
            body="We chose URL-based versioning (/api/v2/) over headers.",
            filename="api-versioning.md",
        )
        path = mgr.write_topic(topic)
        assert path.exists()

        loaded = mgr.read_topic("api-versioning.md")
        assert loaded is not None
        assert loaded.name == "API versioning"
        assert loaded.memory_type == MemoryType.DECISION
        assert "URL-based versioning" in loaded.body

    def test_read_nonexistent_topic(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        assert mgr.read_topic("nope.md") is None

    def test_delete_topic(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        topic = TopicFile(
            name="To delete",
            description="Will be deleted",
            memory_type=MemoryType.REFERENCE,
            body="Temp.",
            filename="delete-me.md",
        )
        mgr.write_topic(topic)
        assert mgr.delete_topic("delete-me.md") is True
        assert mgr.read_topic("delete-me.md") is None
        assert "delete-me.md" not in mgr.read_index()

    def test_delete_nonexistent_topic(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        assert mgr.delete_topic("nope.md") is False

    def test_list_topics(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        for i in range(3):
            topic = TopicFile(
                name=f"Topic {i}",
                description=f"Desc {i}",
                memory_type=MemoryType.CONVENTION,
                body=f"Body {i}",
                filename=f"topic-{i}.md",
            )
            mgr.write_topic(topic)
        topics = mgr.list_topics()
        assert len(topics) == 3

    def test_parse_topic_file_no_frontmatter(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        bad_file = mgr.memory_dir / "bad.md"
        bad_file.write_text("No frontmatter here\nJust plain markdown.")
        assert mgr._parse_topic_file(bad_file) is None

    def test_parse_topic_file_invalid_type(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        f = mgr.memory_dir / "invalid-type.md"
        f.write_text("---\nname: Test\ndescription: Test\ntype: unknown_type\n---\n\nBody")
        topic = mgr._parse_topic_file(f)
        assert topic is not None
        assert topic.memory_type == MemoryType.REFERENCE  # fallback


# ── Classification Tests ─────────────────────────────────────────────


class TestClassification:
    def test_classify_decision(self) -> None:
        assert MemoryManager.classify_fact("We decided to use PostgreSQL instead of MySQL") == MemoryType.DECISION

    def test_classify_architecture(self) -> None:
        assert MemoryManager.classify_fact("The service layer handles all business logic") == MemoryType.ARCHITECTURE

    def test_classify_convention(self) -> None:
        assert MemoryManager.classify_fact("Always use snake_case for naming Python functions") == MemoryType.CONVENTION

    def test_classify_reference(self) -> None:
        assert MemoryManager.classify_fact("API documentation is at https://docs.example.com") == MemoryType.REFERENCE

    def test_classify_ambiguous_defaults_to_reference(self) -> None:
        assert MemoryManager.classify_fact("The sky is blue") == MemoryType.REFERENCE


# ── Search Tests ─────────────────────────────────────────────────────


class TestSearch:
    def test_search_index(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        entry = MemoryEntry(
            name="Pydantic",
            description="We use Pydantic for all models",
            memory_type=MemoryType.CONVENTION,
            filename="pydantic.md",
        )
        mgr.add_to_index(entry)
        results = mgr.search("Pydantic", include_transcripts=False)
        assert len(results) >= 1
        assert any(r.source == "index" for r in results)

    def test_search_topic_files(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        topic = TopicFile(
            name="Auth",
            description="Auth decisions",
            memory_type=MemoryType.DECISION,
            body="We chose JWT over session cookies for stateless auth.",
            filename="auth.md",
        )
        mgr.write_topic(topic)
        results = mgr.search("JWT", include_transcripts=False)
        assert any(r.source == "topic" and "JWT" in r.line for r in results)

    def test_search_case_insensitive(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        topic = TopicFile(
            name="Config",
            description="Config patterns",
            memory_type=MemoryType.ARCHITECTURE,
            body="Using TOML for configuration files.",
            filename="config.md",
        )
        mgr.write_topic(topic)
        results = mgr.search("toml", include_transcripts=False)
        assert len(results) >= 1

    def test_search_no_results(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        results = mgr.search("nonexistent_term_xyz", include_transcripts=False)
        assert results == []

    def test_search_transcripts_directory(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        # Create a fake transcript
        transcript_dir = tmp_path / ".owt" / "transcripts"
        transcript_dir.mkdir(parents=True)
        (transcript_dir / "session-1.md").write_text("Fixed the login bug\nUpdated auth middleware\n")
        results = mgr.search("login", include_transcripts=True)
        assert any(r.source == "transcript" for r in results)

    def test_search_no_transcript_dirs(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        # No transcript dirs exist — should not error
        results = mgr.search("anything", include_transcripts=True)
        # Just verify it doesn't crash
        assert isinstance(results, list)


# ── Consolidation Tests ──────────────────────────────────────────────


class TestConsolidation:
    def test_consolidate_removes_orphaned(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        # Add an index entry with no matching file
        entry = MemoryEntry(
            name="Ghost",
            description="No file",
            memory_type=MemoryType.REFERENCE,
            filename="ghost.md",
        )
        mgr.add_to_index(entry)
        stats = mgr.consolidate()
        assert stats["orphaned_removed"] == 1
        assert "ghost.md" not in mgr.read_index()

    def test_consolidate_adds_unindexed(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        # Write a topic file but don't add to index
        topic_path = mgr.memory_dir / "stray.md"
        topic = TopicFile(
            name="Stray",
            description="Unindexed file",
            memory_type=MemoryType.CONVENTION,
            body="Found.",
            filename="stray.md",
        )
        topic_path.write_text(topic.to_frontmatter())
        stats = mgr.consolidate()
        assert stats["unindexed_added"] == 1
        assert "stray.md" in mgr.read_index()

    def test_consolidate_removes_duplicates(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        mgr.ensure_dirs()
        # Write a topic file
        topic = TopicFile(
            name="Dup",
            description="Duplicate entry",
            memory_type=MemoryType.REFERENCE,
            body="Content.",
            filename="dup.md",
        )
        mgr.write_topic(topic)
        # Manually inject a duplicate index line
        content = mgr.read_index()
        content += "- [Dup](dup.md) — Duplicate entry\n"
        mgr.index_path.write_text(content)
        stats = mgr.consolidate()
        assert stats["duplicates_removed"] == 1

    def test_consolidate_clean_memory(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        topic = TopicFile(
            name="Clean",
            description="All good",
            memory_type=MemoryType.DECISION,
            body="OK.",
            filename="clean.md",
        )
        mgr.write_topic(topic)
        stats = mgr.consolidate()
        assert sum(stats.values()) == 0

    def test_consolidate_empty_dir(self, tmp_path: Path) -> None:
        mgr = MemoryManager(tmp_path)
        stats = mgr.consolidate()
        assert sum(stats.values()) == 0


# ── Slugify Tests ────────────────────────────────────────────────────


class TestSlugify:
    def test_basic_slugify(self) -> None:
        mgr = MemoryManager()
        assert mgr.slugify("Hello World") == "hello-world.md"

    def test_special_characters(self) -> None:
        mgr = MemoryManager()
        assert mgr.slugify("API v2 (new!)") == "api-v2-new.md"

    def test_long_name_truncated(self) -> None:
        mgr = MemoryManager()
        slug = mgr.slugify("a" * 100)
        assert len(slug) <= 63  # 60 + ".md"

    def test_leading_trailing_dashes(self) -> None:
        mgr = MemoryManager()
        slug = mgr.slugify("--test--")
        assert not slug.startswith("-")


# ── CLI Command Tests ────────────────────────────────────────────────


class TestMemoryAddCommand:
    def test_add_basic(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(main, ["memory", "add", "We use Pydantic v2"])
        assert result.exit_code == 0
        assert "Stored" in result.output

    def test_add_with_name_and_type(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(
            main,
            [
                "memory",
                "add",
                "--name",
                "DB choice",
                "--type",
                "decision",
                "Chose PostgreSQL over MySQL",
            ],
        )
        assert result.exit_code == 0
        assert "decision" in result.output
        assert "DB choice" in result.output

    def test_add_creates_topic_file(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(main, ["memory", "add", "--name", "test-topic", "Some fact"])
        memory_dir = tmp_path / ".owt" / "memory"
        assert memory_dir.exists()
        topic_files = list(memory_dir.glob("*.md"))
        # MEMORY.md + the topic file
        assert len(topic_files) >= 2


class TestMemorySearchCommand:
    def test_search_no_results(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # Ensure dirs exist
        (tmp_path / ".owt" / "memory").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".owt" / "memory" / "MEMORY.md").write_text("# Memory Index\n")
        result = cli_runner.invoke(main, ["memory", "search", "nonexistent"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_search_finds_match(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # Add a memory entry first
        cli_runner.invoke(main, ["memory", "add", "--name", "JWT auth", "We use JWT for authentication"])
        result = cli_runner.invoke(main, ["memory", "search", "--no-transcripts", "JWT"])
        assert result.exit_code == 0
        assert "JWT" in result.output


class TestMemoryConsolidateCommand:
    def test_consolidate_clean(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(main, ["memory", "consolidate"])
        assert result.exit_code == 0
        assert "clean" in result.output.lower() or "nothing" in result.output.lower()

    def test_consolidate_with_orphans(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # Create index with orphan
        memory_dir = tmp_path / ".owt" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        index = memory_dir / "MEMORY.md"
        index.write_text("# Memory Index\n- [Ghost](ghost.md) — No file exists\n")
        result = cli_runner.invoke(main, ["memory", "consolidate"])
        assert result.exit_code == 0
        assert "orphaned" in result.output.lower() or "Removed" in result.output


class TestMemoryListCommand:
    def test_list_empty(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = cli_runner.invoke(main, ["memory", "list"])
        assert result.exit_code == 0
        assert "No memory" in result.output or "0" in result.output

    def test_list_with_entries(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        cli_runner.invoke(main, ["memory", "add", "--name", "Entry 1", "Fact one"])
        cli_runner.invoke(main, ["memory", "add", "--name", "Entry 2", "Fact two"])
        result = cli_runner.invoke(main, ["memory", "list"])
        assert result.exit_code == 0
        assert "Entry 1" in result.output
        assert "Entry 2" in result.output
