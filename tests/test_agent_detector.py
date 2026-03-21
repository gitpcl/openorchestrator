"""Tests for AI agent detection."""

from __future__ import annotations

from unittest.mock import patch

from open_orchestrator.config import AITool
from open_orchestrator.core.agent_detector import detect_all_agents, detect_installed_agents


class TestDetectInstalledAgents:
    def test_returns_list(self):
        result = detect_installed_agents()
        assert isinstance(result, list)

    def test_detects_claude_when_installed(self):
        with patch.object(AITool, "is_installed", side_effect=lambda t: t == AITool.CLAUDE):
            result = detect_installed_agents()
            assert AITool.CLAUDE in result
            assert AITool.OPENCODE not in result

    def test_returns_empty_when_none_installed(self):
        with patch.object(AITool, "is_installed", return_value=False):
            result = detect_installed_agents()
            assert result == []

    def test_detects_multiple(self):
        with patch.object(AITool, "is_installed", return_value=True):
            result = detect_installed_agents()
            assert len(result) == len(list(AITool))


class TestDetectAllAgents:
    def test_includes_core_tools(self):
        with patch.object(AITool, "is_installed", side_effect=lambda t: t == AITool.CLAUDE):
            with patch("shutil.which", return_value=None):
                result = detect_all_agents()
                assert "claude" in result

    def test_includes_extra_tools(self):
        with patch.object(AITool, "is_installed", return_value=False):
            with patch("shutil.which", side_effect=lambda b: "/usr/bin/aider" if b == "aider" else None):
                result = detect_all_agents()
                assert "aider" in result

    def test_returns_empty_when_nothing_found(self):
        with patch.object(AITool, "is_installed", return_value=False):
            with patch("shutil.which", return_value=None):
                result = detect_all_agents()
                assert result == []
