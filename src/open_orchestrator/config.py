"""
Configuration management for open-orchestrator.

Loads configuration from .worktreerc files in the following priority:
1. Path specified via --config flag
2. .worktreerc in current directory
3. .worktreerc.toml in current directory
4. ~/.config/open-orchestrator/config.toml
5. ~/.worktreerc
"""

import logging
import shlex
import shutil
from enum import Enum
from pathlib import Path

import toml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# Accent color for status bar and borders.
# Canonical value lives in open_orchestrator.core.theme.COLORS["accent"].
# Duplicated here to avoid circular imports (config <- core.* <- config).
ACCENT_COLOR = "#00d7d7"


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
    def get_known_paths(cls, tool: "AITool") -> list[Path]:
        """Get known installation paths for an AI tool."""
        home = Path.home()
        paths: dict[AITool, list[Path]] = {
            cls.CLAUDE: [
                home / ".claude" / "local" / "claude",
            ],
            cls.OPENCODE: [
                home / "go" / "bin" / "opencode",
                home / ".local" / "bin" / "opencode",
            ],
            cls.DROID: [
                home / ".local" / "bin" / "droid",
                Path("/usr/local/bin/droid"),
            ],
        }
        return paths.get(tool, [])

    @classmethod
    def get_command(
        cls,
        tool: "AITool",
        executable_path: str | None = None,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
        plan_mode: bool = False,
    ) -> str:
        """Get the shell command for an AI tool with options."""
        binary = shlex.quote(executable_path) if executable_path else tool.value

        if tool == cls.CLAUDE:
            cmd_parts = [binary]
            if plan_mode:
                cmd_parts.append("--permission-mode plan")
            else:
                cmd_parts.append("--dangerously-skip-permissions")
            return " ".join(cmd_parts)

        if tool == cls.DROID:
            cmd_parts = [binary]
            if droid_auto:
                cmd_parts.append(f"--auto {shlex.quote(droid_auto.value)}")
            cmd_parts.append("--skip-permissions-unsafe")
            return " ".join(cmd_parts)

        if tool == cls.OPENCODE and opencode_config:
            quoted = shlex.quote(opencode_config)
            return f"OPENCODE_CONFIG={quoted} {binary}"

        return binary

    @classmethod
    def is_installed(cls, tool: "AITool") -> bool:
        """Check if an AI tool is installed on the system."""
        binary = cls.get_binary_name(tool)
        if shutil.which(binary) is not None:
            return True
        for path in cls.get_known_paths(tool):
            if path.exists() and path.is_file():
                return True
        return False

    @classmethod
    def get_executable_path(cls, tool: "AITool") -> str | None:
        """Get the actual executable path for an AI tool."""
        binary = cls.get_binary_name(tool)
        path_binary = shutil.which(binary)
        if path_binary:
            return path_binary
        for path in cls.get_known_paths(tool):
            if path.exists() and path.is_file():
                return str(path)
        return None

    @classmethod
    def get_install_hint(cls, tool: "AITool") -> str:
        """Get installation hint for an AI tool."""
        hints = {
            cls.CLAUDE: "Install Claude Code: npm install -g @anthropic-ai/claude-code",
            cls.OPENCODE: "Install OpenCode: go install github.com/opencode-ai/opencode@latest",
            cls.DROID: "Install Droid: See https://docs.factory.ai/cli",
        }
        return hints.get(tool, f"Please install {tool.value} manually")


class AgnoConfig(BaseModel):
    """Agno intelligence layer configuration."""

    enabled: bool = Field(default=True, description="Enable Agno intelligence features")
    model_id: str = Field(default="claude-sonnet-4-20250514", description="Default model ID for Agno agents")
    planner_model_id: str | None = Field(default=None, description="Override model for planner agent")
    quality_gate_model_id: str | None = Field(default=None, description="Override model for quality gate agent")
    max_tokens: int = Field(default=4096, description="Max tokens for Agno model responses")
    temperature: float = Field(default=0.2, description="Temperature for Agno model responses")
    quality_gate_threshold: float = Field(default=0.7, description="Minimum score to pass quality gate")
    auto_resolve_conflicts: bool = Field(default=False, description="Automatically apply AI conflict resolutions")
    coordinator_model_id: str | None = Field(default=None, description="Override model for coordinator agent")
    memory_enabled: bool = Field(default=True, description="Enable persistent memory for Agno agents")
    memory_db_path: str | None = Field(default=None, description="Custom path for memory DB")


class ClaudeConfig(BaseModel):
    """Claude-specific configuration."""

    pass


class OpenCodeConfig(BaseModel):
    """OpenCode-specific configuration."""

    config_path: str | None = Field(default=None, description="Path to OpenCode configuration file")


class DroidConfig(BaseModel):
    """Droid-specific configuration."""

    default_auto_level: DroidAutoLevel | None = Field(default=None, description="Default auto mode level")
    skip_permissions_unsafe: bool = Field(default=False, description="Skip permissions check")


