"""Context compaction strategies for managing agent message histories.

Three composable strategies:
1. Snip — remove oldest messages while preserving system prompt + recent N
2. Microcompact — replace large tool outputs (10K+ chars) with short summaries
3. Reactive — combine strategies on context overflow, produce retry-ready output

All strategies are pure functions (stateless transforms) operating on
lists of Message objects.
"""

from __future__ import annotations

import logging

from open_orchestrator.models.compaction import (
    CompactionResult,
    Message,
    MessageRole,
)

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_KEEP_RECENT = 10
MICROCOMPACT_THRESHOLD_CHARS = 10_000
MICROCOMPACT_SUMMARY_MAX_CHARS = 500
DEFAULT_TOKEN_LIMIT = 100_000


def _estimate_total_tokens(messages: list[Message]) -> int:
    """Sum estimated tokens across all messages."""
    return sum(m.estimated_tokens for m in messages)


def snip(
    messages: list[Message],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[Message], CompactionResult]:
    """Remove oldest messages while preserving system prompt and recent N.

    Protected messages and system messages are never removed.
    Messages are removed from the middle (after system, before recent).

    Args:
        messages: Full message history.
        keep_recent: Number of most recent messages to preserve.

    Returns:
        Tuple of (compacted messages, result metadata).
    """
    tokens_before = _estimate_total_tokens(messages)

    if len(messages) <= keep_recent:
        return messages, CompactionResult(
            strategy="snip",
            messages_before=len(messages),
            messages_after=len(messages),
            tokens_before=tokens_before,
            tokens_after=tokens_before,
        )

    # Partition: system/protected head, droppable middle, recent tail
    head: list[Message] = []
    for msg in messages:
        if msg.role == MessageRole.SYSTEM or msg.protected:
            head.append(msg)
        else:
            break

    # Everything after head
    rest = messages[len(head) :]
    tail = rest[-keep_recent:] if len(rest) > keep_recent else rest
    middle = rest[: len(rest) - len(tail)]

    # Keep protected messages from the middle
    kept_from_middle = [m for m in middle if m.protected]
    removed_count = len(middle) - len(kept_from_middle)

    result_messages = head + kept_from_middle + tail
    tokens_after = _estimate_total_tokens(result_messages)

    return result_messages, CompactionResult(
        strategy="snip",
        messages_before=len(messages),
        messages_after=len(result_messages),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=removed_count,
    )


def microcompact(
    messages: list[Message],
    *,
    threshold_chars: int = MICROCOMPACT_THRESHOLD_CHARS,
    summary_max_chars: int = MICROCOMPACT_SUMMARY_MAX_CHARS,
) -> tuple[list[Message], CompactionResult]:
    """Replace large tool outputs with short summaries.

    Messages with content exceeding threshold_chars are replaced with
    a truncated summary. System and protected messages are not touched.

    Args:
        messages: Full message history.
        threshold_chars: Minimum content length to trigger compaction.
        summary_max_chars: Maximum length of the replacement summary.

    Returns:
        Tuple of (compacted messages, result metadata).
    """
    tokens_before = _estimate_total_tokens(messages)
    result: list[Message] = []
    summarized = 0

    for msg in messages:
        if msg.protected or msg.role == MessageRole.SYSTEM:
            result.append(msg)
            continue

        if len(msg.content) > threshold_chars:
            # Build summary: first and last lines + size note
            lines = msg.content.splitlines()
            head_lines = lines[:3]
            tail_lines = lines[-2:] if len(lines) > 5 else []
            summary_parts = head_lines + ["...", f"[{len(msg.content)} chars compacted]"] + tail_lines
            summary = "\n".join(summary_parts)[:summary_max_chars]

            result.append(
                Message(
                    role=msg.role,
                    content=summary,
                    name=msg.name,
                    protected=False,
                )
            )
            summarized += 1
        else:
            result.append(msg)

    tokens_after = _estimate_total_tokens(result)

    return result, CompactionResult(
        strategy="microcompact",
        messages_before=len(messages),
        messages_after=len(result),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_summarized=summarized,
    )


def reactive_compact(
    messages: list[Message],
    *,
    token_limit: int = DEFAULT_TOKEN_LIMIT,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[Message], CompactionResult]:
    """Auto-compact on context overflow, combining strategies.

    Applies strategies in order of aggressiveness:
    1. Microcompact large outputs first (cheapest, preserves structure)
    2. Snip old messages if still over budget

    Returns retry-ready messages under the token limit.

    Args:
        messages: Full message history.
        token_limit: Target token budget.
        keep_recent: Messages to preserve during snip.

    Returns:
        Tuple of (compacted messages, combined result metadata).
    """
    tokens_before = _estimate_total_tokens(messages)
    total_removed = 0
    total_summarized = 0
    current = messages

    # Strategy 1: Microcompact large outputs
    if _estimate_total_tokens(current) > token_limit:
        current, micro_result = microcompact(current)
        total_summarized += micro_result.messages_summarized
        logger.info(
            "Microcompact: %d messages summarized, %d tokens freed",
            micro_result.messages_summarized,
            micro_result.tokens_freed,
        )

    # Strategy 2: Snip old messages
    if _estimate_total_tokens(current) > token_limit:
        current, snip_result = snip(current, keep_recent=keep_recent)
        total_removed += snip_result.messages_removed
        logger.info(
            "Snip: %d messages removed, %d tokens freed",
            snip_result.messages_removed,
            snip_result.tokens_freed,
        )

    tokens_after = _estimate_total_tokens(current)

    return current, CompactionResult(
        strategy="reactive",
        messages_before=len(messages),
        messages_after=len(current),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        messages_removed=total_removed,
        messages_summarized=total_summarized,
    )
