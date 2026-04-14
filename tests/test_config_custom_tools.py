"""Tests that custom tools declared in config are usable in the same file.

Regression guard: the pydantic field_validators on ``WorktreeTemplate.ai_tool``
and ``TmuxConfig.ai_tool`` query the tool registry at parse time. If custom
tools are registered AFTER ``Config(**data)`` runs, a config that both
declares ``[tools.mytool]`` and references it via ``ai_tool = "mytool"`` will
fail validation. ``load_config`` must register custom tools BEFORE building
the Config.
"""

from __future__ import annotations

from pathlib import Path

from open_orchestrator.config import ConfigError, load_config
from open_orchestrator.core.tool_registry import _register_builtins, get_registry


def _reset_registry() -> None:
    """Restore the global registry to builtins-only state.

    Custom tools registered by other tests leak into the module-level singleton;
    reset between tests that care about what's present.
    """
    registry = get_registry()
    registry._tools.clear()  # type: ignore[attr-defined]
    _register_builtins(registry)


def test_custom_tool_usable_in_same_config(tmp_path: Path) -> None:
    """A [tools.X] section and tmux.ai_tool = "X" in the same file must validate."""
    _reset_registry()
    cfg = tmp_path / ".worktreerc"
    cfg.write_text(
        """
[tmux]
ai_tool = "mytool"

[tools.mytool]
binary = "my-binary"
command_template = "{binary} --go"
"""
    )
    # Should not raise
    config = load_config(str(cfg))
    assert config.tmux.ai_tool == "mytool"
    assert get_registry().get("mytool") is not None


def test_custom_tool_usable_in_template(tmp_path: Path) -> None:
    """Custom tools must be selectable from a template's ai_tool field too."""
    _reset_registry()
    cfg = tmp_path / ".worktreerc"
    cfg.write_text(
        """
[tools.aider2]
binary = "aider"

[templates.custom]
name = "custom"
description = "Use aider"
ai_tool = "aider2"
"""
    )
    config = load_config(str(cfg))
    tmpl = config.templates["custom"]
    assert tmpl.ai_tool == "aider2"


def test_unknown_tool_still_rejected(tmp_path: Path) -> None:
    """With no matching [tools.X], the registry check still rejects."""
    _reset_registry()
    cfg = tmp_path / ".worktreerc"
    cfg.write_text(
        """
[tmux]
ai_tool = "nonexistent-tool"
"""
    )
    import pytest

    with pytest.raises(ConfigError, match="Unknown AI tool"):
        load_config(str(cfg))


def test_load_config_preserves_builtins(tmp_path: Path) -> None:
    """Loading a config with custom tools does not clobber built-in tool entries."""
    # Use an isolated registry to avoid bleed-through from other tests.
    _reset_registry()
    cfg = tmp_path / ".worktreerc"
    cfg.write_text(
        """
[tools.mytool]
binary = "my-binary"
"""
    )
    load_config(str(cfg))

    registry = get_registry()
    assert registry.get("claude") is not None
    assert registry.get("mytool") is not None
