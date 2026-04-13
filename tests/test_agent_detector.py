"""Tests for AI agent detection."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

from open_orchestrator.core.agent_detector import detect_all_agents, detect_installed_agents
from open_orchestrator.core.tool_registry import CustomTool, get_registry


def _patch_all_builtins(is_installed_fn):
    """Patch is_installed on every registered built-in tool class."""
    stack = ExitStack()
    seen: set[type] = set()
    for tool in get_registry().list_all():
        cls = type(tool)
        if cls in seen or cls is CustomTool:
            continue
        seen.add(cls)
        stack.enter_context(patch.object(cls, "is_installed", is_installed_fn))
    return stack


class TestDetectInstalledAgents:
    def test_returns_list(self):
        result = detect_installed_agents()
        assert isinstance(result, list)

    def test_detects_claude_when_installed(self):
        def is_installed(self) -> bool:
            return self.name == "claude"

        with _patch_all_builtins(is_installed), patch.object(CustomTool, "is_installed", lambda self: False):
            result = detect_installed_agents()
            assert "claude" in result
            assert "opencode" not in result

    def test_returns_empty_when_none_installed(self):
        with _patch_all_builtins(lambda self: False), patch.object(CustomTool, "is_installed", lambda self: False):
            result = detect_installed_agents()
            assert result == []


class TestDetectAllAgents:
    def test_includes_claude_when_installed(self):
        def is_installed(self) -> bool:
            return self.name == "claude"

        with _patch_all_builtins(is_installed), patch.object(CustomTool, "is_installed", lambda self: False):
            result = detect_all_agents()
            assert "claude" in result

    def test_includes_extra_tools(self):
        def is_installed(self) -> bool:
            return self.name == "aider"

        with _patch_all_builtins(lambda self: False), patch.object(CustomTool, "is_installed", is_installed):
            result = detect_all_agents()
            assert "aider" in result

    def test_returns_empty_when_nothing_found(self):
        with _patch_all_builtins(lambda self: False), patch.object(CustomTool, "is_installed", lambda self: False):
            result = detect_all_agents()
            assert result == []
