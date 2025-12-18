"""Pydantic models for project configuration and detection."""

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class PackageManager(str, Enum):
    """Supported package managers for different project types."""

    # Python
    UV = "uv"
    PIP = "pip"
    POETRY = "poetry"
    PIPENV = "pipenv"

    # Node.js
    NPM = "npm"
    YARN = "yarn"
    PNPM = "pnpm"
    BUN = "bun"

    # PHP
    COMPOSER = "composer"

    # Rust
    CARGO = "cargo"

    # Go
    GO = "go"

    # Unknown
    UNKNOWN = "unknown"


class ProjectType(str, Enum):
    """Supported project types."""

    PYTHON = "python"
    NODE = "node"
    PHP = "php"
    RUST = "rust"
    GO = "go"
    UNKNOWN = "unknown"


class ProjectConfig(BaseModel):
    """Configuration model for a detected project."""

    project_type: ProjectType = Field(
        default=ProjectType.UNKNOWN,
        description="The detected project type"
    )
    package_manager: PackageManager = Field(
        default=PackageManager.UNKNOWN,
        description="The detected or preferred package manager"
    )
    project_root: Path = Field(
        description="Root directory of the project"
    )
    has_lock_file: bool = Field(
        default=False,
        description="Whether a lock file was detected"
    )
    lock_file_path: Path | None = Field(
        default=None,
        description="Path to the lock file if detected"
    )
    manifest_file_path: Path | None = Field(
        default=None,
        description="Path to the project manifest file"
    )
    env_file_path: Path | None = Field(
        default=None,
        description="Path to the .env file if detected"
    )
    install_command: str = Field(
        default="",
        description="Command to install dependencies"
    )

    class Config:
        """Pydantic config."""

        # Note: Don't use use_enum_values=True as it breaks enum usage in other modules
        pass

    def get_install_command(self) -> str:
        """Get the appropriate install command for this project."""
        if self.install_command:
            return self.install_command

        install_commands = {
            PackageManager.UV: "uv sync",
            PackageManager.PIP: "pip install -r requirements.txt",
            PackageManager.POETRY: "poetry install",
            PackageManager.PIPENV: "pipenv install",
            PackageManager.NPM: "npm install",
            PackageManager.YARN: "yarn install",
            PackageManager.PNPM: "pnpm install",
            PackageManager.BUN: "bun install",
            PackageManager.COMPOSER: "composer install",
            PackageManager.CARGO: "cargo build",
            PackageManager.GO: "go mod download",
        }

        return install_commands.get(self.package_manager, "")