class WorktreeTemplate(BaseModel):
    """Template configuration for common worktree workflows."""

    name: str = Field(..., description="Template name")
    description: str = Field(..., description="Template description")
    base_branch: str | None = Field(default=None, description="Default base branch")
    ai_tool: AITool | None = Field(default=None, description="AI tool to use")
    ai_instructions: str | None = Field(default=None, description="Instructions to send to AI")
    plan_mode: bool = Field(default=False, description="Start Claude in plan mode")
    install_deps: bool | None = Field(default=None, description="Override auto_install_deps")
    tmux_layout: str | None = Field(default=None, description="Default tmux layout")
    auto_commands: list[str] = Field(default_factory=list, description="Auto-run commands")
    tags: list[str] = Field(default_factory=list, description="Tags for categorizing templates")


class WorktreeConfig(BaseModel):
    """Configuration for worktree operations."""

    base_directory: str = Field(default="../", description="Base directory for creating worktrees")
    naming_pattern: str = Field(default="{project}-{branch}", description="Pattern for worktree directory names")
    auto_cleanup_days: int = Field(default=14, description="Days of inactivity before worktree is considered stale")


class TmuxConfig(BaseModel):
    """Configuration for tmux session management."""

    default_layout: str = Field(default="single", description="Default tmux pane layout")
    auto_start_ai: bool = Field(default=True, description="Automatically start AI tool in new sessions")
    ai_tool: AITool = Field(default=AITool.CLAUDE, description="AI coding tool to start")
    session_prefix: str = Field(default="owt", description="Prefix for tmux session names")
    mouse_mode: bool = Field(default=True, description="Enable mouse support")
    prefix_key: str = Field(default="C-a", description="tmux prefix key for owt sessions")


class EnvironmentConfig(BaseModel):
    """Configuration for environment setup."""

    auto_install_deps: bool = Field(default=True, description="Automatically install dependencies")
    copy_env_file: bool = Field(default=True, description="Copy .env file from main repo")
    adjust_env_paths: bool = Field(default=True, description="Adjust paths in .env file")
    additional_config_files: list[str] = Field(
        default_factory=lambda: [".env.local", ".env.development"],
        description="Additional config files to copy",
    )


class SyncConfig(BaseModel):
    """Configuration for sync operations."""

    default_strategy: str = Field(default="merge", description="Default git pull strategy")
    auto_stash: bool = Field(default=True, description="Automatically stash changes before sync")
    prune_remote: bool = Field(default=True, description="Prune remote tracking branches on fetch")


class Config(BaseModel):
    """Main configuration model for open-orchestrator."""

    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    agno: AgnoConfig = Field(default_factory=AgnoConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    droid: DroidConfig = Field(default_factory=DroidConfig)
    templates: dict[str, WorktreeTemplate] = Field(default_factory=dict, description="Custom worktree templates")

    def get_template(self, name: str) -> WorktreeTemplate | None:
        """Get a template by name, checking custom then built-in."""
        if name in self.templates:
            return self.templates[name]
        return get_builtin_template(name)


def get_builtin_templates() -> dict[str, WorktreeTemplate]:
    """Get built-in worktree templates."""
    return {
        "feature": WorktreeTemplate(
            name="feature",
            description="Full feature development - plan first, implement with tests",
            base_branch="develop",
            ai_tool=AITool.CLAUDE,
            ai_instructions=(
                "Follow this workflow: 1) Plan the implementation, 2) Write tests first (TDD), "
                "3) Implement feature, 4) Document as you go"
            ),
            plan_mode=True,
            tags=["development", "tdd"],
        ),
        "bugfix": WorktreeTemplate(
            name="bugfix",
            description="Quick bugfix workflow - identify root cause, minimal changes",
            base_branch="main",
            ai_tool=AITool.CLAUDE,
            ai_instructions=(
                "Focus on: 1) Identifying root cause, 2) Writing tests that reproduce the bug, 3) Minimal code changes to fix"
            ),
            tags=["quick", "maintenance"],
        ),
        "hotfix": WorktreeTemplate(
            name="hotfix",
            description="Emergency production fix - fast, focused, minimal risk",
            base_branch="main",
            ai_tool=AITool.CLAUDE,
            ai_instructions="HOTFIX MODE: 1) Minimal changes only, 2) Must include test, 3) Focus on production stability",
            tags=["urgent", "production"],
        ),
    }


def get_builtin_template(name: str) -> WorktreeTemplate | None:
    """Get a built-in template by name."""
    return get_builtin_templates().get(name)


def load_config(config_path: str | None = None) -> Config:
    """Load configuration from file or use defaults."""
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
            except Exception as e:
                logger.warning("Failed to load config from %s: %s", path, e)
                continue

    return Config()


def save_config(config: Config, path: Path) -> None:
    """Save configuration to a TOML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        toml.dump(config.model_dump(mode="json"), f)


def get_default_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.cwd() / ".worktreerc"
