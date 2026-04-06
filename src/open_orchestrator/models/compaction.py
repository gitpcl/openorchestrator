"""Pydantic models for context compaction strategies.

Provides data models for:
- Message representation (role, content, token estimate)
- Compaction results with metadata tracking
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# Token estimation: ~4 chars per token
CHARS_PER_TOKEN = 4


class MessageRole(str, Enum):
    """Role of a message in the conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    """A single message in a conversation history."""

    role: MessageRole = Field(description="Message role")
    content: str = Field(description="Message content")
    name: str | None = Field(default=None, description="Optional tool/function name")
    protected: bool = Field(default=False, description="If True, never compacted away")

    @property
    def estimated_tokens(self) -> int:
        """Estimate token count from content length."""
        return max(1, len(self.content) // CHARS_PER_TOKEN)


class CompactionResult(BaseModel):
    """Result of a compaction operation with audit metadata."""

    strategy: str = Field(description="Compaction strategy used (snip, microcompact, reactive)")
    messages_before: int = Field(description="Message count before compaction")
    messages_after: int = Field(description="Message count after compaction")
    tokens_before: int = Field(description="Estimated tokens before")
    tokens_after: int = Field(description="Estimated tokens after")
    messages_removed: int = Field(default=0, description="Messages dropped")
    messages_summarized: int = Field(default=0, description="Messages replaced with summaries")

    @property
    def tokens_freed(self) -> int:
        """Tokens recovered by compaction."""
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def compression_ratio(self) -> float:
        """Ratio of tokens after vs before (lower = more compressed)."""
        if self.tokens_before == 0:
            return 1.0
        return self.tokens_after / self.tokens_before
