"""Tests for context compaction strategies."""

from __future__ import annotations

from open_orchestrator.core.compaction import (
    DEFAULT_KEEP_RECENT,
    MICROCOMPACT_THRESHOLD_CHARS,
    microcompact,
    reactive_compact,
    snip,
)
from open_orchestrator.models.compaction import (
    CompactionResult,
    Message,
    MessageRole,
)


def _msg(role: str = "user", content: str = "Hello", protected: bool = False) -> Message:
    """Helper to create a Message."""
    return Message(role=MessageRole(role), content=content, protected=protected)


def _make_history(n: int, *, system: bool = True) -> list[Message]:
    """Build a synthetic message history with n user/assistant pairs."""
    messages: list[Message] = []
    if system:
        messages.append(_msg("system", "You are a coding agent.", protected=True))
    for i in range(n):
        messages.append(_msg("user", f"User message {i}"))
        messages.append(_msg("assistant", f"Assistant response {i}"))
    return messages


# ── Model Tests ──────────────────────────────────────────────────────


class TestMessage:
    def test_estimated_tokens(self) -> None:
        msg = _msg(content="x" * 400)
        assert msg.estimated_tokens == 100  # 400 / 4

    def test_estimated_tokens_minimum(self) -> None:
        msg = _msg(content="hi")
        assert msg.estimated_tokens >= 1

    def test_protected_flag(self) -> None:
        msg = _msg(protected=True)
        assert msg.protected is True


class TestCompactionResult:
    def test_tokens_freed(self) -> None:
        r = CompactionResult(
            strategy="snip",
            messages_before=10,
            messages_after=5,
            tokens_before=1000,
            tokens_after=400,
        )
        assert r.tokens_freed == 600

    def test_compression_ratio(self) -> None:
        r = CompactionResult(
            strategy="microcompact",
            messages_before=10,
            messages_after=10,
            tokens_before=1000,
            tokens_after=250,
        )
        assert r.compression_ratio == 0.25

    def test_compression_ratio_zero_before(self) -> None:
        r = CompactionResult(
            strategy="snip",
            messages_before=0,
            messages_after=0,
            tokens_before=0,
            tokens_after=0,
        )
        assert r.compression_ratio == 1.0


# ── Snip Tests ───────────────────────────────────────────────────────


class TestSnip:
    def test_snip_preserves_system_and_recent(self) -> None:
        history = _make_history(20)
        result_msgs, result = snip(history, keep_recent=4)

        # System message preserved
        assert result_msgs[0].role == MessageRole.SYSTEM
        # Recent 4 messages preserved
        assert result_msgs[-1].content == "Assistant response 19"
        assert result.messages_removed > 0
        assert result.strategy == "snip"

    def test_snip_short_history_no_change(self) -> None:
        history = _make_history(3)
        result_msgs, result = snip(history, keep_recent=10)
        assert len(result_msgs) == len(history)
        assert result.messages_removed == 0

    def test_snip_preserves_protected(self) -> None:
        history = [
            _msg("system", "System prompt", protected=True),
            _msg("user", "Old message 1"),
            _msg("assistant", "Protected response", protected=True),
            _msg("user", "Old message 2"),
            _msg("user", "Recent 1"),
            _msg("assistant", "Recent 2"),
        ]
        result_msgs, result = snip(history, keep_recent=2)
        # System + protected middle + 2 recent
        protected_kept = [m for m in result_msgs if m.protected]
        assert len(protected_kept) == 2

    def test_snip_tokens_decrease(self) -> None:
        history = _make_history(50)
        _, result = snip(history, keep_recent=5)
        assert result.tokens_after < result.tokens_before
        assert result.tokens_freed > 0

    def test_snip_empty_history(self) -> None:
        result_msgs, result = snip([])
        assert result_msgs == []
        assert result.messages_removed == 0


# ── Microcompact Tests ───────────────────────────────────────────────


