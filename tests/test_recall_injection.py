"""Tests for CLAUDE.md recall auto-injection (Sprint 021 feature 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_orchestrator.core.environment_claude_md import inject_recall_section
from open_orchestrator.core.memory_store import MemoryStore, MemoryStoreConfig
from open_orchestrator.models.memory import MemoryLayer, MemoryType


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """Create a worktree layout with an existing CLAUDE.md."""
    wt = tmp_path / "feature"
    (wt / ".claude").mkdir(parents=True)
    (wt / ".claude" / "CLAUDE.md").write_text("# Project CLAUDE.md\n\nExisting content.\n")
    return wt


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    db = tmp_path / "recall.db"
    monkeypatch.setenv("OWT_RECALL_DB_PATH", str(db))
    s = MemoryStore(MemoryStoreConfig(storage_path=db))
    yield s
    s.close()


class TestInjectRecallSection:
    def test_injects_payload_from_store(self, worktree: Path, store: MemoryStore) -> None:
        store.add_fact(
            "project is owt",
            MemoryType.REFERENCE,
            "id",
            layer=MemoryLayer.L0_IDENTITY,
        )
        store.add_fact(
            "uses pytest",
            MemoryType.CONVENTION,
            "crit",
            layer=MemoryLayer.L1_CRITICAL,
            aaak="TST:pytest",
        )

        inject_recall_section(worktree)

        claude_md = (worktree / ".claude" / "CLAUDE.md").read_text()
        assert "OWT-RECALL-START" in claude_md
        assert "OWT-RECALL-END" in claude_md
        assert "Recall (auto-generated)" in claude_md
        assert "owt" in claude_md
        assert "TST:pytest" in claude_md

    def test_injection_is_idempotent(self, worktree: Path, store: MemoryStore) -> None:
        store.add_fact(
            "project is owt",
            MemoryType.REFERENCE,
            "id",
            layer=MemoryLayer.L0_IDENTITY,
        )

        inject_recall_section(worktree)
        first = (worktree / ".claude" / "CLAUDE.md").read_text()
        inject_recall_section(worktree)
        second = (worktree / ".claude" / "CLAUDE.md").read_text()

        assert first == second
        # Only one OWT-RECALL block
        assert second.count("OWT-RECALL-START") == 1
        assert second.count("OWT-RECALL-END") == 1

    def test_updates_existing_section(self, worktree: Path, store: MemoryStore) -> None:
        store.add_fact(
            "project is alpha",
            MemoryType.REFERENCE,
            "id",
            layer=MemoryLayer.L0_IDENTITY,
        )
        inject_recall_section(worktree)
        first = (worktree / ".claude" / "CLAUDE.md").read_text()
        assert "alpha" in first

        # Replace the fact with a new one
        store.conn.execute("DELETE FROM facts")
        store.conn.commit()
        store.add_fact(
            "project is beta",
            MemoryType.REFERENCE,
            "id",
            layer=MemoryLayer.L0_IDENTITY,
        )
        inject_recall_section(worktree)
        second = (worktree / ".claude" / "CLAUDE.md").read_text()

        assert "beta" in second
        assert "alpha" not in second
        assert second.count("OWT-RECALL-START") == 1

    def test_explicit_payload_override(self, worktree: Path) -> None:
        """Caller can pass a payload directly without hitting the store."""
        inject_recall_section(worktree, payload="## Custom\n- fact")
        content = (worktree / ".claude" / "CLAUDE.md").read_text()
        assert "## Custom" in content
        assert "- fact" in content

    def test_preserves_existing_content(self, worktree: Path, store: MemoryStore) -> None:
        original = (worktree / ".claude" / "CLAUDE.md").read_text()
        store.add_fact(
            "project is owt",
            MemoryType.REFERENCE,
            "id",
            layer=MemoryLayer.L0_IDENTITY,
        )
        inject_recall_section(worktree)
        content = (worktree / ".claude" / "CLAUDE.md").read_text()
        assert "Existing content." in content
        assert "# Project CLAUDE.md" in content

    def test_missing_claude_md_noop(self, tmp_path: Path) -> None:
        """Injecting into a worktree without CLAUDE.md is a safe no-op."""
        wt = tmp_path / "nomd"
        wt.mkdir()
        # Should not raise
        inject_recall_section(wt, payload="something")

    def test_empty_payload_removes_section(self, worktree: Path, store: MemoryStore) -> None:
        store.add_fact(
            "project is owt",
            MemoryType.REFERENCE,
            "id",
            layer=MemoryLayer.L0_IDENTITY,
        )
        inject_recall_section(worktree)
        content = (worktree / ".claude" / "CLAUDE.md").read_text()
        assert "OWT-RECALL-START" in content

        inject_recall_section(worktree, payload="")
        content = (worktree / ".claude" / "CLAUDE.md").read_text()
        assert "OWT-RECALL-START" not in content


class TestConfig:
    def test_recall_enabled_default(self) -> None:
        from open_orchestrator.config import Config

        cfg = Config()
        assert cfg.recall_enabled is True

    def test_recall_enabled_configurable(self) -> None:
        from open_orchestrator.config import Config

        cfg = Config(recall_enabled=False)
        assert cfg.recall_enabled is False
