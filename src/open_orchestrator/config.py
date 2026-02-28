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
        """
        Get the shell command for an AI tool with options.

        Args:
            tool: The AI tool to get command for
            executable_path: Full path to executable (used when not in PATH)
            droid_auto: Droid auto mode level (low, medium, high)
            droid_skip_permissions: Skip permissions check for Droid
            opencode_config: Custom config path for OpenCode
            plan_mode: Start Claude in plan mode (--permission-mode plan)

        Returns:
            Complete command string to execute
        """
        # Use full path if provided, otherwise use tool name
        binary = executable_path or tool.value

        if tool == cls.CLAUDE:
            cmd_parts = [binary]
            if plan_mode:
                cmd_parts.append("--permission-mode plan")
            return " ".join(cmd_parts)

        if tool == cls.DROID:
            cmd_parts = [binary]
            if droid_auto:
                cmd_parts.append(f"--auto {droid_auto.value}")
            if droid_skip_permissions:
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

        # Check PATH first
        if shutil.which(binary) is not None:
            return True

        # Check known installation paths
        for path in cls.get_known_paths(tool):
            if path.exists() and path.is_file():
                return True

        return False

    @classmethod
    def get_executable_path(cls, tool: "AITool") -> str | None:
        """Get the actual executable path for an AI tool."""
        binary = cls.get_binary_name(tool)

        # Check PATH first
        path_binary = shutil.which(binary)
        if path_binary:
            return path_binary

        # Check known installation paths
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


class WorktreeTemplate(BaseModel):
    """Template configuration for common worktree workflows."""

    name: str = Field(..., description="Template name")
    description: str = Field(..., description="Template description")
    base_branch: str | None = Field(
        default=None,
        description="Default base branch for this template",
    )
    ai_tool: AITool | None = Field(
        default=None,
        description="AI tool to use (claude, opencode, droid)",
    )
    ai_instructions: str | None = Field(
        default=None,
        description="Instructions to send to AI when worktree is created",
    )
    tmux_layout: str | None = Field(
        default=None,
        description="tmux pane layout to use",
    )
    plan_mode: bool = Field(
        default=False,
        description="Start Claude in plan mode (safe exploration)",
    )
    auto_commands: list[str] = Field(
        default_factory=list,
        description="Commands to run automatically after creation",
    )
    install_deps: bool | None = Field(
        default=None,
        description="Override auto_install_deps setting",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags for categorizing templates",
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
    mouse_mode: bool = Field(
        default=True,
        description="Enable mouse support (click to switch panes, drag to resize)",
    )
    prefix_key: str = Field(
        default="C-z",
        description="tmux prefix key for owt sessions (e.g. C-z for Ctrl+z, C-b for Ctrl+b)",
    )


class WorkspaceConfig(BaseModel):
    """Configuration for unified workspace mode."""

    unified_mode: bool = Field(
        default=True,
        description="Use unified workspace mode by default (add panes instead of creating separate sessions)",
    )
    default_layout: str = Field(
        default="main-focus",
        description="Default workspace layout (main-focus, grid, stack, focus, tile)",
    )
    max_panes: int = Field(
        default=4,
        description="Maximum panes per workspace (1 main + N worktrees)",
    )
    auto_balance: bool = Field(
        default=True,
        description="Automatically balance pane sizes when adding/removing",
    )
    focus_on_create: bool = Field(
        default=True,
        description="Focus new pane when worktree is created",
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


class HooksConfig(BaseModel):
    """Configuration for status change hooks."""

    enabled: bool = Field(
        default=True,
        description="Enable status change hooks",
    )
    enable_notifications: bool = Field(
        default=True,
        description="Allow desktop notifications",
    )
    notification_sound: bool = Field(
        default=True,
        description="Play sound with notifications",
    )
    default_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Default timeout for hook execution in seconds",
    )
    max_history_entries: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Maximum hook history entries to keep",
    )
    log_hook_output: bool = Field(
        default=False,
        description="Log hook execution output",
    )


class GitHubConfig(BaseModel):
    """Configuration for GitHub integration."""

    api_token: str | None = Field(
        default=None,
        description="GitHub personal access token for API calls",
    )
    auto_link_prs: bool = Field(
        default=True,
        description="Auto-detect and link PRs from branch names",
    )
    branch_pr_pattern: str = Field(
        default=r".*#(\d+).*",
        description="Regex pattern to extract PR number from branch name",
    )
    default_remote: str = Field(
        default="origin",
        description="Default git remote for PR operations",
    )


class Config(BaseModel):
    """Main configuration model for open-orchestrator."""

    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    # AI tool-specific configurations
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    opencode: OpenCodeConfig = Field(default_factory=OpenCodeConfig)
    droid: DroidConfig = Field(default_factory=DroidConfig)
    # Worktree templates
    templates: dict[str, WorktreeTemplate] = Field(
        default_factory=dict,
        description="Custom worktree templates",
    )

    def get_template(self, name: str) -> WorktreeTemplate | None:
        """
        Get a template by name, checking custom templates first, then built-in.

        Args:
            name: Template name to retrieve

        Returns:
            WorktreeTemplate if found, None otherwise
        """
        # Check custom templates first
        if name in self.templates:
            return self.templates[name]

        # Fall back to built-in templates
        return get_builtin_template(name)


