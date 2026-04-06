"""Deferred tool loading and search for AI agent sessions.

Implements the deferred tool pattern: agents start with only a ToolSearch
meta-tool, and full schemas are injected on demand. Prevents token
explosion when many custom tools are registered.

Components:
- ToolSearchProvider: fuzzy search over registered tools by name/description
- DeferredToolLoader: lazy schema injection with LRU eviction
- Token budget tracking to cap schema growth
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from open_orchestrator.core.tool_protocol import AIToolProtocol

logger = logging.getLogger(__name__)

# Approximate token cost: ~4 chars per token
CHARS_PER_TOKEN = 4
DEFAULT_TOKEN_BUDGET = 8000


@dataclass(frozen=True)
class ToolSchema:
    """JSON schema definition for a tool, injected on demand."""

    name: str
    description: str
    parameters: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {"name": self.name, "description": self.description, "parameters": self.parameters},
            indent=2,
        )

    @property
    def estimated_tokens(self) -> int:
        """Estimate token count from JSON size."""
        return max(1, len(self.to_json()) // CHARS_PER_TOKEN)


class ToolSearchResult(BaseModel):
    """Result from a tool search query."""

    name: str = Field(description="Tool name")
    description: str = Field(description="Short description of what the tool does")
    score: float = Field(default=0.0, description="Relevance score (0-1)")
    loaded: bool = Field(default=False, description="Whether full schema is already loaded")


class ToolSearchProvider:
    """Fuzzy search over registered tools by name and description."""

    def __init__(self) -> None:
        self._tool_descriptions: dict[str, str] = {}

    def register_description(self, name: str, description: str) -> None:
        """Register a tool's searchable description."""
        self._tool_descriptions[name] = description

    def register_from_protocol(self, tool: AIToolProtocol) -> None:
        """Register a tool from its protocol interface."""
        desc = f"{tool.name} — AI coding tool ({tool.binary})"
        if tool.install_hint:
            desc += f". {tool.install_hint}"
        self._tool_descriptions[tool.name] = desc

    def register_schema(self, schema: ToolSchema) -> None:
        """Register a tool from its schema definition."""
        self._tool_descriptions[schema.name] = schema.description

    def search(self, query: str, *, max_results: int = 5) -> list[ToolSearchResult]:
        """Search tools by fuzzy matching against names and descriptions.

        Scoring: exact name match = 1.0, name substring = 0.8,
        description substring = 0.5, individual keyword match = 0.3.
        """
        query_lower = query.lower()
        keywords = query_lower.split()
        results: list[ToolSearchResult] = []

        for name, description in self._tool_descriptions.items():
            name_lower = name.lower()
            desc_lower = description.lower()
            score = 0.0

            if query_lower == name_lower:
                score = 1.0
            elif query_lower in name_lower:
                score = 0.8
            elif query_lower in desc_lower:
                score = 0.5
            else:
                matched = sum(1 for kw in keywords if kw in name_lower or kw in desc_lower)
                if matched and keywords:
                    score = 0.3 * (matched / len(keywords))

            if score > 0:
                results.append(
                    ToolSearchResult(
                        name=name,
                        description=description,
                        score=score,
                    )
                )

        results.sort(key=lambda r: (-r.score, r.name))
        return results[:max_results]

    def list_all(self) -> list[ToolSearchResult]:
        """List all registered tools (no filtering)."""
        return [
            ToolSearchResult(name=name, description=desc, score=0.0) for name, desc in sorted(self._tool_descriptions.items())
        ]


class DeferredToolLoader:
    """Lazy schema injection with token budget and LRU eviction.

    Agents start with only ToolSearch available. When they request a
    specific tool, its full schema is injected. An LRU cache evicts
    the least-recently-used schema when the token budget is exceeded.
    """

    def __init__(self, token_budget: int = DEFAULT_TOKEN_BUDGET) -> None:
        self._token_budget = token_budget
        self._schemas: dict[str, ToolSchema] = {}
        self._loaded: OrderedDict[str, ToolSchema] = OrderedDict()
        self._tokens_used = 0

    @property
    def token_budget(self) -> int:
        return self._token_budget

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def tokens_remaining(self) -> int:
        return max(0, self._token_budget - self._tokens_used)

    @property
    def loaded_count(self) -> int:
        return len(self._loaded)

    def register_schema(self, schema: ToolSchema) -> None:
        """Register a tool schema for deferred loading."""
        self._schemas[schema.name] = schema

    def is_registered(self, name: str) -> bool:
        """Check if a tool schema is registered (but not necessarily loaded)."""
        return name in self._schemas

    def is_loaded(self, name: str) -> bool:
        """Check if a tool schema is currently loaded (injected)."""
        return name in self._loaded

    def load(self, name: str) -> ToolSchema | None:
        """Load a tool schema, injecting it into the active set.

        Returns the schema if successfully loaded, None if not registered.
        Evicts LRU schemas if token budget would be exceeded.
        """
        if name in self._loaded:
            # Move to end (most recently used)
            self._loaded.move_to_end(name)
            return self._loaded[name]

        schema = self._schemas.get(name)
        if schema is None:
            return None

        needed = schema.estimated_tokens

        # Evict LRU schemas until we have room
        while self._tokens_used + needed > self._token_budget and self._loaded:
            evicted_name, evicted_schema = self._loaded.popitem(last=False)
            self._tokens_used -= evicted_schema.estimated_tokens
            logger.info("Evicted tool schema '%s' (%d tokens)", evicted_name, evicted_schema.estimated_tokens)

        # If single schema exceeds budget, load it anyway (at least one must fit)
        self._loaded[name] = schema
        self._tokens_used += needed
        logger.info("Loaded tool schema '%s' (%d tokens, %d/%d used)", name, needed, self._tokens_used, self._token_budget)
        return schema

    def unload(self, name: str) -> bool:
        """Explicitly unload a schema. Returns True if it was loaded."""
        if name not in self._loaded:
            return False
        schema = self._loaded.pop(name)
        self._tokens_used -= schema.estimated_tokens
        logger.info("Unloaded tool schema '%s'", name)
        return True

    def get_loaded_schemas(self) -> list[ToolSchema]:
        """Return all currently loaded schemas (in LRU order)."""
        return list(self._loaded.values())

    def get_summary(self) -> dict[str, object]:
        """Return a summary of loader state for diagnostics."""
        return {
            "registered": len(self._schemas),
            "loaded": len(self._loaded),
            "tokens_used": self._tokens_used,
            "token_budget": self._token_budget,
            "loaded_tools": list(self._loaded.keys()),
        }
