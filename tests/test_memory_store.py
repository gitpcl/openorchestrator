"""Tests for the SQLite + FTS5 recall memory store."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from open_orchestrator.core.memory_store import (
    MemoryStore,
    MemoryStoreConfig,
    estimate_tokens,
)
from open_orchestrator.models.memory import (
    LAYER_BUDGETS,
    MemoryLayer,
    MemoryType,
)


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    config = MemoryStoreConfig(storage_path=tmp_path / "recall.db")
    store = MemoryStore(config)
    yield store
    store.close()


class TestEstimateTokens:
    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 1

    def test_short_text(self) -> None:
        assert estimate_tokens("abcd") == 1

    def test_longer_text(self) -> None:
        # 40 chars / 4 = 10 tokens
        assert estimate_tokens("a" * 40) == 10

    def test_rounds_up(self) -> None:
        # 5 chars / 4 = 1.25 → 2
        assert estimate_tokens("hello") == 2


class TestFactsCRUD:
    def test_add_and_get_fact(self, store: MemoryStore) -> None:
        fact = store.add_fact(
            content="Use pytest for Python tests",
            kind=MemoryType.CONVENTION,
            category="testing",
            worktree="global",
            layer=MemoryLayer.L2_TOPIC,
        )
        assert fact.id is not None
        assert fact.content == "Use pytest for Python tests"
        loaded = store.get_fact(fact.id)
        assert loaded is not None
        assert loaded.content == fact.content
        assert loaded.kind == MemoryType.CONVENTION
        assert loaded.layer == MemoryLayer.L2_TOPIC

    def test_list_facts_filters(self, store: MemoryStore) -> None:
        store.add_fact("Fact A", MemoryType.DECISION, "auth", worktree="wt-a")
        store.add_fact("Fact B", MemoryType.DECISION, "db", worktree="wt-a")
        store.add_fact("Fact C", MemoryType.DECISION, "auth", worktree="wt-b")
        assert len(store.list_facts()) == 3
        assert len(store.list_facts(worktree="wt-a")) == 2
        assert len(store.list_facts(category="auth")) == 2
        assert len(store.list_facts(worktree="wt-a", category="db")) == 1

    def test_update_fact(self, store: MemoryStore) -> None:
        fact = store.add_fact("Original", MemoryType.DECISION, "cat")
        updated = store.update_fact(fact.id, content="Updated text")
        assert updated is not None
        assert updated.content == "Updated text"
        assert updated.token_estimate == estimate_tokens("Updated text")

    def test_update_nonexistent_fact(self, store: MemoryStore) -> None:
        assert store.update_fact(999, content="nope") is None

    def test_delete_fact(self, store: MemoryStore) -> None:
        fact = store.add_fact("gone", MemoryType.DECISION, "cat")
        assert store.delete_fact(fact.id) is True
        assert store.get_fact(fact.id) is None

    def test_delete_nonexistent_fact(self, store: MemoryStore) -> None:
        assert store.delete_fact(999) is False


class TestFTSSearch:
    def test_search_returns_matches(self, store: MemoryStore) -> None:
        store.add_fact(
            "Use pytest with fixtures for testing",
            MemoryType.CONVENTION,
            "testing",
        )
        store.add_fact(
            "Use mypy for type checking",
            MemoryType.CONVENTION,
            "tooling",
        )
        hits = store.search_facts("pytest")
        assert len(hits) == 1
        assert "pytest" in hits[0].fact.content

    def test_search_bm25_ranking(self, store: MemoryStore) -> None:
        store.add_fact("auth flow with JWT", MemoryType.ARCHITECTURE, "auth")
        store.add_fact("database migration notes", MemoryType.REFERENCE, "db")
        store.add_fact("auth auth auth JWT", MemoryType.ARCHITECTURE, "auth")
        hits = store.search_facts("auth JWT")
        assert len(hits) >= 2
        # BM25 lower rank = better match
        assert hits[0].rank <= hits[-1].rank

    def test_search_worktree_filter(self, store: MemoryStore) -> None:
        store.add_fact("feature x impl", MemoryType.DECISION, "x", worktree="wt-a")
        store.add_fact("feature x impl", MemoryType.DECISION, "x", worktree="wt-b")
        hits = store.search_facts("feature", worktree="wt-a")
        assert len(hits) == 1
        assert hits[0].fact.worktree == "wt-a"

    def test_search_empty_query(self, store: MemoryStore) -> None:
        store.add_fact("something", MemoryType.DECISION, "x")
        assert store.search_facts("") == []
        assert store.search_facts("   ") == []

    def test_search_survives_updates(self, store: MemoryStore) -> None:
        """FTS5 triggers must keep the virtual table in sync on UPDATE."""
        fact = store.add_fact("old content word", MemoryType.DECISION, "x")
        store.update_fact(fact.id, content="new content phrase")
        assert store.search_facts("old") == []
        hits = store.search_facts("phrase")
        assert len(hits) == 1

    def test_search_survives_deletes(self, store: MemoryStore) -> None:
        """FTS5 triggers must keep the virtual table in sync on DELETE."""
        fact = store.add_fact("ephemeral fact", MemoryType.DECISION, "x")
        assert len(store.search_facts("ephemeral")) == 1
        store.delete_fact(fact.id)
        assert store.search_facts("ephemeral") == []


class TestLayerBudgets:
    def test_l0_budget_enforced(self, store: MemoryStore) -> None:
        budget = LAYER_BUDGETS[MemoryLayer.L0_IDENTITY]
        # Fill the budget with large facts
        big_content = "x" * (budget * 4)  # ~budget tokens
        fact = store.add_fact(
            big_content,
            MemoryType.REFERENCE,
            "identity",
            layer=MemoryLayer.L0_IDENTITY,
        )
        assert fact.layer == MemoryLayer.L0_IDENTITY
        # Next fact should overflow and get demoted
        overflow = store.add_fact(
            "extra identity fact",
            MemoryType.REFERENCE,
            "identity",
            layer=MemoryLayer.L0_IDENTITY,
        )
        assert overflow.layer == MemoryLayer.L2_TOPIC

    def test_l1_budget_enforced(self, store: MemoryStore) -> None:
        budget = LAYER_BUDGETS[MemoryLayer.L1_CRITICAL]
        big = "y" * (budget * 4)
        fact = store.add_fact(
            big,
            MemoryType.DECISION,
            "crit",
            layer=MemoryLayer.L1_CRITICAL,
        )
        assert fact.layer == MemoryLayer.L1_CRITICAL
        overflow = store.add_fact(
            "another crit",
            MemoryType.DECISION,
            "crit",
            layer=MemoryLayer.L1_CRITICAL,
        )
        assert overflow.layer == MemoryLayer.L2_TOPIC

    def test_l2_no_budget(self, store: MemoryStore) -> None:
        # Adding many L2 facts should never demote
        for i in range(20):
            fact = store.add_fact(
                f"L2 fact {i} " + "x" * 200,
                MemoryType.REFERENCE,
                "bulk",
                layer=MemoryLayer.L2_TOPIC,
            )
            assert fact.layer == MemoryLayer.L2_TOPIC

    def test_get_l0_l1_payload_under_budget(self, store: MemoryStore) -> None:
        store.add_fact("owt project", MemoryType.REFERENCE, "id", layer=MemoryLayer.L0_IDENTITY)
        store.add_fact(
            "uses pytest",
            MemoryType.CONVENTION,
            "crit",
            layer=MemoryLayer.L1_CRITICAL,
            aaak="TST:pytest",
        )
        payload = store.get_l0_l1_payload()
        assert "Identity" in payload
        assert "Critical" in payload
        assert "owt project" in payload
        assert "TST:pytest" in payload
        assert estimate_tokens(payload) < 250


class TestKnowledgeGraph:
    def test_add_and_query_triple(self, store: MemoryStore) -> None:
        store.kg_add("owt", "uses", "pytest")
        results = store.kg_query("owt")
        assert len(results) == 1
        assert results[0].predicate == "uses"
        assert results[0].object == "pytest"
        assert results[0].valid_to is None

    def test_invalidate_triple(self, store: MemoryStore) -> None:
        store.kg_add("owt", "version", "0.2.0")
        count = store.kg_invalidate("owt", "version")
        assert count == 1
        assert store.kg_query("owt", "version") == []

    def test_point_in_time_query(self, store: MemoryStore) -> None:
        t1 = datetime(2026, 1, 1, 12, 0, 0)
        t2 = datetime(2026, 2, 1, 12, 0, 0)
        store.kg_add("owt", "version", "0.1.0", valid_from=t1)
        store.kg_invalidate("owt", "version", at=t2)
        store.kg_add("owt", "version", "0.2.0", valid_from=t2)

        past = store.kg_query("owt", "version", at=t1 + timedelta(hours=1))
        assert len(past) == 1
        assert past[0].object == "0.1.0"

        now = store.kg_query("owt", "version")
        assert len(now) == 1
        assert now[0].object == "0.2.0"

    def test_timeline_chronological(self, store: MemoryStore) -> None:
        t1 = datetime(2026, 1, 1)
        t2 = datetime(2026, 2, 1)
        t3 = datetime(2026, 3, 1)
        store.kg_add("owt", "version", "0.3.0", valid_from=t3)
        store.kg_add("owt", "version", "0.1.0", valid_from=t1)
        store.kg_add("owt", "version", "0.2.0", valid_from=t2)
        timeline = store.kg_timeline("owt")
        assert len(timeline) == 3
        assert [t.object for t in timeline] == ["0.1.0", "0.2.0", "0.3.0"]

    def test_entities_distinct(self, store: MemoryStore) -> None:
        store.kg_add("owt", "uses", "sqlite")
        store.kg_add("owt", "lang", "python")
        store.kg_add("claude", "uses", "anthropic")
        entities = store.kg_entities()
        assert entities == ["claude", "owt"]

    def test_detect_contradictions(self, store: MemoryStore) -> None:
        store.kg_add("owt", "version", "0.1.0")
        store.kg_add("owt", "version", "0.2.0")
        store.kg_add("owt", "lang", "python")
        groups = store.detect_contradictions()
        assert len(groups) == 1
        assert groups[0].subject == "owt"
        assert groups[0].predicate == "version"
        assert len(groups[0].conflicting_triples) == 2

    def test_no_contradictions_when_invalidated(self, store: MemoryStore) -> None:
        """Once one is invalidated, the remaining valid triple is not a contradiction."""
        store.kg_add("owt", "version", "0.1.0")
        store.kg_add("owt", "version", "0.2.0")
        store.kg_invalidate("owt", "version", at=datetime.now() + timedelta(seconds=1))
        store.kg_add("owt", "version", "0.3.0")
        groups = store.detect_contradictions()
        assert len(groups) == 0 or all(len(g.conflicting_triples) < 2 for g in groups)

    def test_resolve_contradiction(self, store: MemoryStore) -> None:
        store.kg_add("owt", "version", "0.1.0")
        keeper = store.kg_add("owt", "version", "0.2.0")
        groups = store.detect_contradictions()
        assert len(groups) == 1
        invalidated = store.resolve_contradiction(groups[0], keep_id=keeper.id)
        assert invalidated == 1
        # No more contradictions
        assert store.detect_contradictions() == []
        # Keeper is still valid
        remaining = store.kg_query("owt", "version")
        assert len(remaining) == 1
        assert remaining[0].id == keeper.id

    def test_audit_trail_preserved(self, store: MemoryStore) -> None:
        """Invalidated triples are not deleted — they remain in timeline."""
        store.kg_add("owt", "version", "0.1.0")
        store.kg_invalidate("owt", "version")
        timeline = store.kg_timeline("owt")
        assert len(timeline) == 1
        assert timeline[0].valid_to is not None


class TestContextManager:
    def test_context_manager(self, tmp_path: Path) -> None:
        config = MemoryStoreConfig(storage_path=tmp_path / "recall.db")
        with MemoryStore(config) as store:
            store.add_fact("test", MemoryType.DECISION, "x")
            assert len(store.list_facts()) == 1
