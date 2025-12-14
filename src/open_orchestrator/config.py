"""
Configuration management for open-orchestrator.

Loads configuration from .worktreerc files in the following priority:
1. Path specified via --config flag
2. .worktreerc in current directory
3. .worktreerc.toml in current directory
4. ~/.config/open-orchestrator/config.toml
5. ~/.worktreerc
"""

import shutil
from enum import Enum
from pathlib import Path
from typing import Optional

import toml
from pydantic import BaseModel, Field


class DroidAutoLevel(str, Enum):
    """Droid auto mode levels for autonomy control."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AITool(str, Enum):
    """Supported AI coding tools."""

    CLAUDE = "claude"
    OPENCODE = "opencode"
    DROID = "droid"

    @classmethod
    def get_binary_name(cls, tool: "AITool") -> str:
        """Get the binary/executable name for an AI tool."""
        return tool.value

    @classmethod
    def get_command(
        cls,
        tool: "AITool",
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
    ) -> str:
        """
        Get the shell command for an AI tool with options.

        Args:
            tool: The AI tool to get command for
            droid_auto: Droid auto mode level (low, medium, high)
            droid_skip_permissions: Skip permissions check for Droid
            opencode_config: Custom config path for OpenCode

        Returns:
            Complete command string to execute
        """
        if tool == cls.DROID:
            cmd_parts = ["droid"]
            if droid_auto:
                cmd_parts.append(f"--auto {droid_auto.value}")
            if droid_skip_permissions:
                cmd_parts.append("--skip-permissions-unsafe")
            return " ".join(cmd_parts)

        if tool == cls.OPENCODE and opencode_config:
            return f"OPENCODE_CONFIG={opencode_config} opencode"

        return tool.value

    @classmethod
    def is_installed(cls, tool: "AITool") -> bool:
        """Check if an AI tool is installed on the system."""
        binary = cls.get_binary_name(tool)
        return shutil.which(binary) is not None

    @classmethod
    def get_install_hint(cls, tool: "AITool") -> str:
        """Get installation hint for an AI tool."""
        hints = {
            cls.CLAUDE: "Install Claude Code: npm install -g @anthropic-ai/claude-code",
            cls.OPENCODE: "Install OpenCode: go install github.com/opencode-ai/opencode@latest",
            cls.DROID: "Install Droid: See https://docs.factory.ai/cli",
        }
        return hints.get(tool, f"Please install {tool.value} manually")


class ClaudeConfig(BaseModel):
    """Claude-specific configuration."""

    pass  # No special config needed yet


class OpenCodeConfig(BaseModel):
    """OpenCode-specific configuration."""

    config_path: str | None = Field(
        default=None,
        description="Path to OpenCode configuration file",
    )


class DroidConfig(BaseModel):
    """Droid-specific configuration."""

    default_auto_level: DroidAutoLevel | None = Field(
        default=None,
        description="Default auto mode level (low, medium, high)",
    )
    skip_permissions_unsafe: bool = Field(
        default=False,
        description="Skip permissions check (use with caution)",
    )


class WorktreeConfig(BaseModel):
    """Configuration for worktree operations."""

    base_directory: str = Field(
        default="../",
        description="Base directory for creating worktrees (relative to repo root)",
    )
    naming_pattern: str = Field(
        default="{project}-{branch}",
        description="Pattern for worktree directory names",
    )
    auto_cleanup_days: int = Field(
        default=14,
        description="Days of inactivity before worktree is considered stale",
    )


class TmuxConfig(BaseModel):
    """Configuration for tmux session management."""

    default_layout: str = Field(
        default="main-vertical",
        description="Default tmux pane layout",
    )
    auto_start_ai: bool = Field(
        default=True,
        description="Automatically start AI tool in new sessions",
    )
    ai_tool: AITool = Field(
        default=AITool.CLAUDE,
        description="AI coding tool to start (claude, opencode, droid)",
    )
    pane_count: int = Field(
        default=2,
        description="Default number of panes",
    )
    session_prefix: str = Field(
        default="owt",
        description="Prefix for tmux session names",
    )


class EnvironmentConfig(BaseModel):
    """Configuration for environment setup."""

    auto_install_deps: bool = Field(
        default=True,
        description="Automatically install dependencies in new worktrees",
    )
    copy_env_file: bool = Field(
        default=True,
        description="Copy .env file from main repo to worktrees",
    )
    adjust_env_paths: bool = Field(
        default=True,
        description="Adjust paths in .env file for worktree location",
    )
    additional_config_files: list[str] = Field(
        default_factory=lambda: [".env.local", ".env.development"],
        description="Additional config files to copy",
    )


class SyncConfig(BaseModel):
    """Configuration for sync operations."""

    default_strategy: str = Field(
        default="merge",
        description="Default git pull strategy (merge or rebase)",
    )
    auto_stash: bool = Field(
        default=True,
        description="Automatically stash changes before sync",
    )
    prune_remote: bool = Field(
        default=True,
        description="Prune remote tracking branches on fetch",
    )


class Config(BaseModel):
    """Main configuration model for open-orchestrator."""

    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    # AI tool-specific configurations
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    droid: DroidConfig = Field(default_factory=DroidConfig)


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from file or use defaults.

    Args:
        config_path: Optional explicit path to config file.

    Returns:
        Config instance with loaded or default values.
    """
    search_paths = [
        Path(config_path) if config_path else None,
        Path.cwd() / ".worktreerc",
        Path.cwd() / ".worktreerc.toml",
        Path.home() / ".config" / "open-orchestrator" / "config.toml",
        Path.home() / ".worktreerc",
    ]

    for path in search_paths:
        if path and path.exists():
            try:
                data = toml.load(path)
                return Config(**data)
            except Exception:
                # If config file is invalid, continue to next
                continue

    return Config()


def save_config(config: Config, path: Path) -> None:
    """
    Save configuration to a TOML file.

    Args:
        config: Configuration to save.
        path: Path to save the config file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        toml.dump(config.model_dump(), f)


def get_default_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.cwd() / ".worktreerc"
