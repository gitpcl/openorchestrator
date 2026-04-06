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
