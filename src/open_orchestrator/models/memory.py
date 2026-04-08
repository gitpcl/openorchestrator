"""Pydantic models for the worktree memory system.

Provides data models for:
- Memory entries with YAML frontmatter (name, description, type)
- MEMORY.md index management
- Topic file metadata
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """Classification of a memory entry."""

    DECISION = "decision"
    ARCHITECTURE = "architecture"
    CONVENTION = "convention"
    REFERENCE = "reference"


class MemoryEntry(BaseModel):
    """A single memory entry with YAML frontmatter fields."""

    name: str = Field(description="Short title for the memory")
    description: str = Field(description="One-line description used to judge relevance")
    memory_type: MemoryType = Field(description="Classification of the memory")
    filename: str = Field(description="Filename of the topic file (e.g. auth-flow.md)")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @property
    def index_line(self) -> str:
        """Format as a MEMORY.md index line (under 150 chars)."""
        line = f"- [{self.name}]({self.filename}) — {self.description}"
        if len(line) > 150:
            line = line[:147] + "..."
        return line


class TopicFile(BaseModel):
    """Parsed representation of a topic file with YAML frontmatter."""

    name: str = Field(description="Memory name from frontmatter")
    description: str = Field(description="One-line description from frontmatter")
    memory_type: MemoryType = Field(description="Type from frontmatter")
    body: str = Field(default="", description="Markdown body content after frontmatter")
    filename: str = Field(default="", description="Filename on disk")

    def to_frontmatter(self) -> str:
        """Render as a markdown file with YAML frontmatter."""
        return f"---\nname: {self.name}\ndescription: {self.description}\ntype: {self.memory_type.value}\n---\n\n{self.body}\n"

    def to_entry(self) -> MemoryEntry:
        """Convert to a MemoryEntry for index tracking."""
        return MemoryEntry(
            name=self.name,
            description=self.description,
            memory_type=self.memory_type,
            filename=self.filename,
        )


class SearchResult(BaseModel):
    """A single search hit across memory sources."""

    source: str = Field(description="Where the match was found (index, topic, transcript)")
    filename: str = Field(description="File that matched")
    line_number: int = Field(default=0, description="Line number of the match")
    line: str = Field(description="Matched line content")
    context: str = Field(default="", description="Surrounding context")


class MemoryLayer(str, Enum):
    """4-layer token-budgeted memory stack.

    L0: Project identity (50 tokens, always loaded)
    L1: Critical facts AAAK-compressed (200 tokens, always loaded)
    L2: Topic-scoped facts (on-demand, no fixed budget)
    L3: Deep storage (search-only, unlimited)
    """

    L0_IDENTITY = "L0"
    L1_CRITICAL = "L1"
    L2_TOPIC = "L2"
    L3_DEEP = "L3"


# Token budgets per layer (4 chars per token heuristic)
LAYER_BUDGETS: dict[MemoryLayer, int] = {
    MemoryLayer.L0_IDENTITY: 50,
    MemoryLayer.L1_CRITICAL: 200,
    MemoryLayer.L2_TOPIC: 0,  # 0 = no fixed budget
    MemoryLayer.L3_DEEP: 0,
}


class Fact(BaseModel):
    """A single recall fact stored in the memory store."""

    id: int | None = Field(default=None, description="Row ID after persistence")
    worktree: str = Field(description="Worktree the fact is scoped to (or 'global')")
    category: str = Field(description="Category tag (e.g. auth, db, ci)")
    kind: MemoryType = Field(description="Classification of the fact")
    layer: MemoryLayer = Field(description="Memory layer for token budgeting")
    content: str = Field(description="Full natural-language fact body")
    aaak: str | None = Field(default=None, description="AAAK-compressed form (L1 only)")
    token_estimate: int = Field(default=0, description="Token estimate for budgeting")
    source: str | None = Field(default=None, description="file:line, commit sha, or 'manual'")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class FactSearchHit(BaseModel):
    """A single FTS5 search hit with BM25 ranking."""

    fact: Fact = Field(description="The matched fact")
    rank: float = Field(description="BM25 rank (lower is better)")
    snippet: str = Field(default="", description="Highlighted snippet from match")


class Triple(BaseModel):
    """A temporal knowledge graph triple."""

    id: int | None = Field(default=None, description="Row ID after persistence")
    subject: str = Field(description="Subject entity")
    predicate: str = Field(description="Relationship predicate")
    object: str = Field(description="Object entity or value")
    valid_from: datetime = Field(default_factory=datetime.now)
    valid_to: datetime | None = Field(default=None, description="None means currently valid")
    source_fact_id: int | None = Field(default=None, description="Optional source fact reference")


class ContradictionGroup(BaseModel):
    """A group of conflicting triples sharing subject+predicate but different objects."""

    subject: str
    predicate: str
    conflicting_triples: list[Triple]