class TestMicrocompact:
    def test_large_output_summarized(self) -> None:
        large_content = "Line {}\n".format("x" * 100) * 200  # ~20K chars
        history = [
            _msg("system", "System", protected=True),
            _msg("user", "Run tests"),
            _msg("tool", large_content),
            _msg("assistant", "Tests passed"),
        ]
        result_msgs, result = microcompact(history)
        assert result.messages_summarized == 1
        # Tool message was replaced
        tool_msg = [m for m in result_msgs if m.role == MessageRole.TOOL][0]
        assert len(tool_msg.content) < len(large_content)
        assert "compacted" in tool_msg.content

    def test_small_output_unchanged(self) -> None:
        history = [
            _msg("user", "Hi"),
            _msg("assistant", "Hello"),
        ]
        result_msgs, result = microcompact(history)
        assert result.messages_summarized == 0
        assert result_msgs == history

    def test_protected_not_compacted(self) -> None:
        large = "x" * (MICROCOMPACT_THRESHOLD_CHARS + 1000)
        history = [
            _msg("assistant", large, protected=True),
        ]
        result_msgs, result = microcompact(history)
        assert result.messages_summarized == 0
        assert result_msgs[0].content == large

    def test_system_not_compacted(self) -> None:
        large = "x" * (MICROCOMPACT_THRESHOLD_CHARS + 1000)
        history = [_msg("system", large)]
        result_msgs, _ = microcompact(history)
        assert result_msgs[0].content == large

    def test_summary_within_limit(self) -> None:
        large = "x\n" * 10000  # 20K chars
        history = [_msg("tool", large)]
        result_msgs, _ = microcompact(history, summary_max_chars=200)
        assert len(result_msgs[0].content) <= 200

    def test_tokens_decrease_after_compact(self) -> None:
        large = "data " * 5000  # ~25K chars
        history = [_msg("tool", large)]
        _, result = microcompact(history)
        assert result.tokens_after < result.tokens_before


# ── Reactive Compact Tests ───────────────────────────────────────────


class TestReactiveCompact:
    def test_under_budget_no_change(self) -> None:
        history = _make_history(3)
        result_msgs, result = reactive_compact(history, token_limit=100_000)
        assert result.messages_removed == 0
        assert result.messages_summarized == 0
        assert len(result_msgs) == len(history)

    def test_over_budget_triggers_compaction(self) -> None:
        # Create history that exceeds a small token limit
        history = _make_history(50)  # ~101 messages, ~6K+ tokens
        result_msgs, result = reactive_compact(history, token_limit=100, keep_recent=3)
        assert result.strategy == "reactive"
        assert result.tokens_after < result.tokens_before
        assert len(result_msgs) < len(history)

    def test_microcompact_runs_first(self) -> None:
        # Large tool output + small token limit
        large = "x" * 50_000
        history = [
            _msg("system", "System", protected=True),
            _msg("tool", large),
            _msg("user", "Question"),
            _msg("assistant", "Answer"),
        ]
        _, result = reactive_compact(history, token_limit=500)
        assert result.messages_summarized >= 1

    def test_snip_runs_after_microcompact(self) -> None:
        # Many messages + strict budget
        history = _make_history(100)
        _, result = reactive_compact(history, token_limit=50, keep_recent=2)
        assert result.messages_removed > 0

    def test_result_metadata_combined(self) -> None:
        large = "x" * 50_000
        history = [
            _msg("system", "System", protected=True),
            _msg("tool", large),
        ] + [_msg("user", f"Msg {i}") for i in range(50)]

        _, result = reactive_compact(history, token_limit=50, keep_recent=2)
        assert result.strategy == "reactive"
        # At least one strategy should have fired
        assert result.messages_removed > 0 or result.messages_summarized > 0

    def test_empty_history(self) -> None:
        result_msgs, result = reactive_compact([], token_limit=100)
        assert result_msgs == []
        assert result.tokens_before == 0
