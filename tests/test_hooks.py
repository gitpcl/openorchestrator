"""Tests for AI tool hook installation.

Verifies the JSON structure written to settings files matches
the contracts expected by Claude Code and Droid.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from open_orchestrator.core.hooks import install_hooks

_has_mcp: bool
try:
    import mcp  # noqa: F401

    _has_mcp = True
except ImportError:
    _has_mcp = False

requires_mcp = pytest.mark.skipif(not _has_mcp, reason="MCP SDK not installed")


class TestInstallClaudeHooks:
    def test_creates_settings_file(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        settings = tmp_path / ".claude" / "settings.local.json"
        assert settings.exists()

    def test_settings_is_valid_json(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        settings = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings.read_text())
        assert isinstance(data, dict)
        assert "hooks" in data

    def test_has_required_hook_events(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        hooks = data["hooks"]
        assert "UserPromptSubmit" in hooks
        assert "Stop" in hooks
        assert "Notification" in hooks

    def test_hook_structure_has_type_command(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        # UserPromptSubmit hook should have type: command
        submit_hook = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        assert submit_hook["type"] == "command"
        assert "owt hook" in submit_hook["command"]
        assert "--event working" in submit_hook["command"]
        assert "my-feature" in submit_hook["command"]

    def test_stop_hook_sends_waiting(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        stop_hook = data["hooks"]["Stop"][0]["hooks"][0]
        assert "--event waiting" in stop_hook["command"]

    def test_notification_hook_has_matcher(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        notif = data["hooks"]["Notification"][0]
        assert notif["matcher"] == "permission_prompt"
        assert "--event blocked" in notif["hooks"][0]["command"]

    def test_preserves_existing_settings(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = claude_dir / "settings.local.json"
        settings.write_text(json.dumps({"customKey": "preserved"}))

        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        data = json.loads(settings.read_text())
        assert data["customKey"] == "preserved"
        assert "hooks" in data

    def test_idempotent_reinstall(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")
            install_hooks(tmp_path, "my-feature", "claude")

        data = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
        # Should not duplicate notification hooks
        assert len(data["hooks"]["Notification"]) == 1


class TestInstallDroidHooks:
    def test_creates_factory_settings(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "droid")

        settings = tmp_path / ".factory" / "settings.json"
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert "hooks" in data
        assert "UserPromptSubmit" in data["hooks"]
        assert "Stop" in data["hooks"]
        assert "Notification" in data["hooks"]

    def test_droid_hook_references_owt(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "droid")

        data = json.loads((tmp_path / ".factory" / "settings.json").read_text())
        cmd = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        assert "/usr/bin/owt" in cmd


class TestUnsupportedTool:
    def test_opencode_returns_false(self, tmp_path: Path):
        result = install_hooks(tmp_path, "my-feature", "opencode")
        assert result is False

    def test_claude_returns_true(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            result = install_hooks(tmp_path, "my-feature", "claude")
        assert result is True


class TestMCPConfig:
    """Tests for MCP peer server config injection."""

    @requires_mcp
    def test_mcp_config_injected_with_hooks(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        assert "mcpServers" in data
        assert "owt-peers" in data["mcpServers"]

    @requires_mcp
    def test_mcp_config_has_correct_structure(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "auth-feature", "claude")

        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        mcp = data["mcpServers"]["owt-peers"]

        assert "command" in mcp
        assert mcp["args"] == ["-m", "open_orchestrator.core.mcp_peer"]
        assert mcp["env"]["OWT_WORKTREE_NAME"] == "auth-feature"
        assert "status.db" in mcp["env"]["OWT_DB_PATH"]

    @requires_mcp
    def test_mcp_config_preserves_existing_hooks(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")

        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        # Both hooks and mcpServers should coexist
        assert "hooks" in data
        assert "mcpServers" in data
        assert "UserPromptSubmit" in data["hooks"]

    def test_mcp_config_skipped_when_sdk_missing(self, tmp_path: Path):
        import sys

        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"), patch.dict(sys.modules, {"mcp": None}):
            install_hooks(tmp_path, "my-feature", "claude")

        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        assert "mcpServers" not in data
        # Hooks still installed
        assert "hooks" in data

    @requires_mcp
    def test_mcp_config_idempotent(self, tmp_path: Path):
        with patch("open_orchestrator.core.hooks._owt_path", return_value="/usr/bin/owt"):
            install_hooks(tmp_path, "my-feature", "claude")
            install_hooks(tmp_path, "my-feature", "claude")

        settings_path = tmp_path / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        # Should have exactly one owt-peers entry
        assert len(data["mcpServers"]) == 1
