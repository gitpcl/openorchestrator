"""Tests for AI tool protocol, CustomTool, and ToolRegistry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from open_orchestrator.core.tool_protocol import AIToolProtocol
from open_orchestrator.core.tool_registry import (
    CustomTool,
    ToolRegistry,
    _register_builtins,
    get_registry,
    register_custom_tools,
)

# ---------------------------------------------------------------------------
# AIToolProtocol
# ---------------------------------------------------------------------------


class TestAIToolProtocol:
    """Test the Protocol interface and runtime_checkable behavior."""

    def test_custom_tool_satisfies_protocol(self) -> None:
        """CustomTool should satisfy the AIToolProtocol at runtime."""
        tool = CustomTool(name="test", binary="test-bin")
        assert isinstance(tool, AIToolProtocol)

    def test_non_conforming_object_fails_protocol(self) -> None:
        """A plain object should not satisfy AIToolProtocol."""
        assert not isinstance(object(), AIToolProtocol)


# ---------------------------------------------------------------------------
# CustomTool
# ---------------------------------------------------------------------------


class TestCustomTool:
    """Test CustomTool dataclass and methods."""

    def test_get_command_basic(self) -> None:
        tool = CustomTool(name="aider", binary="aider", command_template="{binary} --yes")
        assert tool.get_command() == "aider --yes"

    def test_get_command_with_executable_path(self) -> None:
        tool = CustomTool(name="aider", binary="aider", command_template="{binary} --yes")
        cmd = tool.get_command(executable_path="/usr/local/bin/aider")
        assert "/usr/local/bin/aider" in cmd
        assert "--yes" in cmd

    def test_get_command_with_prompt(self) -> None:
        tool = CustomTool(
            name="claude",
            binary="claude",
            command_template="{binary}",
            prompt_flag="-p",
        )
        cmd = tool.get_command(prompt="Fix the bug")
        assert "-p" in cmd
        assert "Fix the bug" in cmd

    def test_get_command_no_prompt_flag_ignores_prompt(self) -> None:
        tool = CustomTool(name="opencode", binary="opencode", command_template="{binary}")
        cmd = tool.get_command(prompt="Fix the bug")
        assert cmd == "opencode"
        assert "Fix the bug" not in cmd

    @patch("shutil.which", return_value="/usr/bin/test-tool")
    def test_is_installed_found_in_path(self, _mock_which: object) -> None:
        tool = CustomTool(name="test", binary="test-tool")
        assert tool.is_installed() is True

    @patch("shutil.which", return_value=None)
    def test_is_installed_not_in_path_or_known(self, _mock_which: object) -> None:
        tool = CustomTool(name="test", binary="test-tool")
        assert tool.is_installed() is False

    @patch("shutil.which", return_value=None)
    def test_is_installed_found_in_known_paths(self, _mock_which: object, tmp_path: Path) -> None:
        known = tmp_path / "test-tool"
        known.touch()
        tool = CustomTool(name="test", binary="test-tool", known_paths=[str(known)])
        assert tool.is_installed() is True

    def test_get_known_paths_expands_tilde(self) -> None:
        tool = CustomTool(name="test", binary="test", known_paths=["~/bin/test"])
        paths = tool.get_known_paths()
        assert len(paths) == 1
        assert "~" not in str(paths[0])

    def test_default_supports_hooks_is_false(self) -> None:
        tool = CustomTool(name="test", binary="test")
        assert tool.supports_hooks is False

    def test_supports_hooks_configurable(self) -> None:
        tool = CustomTool(name="test", binary="test", supports_hooks=True)
        assert tool.supports_hooks is True

    def test_default_supports_headless_is_false(self) -> None:
        tool = CustomTool(name="test", binary="test")
        assert tool.supports_headless is False

    def test_default_supports_plan_mode_is_false(self) -> None:
        tool = CustomTool(name="test", binary="test")
        assert tool.supports_plan_mode is False

    def test_capability_flags_configurable(self) -> None:
        tool = CustomTool(
            name="test",
            binary="test",
            supports_hooks=True,
            supports_headless=True,
            supports_plan_mode=True,
        )
        assert tool.supports_hooks is True
        assert tool.supports_headless is True
        assert tool.supports_plan_mode is True

    def test_custom_tool_install_hooks_returns_false(self, tmp_path: Path) -> None:
        """Custom tools never install built-in hooks."""
        tool = CustomTool(name="test", binary="test", supports_hooks=True)
        assert tool.install_hooks(tmp_path, "name") is False


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    """Test ToolRegistry CRUD and query operations."""

    def test_register_and_get(self) -> None:
        reg = ToolRegistry()
        tool = CustomTool(name="myTool", binary="my-tool")
        reg.register(tool)
        assert reg.get("myTool") is tool

    def test_get_missing_returns_none(self) -> None:
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_list_names_sorted(self) -> None:
        reg = ToolRegistry()
        reg.register(CustomTool(name="zed", binary="zed"))
        reg.register(CustomTool(name="alpha", binary="alpha"))
        reg.register(CustomTool(name="mid", binary="mid"))
        assert reg.list_names() == ["alpha", "mid", "zed"]

    @patch("shutil.which")
    def test_list_installed_filters(self, mock_which: object) -> None:
        reg = ToolRegistry()
        mock_which.side_effect = lambda b: "/usr/bin/found" if b == "found-tool" else None  # type: ignore[assignment]
        reg.register(CustomTool(name="found", binary="found-tool"))
        reg.register(CustomTool(name="missing", binary="missing-tool"))
        installed = reg.list_installed()
        assert len(installed) == 1
        assert installed[0].name == "found"

    def test_supports_hooks_true(self) -> None:
        reg = ToolRegistry()
        reg.register(CustomTool(name="hook-tool", binary="ht", supports_hooks=True))
        assert reg.supports_hooks("hook-tool") is True

    def test_supports_hooks_false(self) -> None:
        reg = ToolRegistry()
        reg.register(CustomTool(name="no-hook", binary="nh", supports_hooks=False))
        assert reg.supports_hooks("no-hook") is False

    def test_supports_hooks_missing_tool(self) -> None:
        reg = ToolRegistry()
        assert reg.supports_hooks("nonexistent") is False

    def test_register_overwrites(self) -> None:
        reg = ToolRegistry()
        tool1 = CustomTool(name="t", binary="old")
        tool2 = CustomTool(name="t", binary="new")
        reg.register(tool1)
        reg.register(tool2)
        assert reg.get("t") is tool2
        assert reg.get("t").binary == "new"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Built-in registration
# ---------------------------------------------------------------------------


class TestBuiltinRegistration:
    """Test that built-in tools are properly registered."""

    def test_register_builtins_adds_three_tools(self) -> None:
        reg = ToolRegistry()
        _register_builtins(reg)
        names = reg.list_names()
        assert "claude" in names
        assert "opencode" in names
        assert "droid" in names

    def test_claude_supports_hooks(self) -> None:
        reg = ToolRegistry()
        _register_builtins(reg)
        assert reg.supports_hooks("claude") is True

    def test_opencode_no_hooks(self) -> None:
        reg = ToolRegistry()
        _register_builtins(reg)
        assert reg.supports_hooks("opencode") is False

    def test_global_registry_has_builtins(self) -> None:
        """Module-level singleton should have builtins pre-registered."""
        reg = get_registry()
        assert "claude" in reg.list_names()

    def test_claude_supports_headless_and_plan_mode(self) -> None:
        reg = ToolRegistry()
        _register_builtins(reg)
        tool = reg.require("claude")
        assert tool.supports_headless is True
        assert tool.supports_plan_mode is True

    def test_droid_does_not_support_headless(self) -> None:
        reg = ToolRegistry()
        _register_builtins(reg)
        tool = reg.require("droid")
        assert tool.supports_headless is False

    def test_extras_registered(self) -> None:
        """Extra tools (codex, aider, etc.) should be registered."""
        reg = ToolRegistry()
        _register_builtins(reg)
        for extra in ("codex", "gemini-cli", "aider", "amp", "kilo-code"):
            assert reg.get(extra) is not None, f"{extra} not registered"

    def test_require_raises_on_missing(self) -> None:
        reg = ToolRegistry()
        import pytest as _pytest

        with _pytest.raises(KeyError):
            reg.require("nonexistent")

    def test_claude_plan_mode_command(self) -> None:
        reg = ToolRegistry()
        _register_builtins(reg)
        tool = reg.require("claude")
        cmd = tool.get_command(plan_mode=True, prompt="plan this")
        assert "--permission-mode plan" in cmd
        assert "-p" in cmd


# ---------------------------------------------------------------------------
# Custom tool registration from config
# ---------------------------------------------------------------------------


class TestRegisterCustomTools:
    """Test custom tool registration from TOML config."""

    def test_register_custom_tool(self) -> None:
        reg = ToolRegistry()
        config: dict[str, dict[str, object]] = {
            "aider": {
                "binary": "aider",
                "command_template": "{binary} --yes",
                "supports_hooks": False,
            }
        }
        register_custom_tools(reg, config)
        tool = reg.get("aider")
        assert tool is not None
        assert tool.name == "aider"

    def test_cannot_override_builtin(self) -> None:
        reg = ToolRegistry()
        _register_builtins(reg)
        original = reg.get("claude")
        config: dict[str, dict[str, object]] = {
            "claude": {"binary": "evil-claude"},
        }
        register_custom_tools(reg, config)
        assert reg.get("claude") is original

    def test_custom_tool_with_prompt_flag(self) -> None:
        reg = ToolRegistry()
        config: dict[str, dict[str, object]] = {
            "mytool": {
                "binary": "mytool",
                "prompt_flag": "--ask",
            }
        }
        register_custom_tools(reg, config)
        tool = reg.get("mytool")
        assert tool is not None
        cmd = tool.get_command(prompt="hello")
        assert "--ask" in cmd

    def test_empty_config_is_noop(self) -> None:
        reg = ToolRegistry()
        register_custom_tools(reg, {})
        assert reg.list_names() == []
