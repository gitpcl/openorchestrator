"""Tests for deferred tool loading and search."""

from __future__ import annotations

from open_orchestrator.core.tool_search import (
    CHARS_PER_TOKEN,
    DEFAULT_TOKEN_BUDGET,
    DeferredToolLoader,
    ToolSchema,
    ToolSearchProvider,
    ToolSearchResult,
)


# ── ToolSchema Tests ─────────────────────────────────────────────────


class TestToolSchema:
    def test_to_json(self) -> None:
        schema = ToolSchema(name="test", description="A test tool", parameters={"type": "object"})
        j = schema.to_json()
        assert '"name": "test"' in j
        assert '"description": "A test tool"' in j

    def test_estimated_tokens(self) -> None:
        schema = ToolSchema(name="test", description="Short desc")
        tokens = schema.estimated_tokens
        assert tokens > 0
        assert tokens == max(1, len(schema.to_json()) // CHARS_PER_TOKEN)

    def test_frozen_dataclass(self) -> None:
        schema = ToolSchema(name="test", description="Immutable")
        try:
            schema.name = "changed"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass


# ── ToolSearchProvider Tests ─────────────────────────────────────────


class TestToolSearchProvider:
    def test_register_and_search_exact(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("claude", "AI coding assistant by Anthropic")
        results = provider.search("claude")
        assert len(results) == 1
        assert results[0].name == "claude"
        assert results[0].score == 1.0

    def test_search_name_substring(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("opencode", "Open source AI code editor")
        results = provider.search("open")
        assert len(results) == 1
        assert results[0].score == 0.8

    def test_search_description_match(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("droid", "AI coding tool by Factory")
        results = provider.search("Factory")
        assert len(results) == 1
        assert results[0].score == 0.5

    def test_search_keyword_match(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("claude", "AI coding assistant by Anthropic")
        provider.register_description("droid", "AI coding tool by Factory")
        results = provider.search("AI coding")
        assert len(results) == 2

    def test_search_no_match(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("claude", "AI assistant")
        results = provider.search("nonexistent_xyz")
        assert results == []

    def test_search_max_results(self) -> None:
        provider = ToolSearchProvider()
        for i in range(10):
            provider.register_description(f"tool-{i}", f"Tool number {i} for testing")
        results = provider.search("tool", max_results=3)
        assert len(results) == 3

    def test_search_sorted_by_score(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("claude", "AI coding assistant")
        provider.register_description("claude-dev", "Development version of claude")
        results = provider.search("claude")
        assert results[0].name == "claude"
        assert results[0].score >= results[1].score

    def test_register_from_schema(self) -> None:
        provider = ToolSearchProvider()
        schema = ToolSchema(name="custom", description="Custom build tool")
        provider.register_schema(schema)
        results = provider.search("custom")
        assert len(results) == 1

    def test_list_all(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("b-tool", "Second")
        provider.register_description("a-tool", "First")
        all_tools = provider.list_all()
        assert len(all_tools) == 2
        assert all_tools[0].name == "a-tool"  # sorted

    def test_case_insensitive_search(self) -> None:
        provider = ToolSearchProvider()
        provider.register_description("Claude", "AI Assistant")
        results = provider.search("claude")
        assert len(results) == 1
        assert results[0].score == 1.0


# ── DeferredToolLoader Tests ─────────────────────────────────────────


class TestDeferredToolLoader:
    def _make_schema(self, name: str, size: int = 100) -> ToolSchema:
        """Create a schema with approximately `size` chars of JSON."""
        desc = "x" * max(0, size - 50)
        return ToolSchema(name=name, description=desc)

    def test_register_and_load(self) -> None:
        loader = DeferredToolLoader()
        schema = ToolSchema(name="test", description="Test tool")
        loader.register_schema(schema)
        assert loader.is_registered("test")
        assert not loader.is_loaded("test")

        loaded = loader.load("test")
        assert loaded is not None
        assert loaded.name == "test"
        assert loader.is_loaded("test")

    def test_load_unregistered_returns_none(self) -> None:
        loader = DeferredToolLoader()
        assert loader.load("nonexistent") is None

    def test_load_updates_lru(self) -> None:
        loader = DeferredToolLoader(token_budget=50000)
        s1 = ToolSchema(name="first", description="First tool")
        s2 = ToolSchema(name="second", description="Second tool")
        loader.register_schema(s1)
        loader.register_schema(s2)

        loader.load("first")
        loader.load("second")
        loader.load("first")  # Move first to end

        schemas = loader.get_loaded_schemas()
        assert schemas[-1].name == "first"

    def test_token_budget_eviction(self) -> None:
        # Budget for ~2 small schemas
        loader = DeferredToolLoader(token_budget=50)
        s1 = self._make_schema("tool-1", size=80)
        s2 = self._make_schema("tool-2", size=80)
        s3 = self._make_schema("tool-3", size=80)

        loader.register_schema(s1)
        loader.register_schema(s2)
        loader.register_schema(s3)

        loader.load("tool-1")
        loader.load("tool-2")
        # Loading tool-3 should evict tool-1 (LRU)
        loader.load("tool-3")

        assert not loader.is_loaded("tool-1")
        assert loader.is_loaded("tool-3")

    def test_single_large_schema_still_loads(self) -> None:
        loader = DeferredToolLoader(token_budget=10)
        large = self._make_schema("big", size=500)
        loader.register_schema(large)

        loaded = loader.load("big")
        assert loaded is not None
        assert loader.is_loaded("big")

    def test_unload(self) -> None:
        loader = DeferredToolLoader()
        schema = ToolSchema(name="test", description="Test")
        loader.register_schema(schema)
        loader.load("test")
        assert loader.is_loaded("test")

        tokens_before = loader.tokens_used
        assert loader.unload("test") is True
        assert not loader.is_loaded("test")
        assert loader.tokens_used < tokens_before

    def test_unload_not_loaded(self) -> None:
        loader = DeferredToolLoader()
        assert loader.unload("nope") is False

    def test_tokens_remaining(self) -> None:
        loader = DeferredToolLoader(token_budget=1000)
        assert loader.tokens_remaining == 1000

        schema = ToolSchema(name="test", description="x" * 100)
        loader.register_schema(schema)
        loader.load("test")

        assert loader.tokens_remaining < 1000
        assert loader.tokens_remaining == loader.token_budget - loader.tokens_used

    def test_loaded_count(self) -> None:
        loader = DeferredToolLoader()
        assert loader.loaded_count == 0

        for i in range(3):
            schema = ToolSchema(name=f"tool-{i}", description=f"Tool {i}")
            loader.register_schema(schema)
            loader.load(f"tool-{i}")
        assert loader.loaded_count == 3

    def test_get_summary(self) -> None:
        loader = DeferredToolLoader(token_budget=5000)
        schema = ToolSchema(name="test", description="Test")
        loader.register_schema(schema)
        loader.load("test")

        summary = loader.get_summary()
        assert summary["registered"] == 1
        assert summary["loaded"] == 1
        assert summary["token_budget"] == 5000
        assert "test" in summary["loaded_tools"]  # type: ignore[operator]

    def test_reload_same_schema_no_double_count(self) -> None:
        loader = DeferredToolLoader()
        schema = ToolSchema(name="test", description="Test")
        loader.register_schema(schema)

        loader.load("test")
        tokens_after_first = loader.tokens_used
        loader.load("test")  # Reload (should just update LRU, not add tokens)
        assert loader.tokens_used == tokens_after_first

    def test_default_token_budget(self) -> None:
        loader = DeferredToolLoader()
        assert loader.token_budget == DEFAULT_TOKEN_BUDGET


# ── Config Integration Test ──────────────────────────────────────────


class TestConfigIntegration:
    def test_tool_token_budget_in_config(self) -> None:
        from open_orchestrator.config import Config

        config = Config()
        assert config.tool_token_budget == 8000

    def test_tool_token_budget_custom(self) -> None:
        from open_orchestrator.config import Config

        config = Config(tool_token_budget=2000)
        assert config.tool_token_budget == 2000

    def test_tool_token_budget_minimum(self) -> None:
        from pydantic import ValidationError

        from open_orchestrator.config import Config

        try:
            Config(tool_token_budget=50)
            assert False, "Should reject budget < 100"
        except ValidationError:
            pass


# ── ToolRegistry Integration ─────────────────────────────────────────


class TestRegistryIntegration:
    def test_list_all(self) -> None:
        from open_orchestrator.core.tool_registry import ToolRegistry

        registry = ToolRegistry()
        from open_orchestrator.core.tool_registry import CustomTool

        registry.register(CustomTool(name="test", binary="test-bin"))
        all_tools = registry.list_all()
        assert len(all_tools) == 1
        assert all_tools[0].name == "test"