def get_builtin_templates() -> dict[str, WorktreeTemplate]:
    """
    Get built-in worktree templates inspired by Claude-Flow's specialized agents.

    Returns:
        Dictionary mapping template names to WorktreeTemplate instances
    """
    return {
        "bugfix": WorktreeTemplate(
            name="bugfix",
            description="Quick bugfix workflow - identify root cause, minimal changes, tests first",
            base_branch="main",
            ai_tool=AITool.CLAUDE,
            ai_instructions="Focus on: 1) Identifying root cause, 2) Writing tests that reproduce the bug, 3) Minimal code changes to fix",
            tmux_layout="three-pane",
            auto_commands=["git log --oneline -10", "git diff main"],
            tags=["quick", "maintenance"],
        ),
        "feature": WorktreeTemplate(
            name="feature",
            description="Full feature development - plan first, implement with tests, document",
            base_branch="develop",
            ai_tool=AITool.CLAUDE,
            ai_instructions="Follow this workflow: 1) Plan the implementation, 2) Write tests first (TDD), 3) Implement feature, 4) Document as you go",
            tmux_layout="quad",
            plan_mode=True,
            tags=["development", "tdd"],
        ),
        "research": WorktreeTemplate(
            name="research",
            description="Safe exploration mode - read-only, no code changes, document findings",
            base_branch="main",
            ai_tool=AITool.CLAUDE,
            ai_instructions="Explore and document options. Do NOT make code changes yet - this is research only. Document your findings thoroughly.",
            tmux_layout="main-vertical",
            plan_mode=True,
            install_deps=False,
            tags=["exploration", "read-only"],
        ),
        "security-audit": WorktreeTemplate(
            name="security-audit",
            description="Security review - scan for vulnerabilities, check dependencies, detect secrets",
            base_branch="main",
            ai_tool=AITool.CLAUDE,
            ai_instructions="Security audit checklist: 1) Review for common vulnerabilities (XSS, SQL injection, etc), 2) Check dependency security, 3) Scan for exposed secrets",
            tmux_layout="main-vertical",
            auto_commands=["npm audit 2>/dev/null || pip-audit 2>/dev/null || echo 'No audit tools found'"],
            tags=["security", "audit"],
        ),
        "refactor": WorktreeTemplate(
            name="refactor",
            description="Code refactoring - improve structure while maintaining functionality",
            base_branch="develop",
            ai_tool=AITool.CLAUDE,
            ai_instructions="Refactoring focus: 1) Identify code smells, 2) Plan refactoring steps, 3) Ensure tests pass at each step, 4) Keep commits small and focused",
            tmux_layout="three-pane",
            plan_mode=True,
            tags=["refactoring", "quality"],
        ),
        "hotfix": WorktreeTemplate(
            name="hotfix",
            description="Emergency production fix - fast, focused, minimal risk",
            base_branch="main",
            ai_tool=AITool.CLAUDE,
            ai_instructions="HOTFIX MODE: 1) Minimal changes only, 2) Must include test, 3) Focus on production stability",
            tmux_layout="main-vertical",
            tags=["urgent", "production"],
        ),
        "experiment": WorktreeTemplate(
            name="experiment",
            description="Try new approaches - isolated environment, no pressure",
            base_branch="develop",
            ai_tool=AITool.CLAUDE,
            ai_instructions="This is an experimental branch. Feel free to try different approaches. Document what works and what doesn't.",
            tmux_layout="quad",
            install_deps=True,
            tags=["experimental", "prototype"],
        ),
        "docs": WorktreeTemplate(
            name="docs",
            description="Documentation updates - README, API docs, guides",
            base_branch="main",
            ai_tool=AITool.CLAUDE,
            ai_instructions="Documentation focus: 1) Update relevant docs, 2) Check for outdated information, 3) Ensure examples work, 4) Improve clarity",
            tmux_layout="main-vertical",
            install_deps=False,
            tags=["documentation"],
        ),
    }


def get_builtin_template(name: str) -> WorktreeTemplate | None:
    """
    Get a built-in template by name.

    Args:
        name: Template name

    Returns:
        WorktreeTemplate if found, None otherwise
    """
    templates = get_builtin_templates()
    return templates.get(name)


def list_all_templates(config: Config) -> dict[str, WorktreeTemplate]:
    """
    Get all templates (built-in + custom).

    Args:
        config: Config instance with custom templates

    Returns:
        Dictionary of all available templates
    """
    templates = get_builtin_templates()
    # Custom templates override built-in
    templates.update(config.templates)
    return templates


def load_config(config_path: str | None = None) -> Config:
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
            except Exception as e:
                # Log config file load failures for debugging and security awareness
                logger.warning(f"Failed to load config from {path}: {e}")
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
