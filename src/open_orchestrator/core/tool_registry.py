"""Registry-based AI tool discovery.

Provides a singleton registry that replaces hardcoded tool lookups.
Built-in tools (claude, opencode, droid) and detectable extras (codex,
gemini-cli, aider, amp, kilo-code) are registered at import time. Custom
tools from config are registered via ``register_custom_tools()``.
"""

from __future__ import annotations

import logging
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from open_orchestrator.core.tool_protocol import AIToolProtocol

logger = logging.getLogger(__name__)


def _resolve_binary(binary: str, known_paths: list[Path]) -> str | None:
    """Return the resolved executable path, or None if not installed."""
    path = shutil.which(binary)
    if path:
        return path
    for candidate in known_paths:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


@dataclass
class CustomTool:
    """A user-declared AI tool from ``[tools.<name>]`` config.

    Built-in tools (claude, droid, opencode) have their own classes below
    because they need tool-specific command-building logic (plan mode, hooks,
    prompt delivery).
    """

    name: str
    binary: str
    command_template: str = "{binary}"
    prompt_flag: str | None = None
    known_paths: list[str] = field(default_factory=list)
    supports_hooks: bool = False
    supports_headless: bool = False
    supports_plan_mode: bool = False
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
        return _resolve_binary(self.binary, self.get_known_paths()) is not None

    def get_known_paths(self) -> list[Path]:
        return [Path(p).expanduser() for p in self.known_paths]

    def install_hooks(
        self,
        worktree_path: Path,
        worktree_name: str,
        db_path: str | Path | None = None,
    ) -> bool:
        """Custom tools have no built-in hook integration."""
        return False


class ClaudeTool:
    """Built-in Claude Code tool with plan-mode and hook support."""

    name = "claude"
    binary = "claude"
    supports_hooks = True
    supports_headless = True
    supports_plan_mode = True
    install_hint = "Install Claude Code: npm install -g @anthropic-ai/claude-code"

    def get_command(
        self,
        *,
        executable_path: str | None = None,
        plan_mode: bool = False,
        prompt: str | None = None,
    ) -> str:
        binary = shlex.quote(executable_path) if executable_path else self.binary
        parts = [binary]
        if plan_mode:
            parts.append("--permission-mode plan")
        else:
            parts.append("--dangerously-skip-permissions")
        if prompt:
            # -p (print mode) — caller pipes the prompt via stdin from a
            # temp file to avoid tmux send-keys buffer truncation.
            parts.append("-p")
        return " ".join(parts)

    def is_installed(self) -> bool:
        return _resolve_binary(self.binary, self.get_known_paths()) is not None

    def get_known_paths(self) -> list[Path]:
        return [Path.home() / ".claude" / "local" / "claude"]

    def install_hooks(
        self,
        worktree_path: Path,
        worktree_name: str,
        db_path: str | Path | None = None,
    ) -> bool:
        from open_orchestrator.core.hooks import install_claude_hooks

        return install_claude_hooks(worktree_path, worktree_name, db_path=db_path)


class DroidTool:
    """Built-in Droid (Factory) tool with hook support."""

    name = "droid"
    binary = "droid"
    supports_hooks = True
    supports_headless = False
    supports_plan_mode = False
    install_hint = "Install Droid: See https://docs.factory.ai/cli"

    def get_command(
        self,
        *,
        executable_path: str | None = None,
        plan_mode: bool = False,
        prompt: str | None = None,
    ) -> str:
        binary = shlex.quote(executable_path) if executable_path else self.binary
        return f"{binary} --skip-permissions-unsafe"

    def is_installed(self) -> bool:
        return _resolve_binary(self.binary, self.get_known_paths()) is not None

    def get_known_paths(self) -> list[Path]:
        return [Path.home() / ".local" / "bin" / "droid", Path("/usr/local/bin/droid")]

    def install_hooks(
        self,
        worktree_path: Path,
        worktree_name: str,
        db_path: str | Path | None = None,
    ) -> bool:
        from open_orchestrator.core.hooks import install_droid_hooks

        return install_droid_hooks(worktree_path, worktree_name, db_path=db_path)


class OpenCodeTool:
    """Built-in OpenCode tool (no hook integration)."""

    name = "opencode"
    binary = "opencode"
    supports_hooks = False
    supports_headless = False
    supports_plan_mode = False
    install_hint = "Install OpenCode: go install github.com/opencode-ai/opencode@latest"

    def get_command(
        self,
        *,
        executable_path: str | None = None,
        plan_mode: bool = False,
        prompt: str | None = None,
    ) -> str:
        return shlex.quote(executable_path) if executable_path else self.binary

    def is_installed(self) -> bool:
        return _resolve_binary(self.binary, self.get_known_paths()) is not None

    def get_known_paths(self) -> list[Path]:
        return [
            Path.home() / "go" / "bin" / "opencode",
            Path.home() / ".local" / "bin" / "opencode",
        ]

    def install_hooks(
        self,
        worktree_path: Path,
        worktree_name: str,
        db_path: str | Path | None = None,
    ) -> bool:
        return False


# Additional AI tools detectable on the system but without hook or
# headless integration. Users pick them via --ai-tool <name>; they start
# and status falls back to pane scraping.
_EXTRA_BINARIES: dict[str, str] = {
    "codex": "codex",
    "gemini-cli": "gemini",
    "aider": "aider",
    "amp": "amp",
    "kilo-code": "kilo-code",
}


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

    def require(self, name: str) -> AIToolProtocol:
        """Look up a tool by name, raising if not registered."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown AI tool '{name}'. Registered: {self.list_names()}")
        return tool

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
    """Register built-in tools and detectable extras."""
    registry.register(ClaudeTool())
    registry.register(DroidTool())
    registry.register(OpenCodeTool())
    for name, binary in _EXTRA_BINARIES.items():
        registry.register(
            CustomTool(
                name=name,
                binary=binary,
                install_hint=f"Install {name} manually.",
            )
        )


def register_custom_tools(registry: ToolRegistry, tools_config: dict[str, dict[str, object]]) -> None:
    """Register custom tools from ``[tools.<name>]`` config sections."""
    reserved = {"claude", "opencode", "droid"}
    for name, cfg in tools_config.items():
        if name in reserved:
            logger.warning("Cannot override built-in tool '%s' via config", name)
            continue
        tool = CustomTool(
            name=name,
            binary=str(cfg.get("binary", name)),
            command_template=str(cfg.get("command_template", "{binary}")),
            prompt_flag=str(cfg["prompt_flag"]) if cfg.get("prompt_flag") else None,
            known_paths=[str(p) for p in cfg.get("known_paths") or []],  # type: ignore[attr-defined]
            supports_hooks=bool(cfg.get("supports_hooks", False)),
            supports_headless=bool(cfg.get("supports_headless", False)),
            supports_plan_mode=bool(cfg.get("supports_plan_mode", False)),
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
