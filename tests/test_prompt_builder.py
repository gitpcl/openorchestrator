"""Tests for prompt_builder module — pure logic, zero mocking."""

from __future__ import annotations

import pytest

from open_orchestrator.core.prompt_builder import (
    PromptBuilder,
    TaskType,
    classify_task,
    get_protocol_for_task,
)


class TestClassifyTask:
    """Test classify_task() keyword heuristics."""

    def test_bugfix_keywords(self) -> None:
        assert classify_task("fix login bug") == TaskType.BUGFIX
        assert classify_task("Fix broken redirect") == TaskType.BUGFIX
        assert classify_task("Hotfix for prod crash") == TaskType.BUGFIX
        assert classify_task("regression in auth flow") == TaskType.BUGFIX

    def test_feature_keywords(self) -> None:
        assert classify_task("add payment integration") == TaskType.FEATURE
        assert classify_task("implement JWT auth") == TaskType.FEATURE
        assert classify_task("create user dashboard") == TaskType.FEATURE
        assert classify_task("build API endpoints") == TaskType.FEATURE

    def test_refactor_keywords(self) -> None:
        assert classify_task("refactor database queries") == TaskType.REFACTOR
        assert classify_task("clean up utils module") == TaskType.REFACTOR
        assert classify_task("simplify the auth module") == TaskType.REFACTOR
        assert classify_task("extract helper functions") == TaskType.REFACTOR

    def test_test_keywords(self) -> None:
        assert classify_task("add test coverage for auth") == TaskType.TEST
        assert classify_task("write spec for parser") == TaskType.TEST
        assert classify_task("improve coverage metrics") == TaskType.TEST

    def test_docs_keywords(self) -> None:
        assert classify_task("update README") == TaskType.DOCS
        assert classify_task("add changelog entry") == TaskType.DOCS
        assert classify_task("write API guide") == TaskType.DOCS

    def test_fallback_to_feature(self) -> None:
        assert classify_task("something generic") == TaskType.FEATURE
        assert classify_task("do the thing") == TaskType.FEATURE

    def test_first_match_wins(self) -> None:
        # "fix" matches before "add"
        assert classify_task("fix and add new feature") == TaskType.BUGFIX
        # "test" matches before "refactor"
        assert classify_task("test the refactored code") == TaskType.TEST

    def test_case_insensitive(self) -> None:
        assert classify_task("FIX LOGIN BUG") == TaskType.BUGFIX
        assert classify_task("Add Payment") == TaskType.FEATURE


class TestPromptBuilder:
    """Test PromptBuilder immutability and assembly."""

    def test_empty_build(self) -> None:
        builder = PromptBuilder()
        assert builder.build() == ""

    def test_add_section_returns_new_builder(self) -> None:
        original = PromptBuilder()
        new_builder = original.add_section("role", "You are an agent.", priority=100)
        assert original.sections == ()
        assert len(new_builder.sections) == 1

    def test_build_assembles_by_priority(self) -> None:
        result = (
            PromptBuilder()
            .add_section("low", "LOW", priority=10)
            .add_section("high", "HIGH", priority=100)
            .add_section("mid", "MID", priority=50)
            .build()
        )
        lines = result.split("\n\n")
        assert lines[0] == "HIGH"
        assert lines[1] == "MID"
        assert lines[2] == "LOW"

    def test_build_without_budget_includes_all(self) -> None:
        builder = PromptBuilder().add_section("a", "Content A", priority=100).add_section("b", "Content B", priority=50)
        result = builder.build()
        assert "Content A" in result
        assert "Content B" in result

    def test_build_with_budget_drops_lowest(self) -> None:
        builder = (
            PromptBuilder()
            .add_section("a", "Short", priority=100)
            .add_section("b", "Also short", priority=90)
            .add_section("c", "Very long content " * 500, priority=10)
        )
        # Budget too small for section c
        result = builder.build(max_tokens=50)
        assert "Short" in result
        assert "Also short" in result
        assert "Very long content" not in result

    def test_build_with_large_budget_keeps_all(self) -> None:
        builder = PromptBuilder().add_section("a", "A", priority=100).add_section("b", "B", priority=50)
        result = builder.build(max_tokens=10000)
        assert "A" in result
        assert "B" in result

    def test_frozen_dataclass(self) -> None:
        builder = PromptBuilder()
        with pytest.raises(AttributeError):
            builder.sections = ()  # type: ignore[misc]


class TestGetProtocolForTask:
    """Test get_protocol_for_task() returns correct protocols."""

    def test_bugfix_protocol(self) -> None:
        proto = get_protocol_for_task("fix login bug")
        assert "REPRODUCE" in proto
        assert "Bug Fix" in proto

    def test_feature_protocol(self) -> None:
        proto = get_protocol_for_task("add payment integration")
        assert "ORIENT" in proto
        assert "Feature Implementation" in proto

    def test_refactor_protocol(self) -> None:
        proto = get_protocol_for_task("refactor queries")
        assert "BASELINE" in proto
        assert "Refactoring" in proto

    def test_test_protocol(self) -> None:
        proto = get_protocol_for_task("add test coverage")
        assert "SURVEY" in proto
        assert "Test Writing" in proto

    def test_docs_protocol(self) -> None:
        proto = get_protocol_for_task("update README")
        assert "Documentation" in proto

    def test_all_types_have_protocols(self) -> None:
        for task_type in TaskType:
            # All TaskTypes should have an entry in _PROTOCOLS
            from open_orchestrator.core.prompt_builder import _PROTOCOLS

            assert task_type in _PROTOCOLS, f"Missing protocol for {task_type}"
