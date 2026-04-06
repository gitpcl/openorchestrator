"""Registry-based AI tool discovery.

Provides a singleton registry that replaces hardcoded tool lookups.
Built-in tools (claude, opencode, droid) are registered at import time.
Custom tools from config are registered via register_custom_tools().
"""

from __future__ import annotations

import logging
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from open_orchestrator.core.tool_protocol import AIToolProtocol

logger = logging.getLogger(__name__)


@dataclass
class CustomTool:
    """A custom AI tool declared in config TOML."""

    name: str
    binary: str
    command_template: str = "{binary}"
    prompt_flag: str | None = None
    known_paths: list[str] = field(default_factory=list)
    supports_hooks: bool = False
    install_hint: str = ""

    def get_command(
        self,
        *,
        executable_path: str | None = None,
        plan_mode: bool = False,
        prompt: str | None = None,
    ) -> str:
        binary = shlex.quote(executable_path) if executable_path else self.binary
        cmd = self.command_template.format(binary=binary)
        if prompt and self.prompt_flag:
            cmd += f" {self.prompt_flag} {shlex.quote(prompt)}"
        return cmd

    def is_installed(self) -> bool:
        if shutil.which(self.binary):
            return True
        return any(Path(p).expanduser().exists() for p in self.known_paths)

    def get_known_paths(self) -> list[Path]:
        return [Path(p).expanduser() for p in self.known_paths]


class ToolRegistry:
    """Singleton registry for AI coding tools."""

    def __init__(self) -> None:
        self._tools: dict[str, AIToolProtocol] = {}

    def register(self, tool: AIToolProtocol) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> AIToolProtocol | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(self._tools)

    def list_installed(self) -> list[AIToolProtocol]:
        """Return all tools that are currently installed."""
        return [t for t in self._tools.values() if t.is_installed()]

    def supports_hooks(self, name: str) -> bool:
        """Check if a tool supports OWT status hooks."""
        tool = self._tools.get(name)
        return tool.supports_hooks if tool else False

    def list_all(self) -> list[AIToolProtocol]:
        """Return all registered tools."""
        return list(self._tools.values())


def _register_builtins(registry: ToolRegistry) -> None:
    """Register built-in tools (claude, opencode, droid) as CustomTool instances."""
    registry.register(
        CustomTool(
            name="claude",
            binary="claude",
            command_template="{binary} --dangerously-skip-permissions",
            prompt_flag="-p",
            known_paths=["~/.claude/local/claude"],
            supports_hooks=True,
            install_hint="Install Claude Code: npm install -g @anthropic-ai/claude-code",
        )
    )
    registry.register(
        CustomTool(
            name="opencode",
            binary="opencode",
            command_template="{binary}",
            known_paths=["~/go/bin/opencode", "~/.local/bin/opencode"],
            supports_hooks=False,
            install_hint="Install OpenCode: go install github.com/opencode-ai/opencode@latest",
        )
    )
    registry.register(
        CustomTool(
            name="droid",
            binary="droid",
            command_template="{binary} --skip-permissions-unsafe",
            known_paths=["~/.local/bin/droid", "/usr/local/bin/droid"],
            supports_hooks=True,
            install_hint="Install Droid: See https://docs.factory.ai/cli",
        )
    )


def register_custom_tools(registry: ToolRegistry, tools_config: dict[str, dict[str, object]]) -> None:
    """Register custom tools from [tools.<name>] config sections."""
    for name, cfg in tools_config.items():
        if name in ("claude", "opencode", "droid"):
            logger.warning("Cannot override built-in tool '%s' via config", name)
            continue
        tool = CustomTool(
            name=name,
            binary=str(cfg.get("binary", name)),
            command_template=str(cfg.get("command_template", "{binary}")),
            prompt_flag=str(cfg["prompt_flag"]) if cfg.get("prompt_flag") else None,
            known_paths=[str(p) for p in cfg.get("known_paths", [])],  # type: ignore[attr-defined]
            supports_hooks=bool(cfg.get("supports_hooks", False)),
            install_hint=str(cfg.get("install_hint", "")),
        )
        registry.register(tool)
        logger.info("Registered custom AI tool: %s (%s)", name, tool.binary)


# Module-level singleton
_registry = ToolRegistry()
_register_builtins(_registry)


def get_registry() -> ToolRegistry:
    """Get the global tool registry singleton."""
    return _registry
