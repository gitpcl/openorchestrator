"""
Pytest configuration and shared fixtures for Claude Orchestrator tests.
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_directory() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def temp_dir(temp_directory: Path) -> Path:
    """Alias for temp_directory for backward compatibility."""
    return temp_directory


@pytest.fixture
def git_repo(temp_directory: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository for tests."""
    repo_path = temp_directory / "test-repo"
    repo_path.mkdir()

    subprocess.run(
        ["git", "init"],
        cwd=repo_path,
        capture_output=True
    )

    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path,
        capture_output=True
    )

    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path,
        capture_output=True
    )

    readme = repo_path / "README.md"
    readme.write_text("# Test Repository\n")

    subprocess.run(
        ["git", "add", "."],
        cwd=repo_path,
        capture_output=True
    )

    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path,
        capture_output=True
    )

    yield repo_path


@pytest.fixture
def git_worktree(git_repo: Path, temp_directory: Path) -> Generator[Path, None, None]:
    """Create a git worktree for tests."""
    worktree_path = temp_directory / "test-worktree"

    subprocess.run(
        ["git", "worktree", "add", "-b", "test-branch", str(worktree_path)],
        cwd=git_repo,
        capture_output=True
    )

    yield worktree_path

    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=git_repo,
        capture_output=True
    )


@pytest.fixture
def mock_worktree_paths(temp_directory: Path) -> list[str]:
    """Create mock worktree directory paths."""
    paths = []

    for i in range(3):
        path = temp_directory / f"worktree-{i}"
        path.mkdir()
        paths.append(str(path))

    return paths


# Project detection fixtures


@pytest.fixture
def python_project_dir(temp_directory: Path) -> Path:
    """Create a mock Python project with pyproject.toml."""
    project_dir = temp_directory / "python-project"
    project_dir.mkdir()

    pyproject_content = """
[project]
name = "test-project"
version = "0.1.0"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = []
"""
    (project_dir / "pyproject.toml").write_text(pyproject_content)
    (project_dir / "uv.lock").write_text("# uv lock file")

    return project_dir


@pytest.fixture
def poetry_project_dir(temp_directory: Path) -> Path:
    """Create a mock Poetry project."""
    project_dir = temp_directory / "poetry-project"
    project_dir.mkdir()

    pyproject_content = """
[tool.poetry]
name = "test-project"
version = "0.1.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
"""
    (project_dir / "pyproject.toml").write_text(pyproject_content)
    (project_dir / "poetry.lock").write_text("# poetry lock file")

    return project_dir


@pytest.fixture
def pip_project_dir(temp_directory: Path) -> Path:
    """Create a mock pip project with requirements.txt."""
    project_dir = temp_directory / "pip-project"
    project_dir.mkdir()

    (project_dir / "requirements.txt").write_text("click>=8.0.0\npydantic>=2.0.0\n")

    return project_dir


@pytest.fixture
def node_npm_project_dir(temp_directory: Path) -> Path:
    """Create a mock Node.js project with npm."""
    project_dir = temp_directory / "node-npm-project"
    project_dir.mkdir()

    package_json = {
        "name": "test-project",
        "version": "1.0.0",
        "dependencies": {}
    }
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    (project_dir / "package-lock.json").write_text("{}")

    return project_dir


@pytest.fixture
def node_yarn_project_dir(temp_directory: Path) -> Path:
    """Create a mock Node.js project with yarn."""
    project_dir = temp_directory / "node-yarn-project"
    project_dir.mkdir()

    package_json = {
        "name": "test-project",
        "version": "1.0.0",
        "packageManager": "yarn@4.0.0"
    }
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    (project_dir / "yarn.lock").write_text("# yarn lock file")

    return project_dir


@pytest.fixture
def node_pnpm_project_dir(temp_directory: Path) -> Path:
    """Create a mock Node.js project with pnpm."""
    project_dir = temp_directory / "node-pnpm-project"
    project_dir.mkdir()

    package_json = {
        "name": "test-project",
        "version": "1.0.0"
    }
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    (project_dir / "pnpm-lock.yaml").write_text("lockfileVersion: 6.0")

    return project_dir


@pytest.fixture
def node_bun_project_dir(temp_directory: Path) -> Path:
    """Create a mock Node.js project with bun."""
    project_dir = temp_directory / "node-bun-project"
    project_dir.mkdir()

    package_json = {
        "name": "test-project",
        "version": "1.0.0"
    }
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    (project_dir / "bun.lockb").write_bytes(b"bun binary lock file")

    return project_dir


@pytest.fixture
def rust_project_dir(temp_directory: Path) -> Path:
    """Create a mock Rust project."""
    project_dir = temp_directory / "rust-project"
    project_dir.mkdir()

    cargo_toml = """
[package]
name = "test-project"
version = "0.1.0"
edition = "2021"
"""
    (project_dir / "Cargo.toml").write_text(cargo_toml)
    (project_dir / "Cargo.lock").write_text("# cargo lock file")

    return project_dir


@pytest.fixture
def go_project_dir(temp_directory: Path) -> Path:
    """Create a mock Go project."""
    project_dir = temp_directory / "go-project"
    project_dir.mkdir()

    (project_dir / "go.mod").write_text("module test-project\n\ngo 1.21\n")
    (project_dir / "go.sum").write_text("")

    return project_dir


@pytest.fixture
def php_project_dir(temp_directory: Path) -> Path:
    """Create a mock PHP/Composer project."""
    project_dir = temp_directory / "php-project"
    project_dir.mkdir()

    composer_json = {
        "name": "test/project",
        "require": {}
    }
    (project_dir / "composer.json").write_text(json.dumps(composer_json, indent=2))
    (project_dir / "composer.lock").write_text("{}")

    return project_dir


@pytest.fixture
def monorepo_project_dir(temp_directory: Path) -> Path:
    """Create a mock monorepo with multiple project types."""
    project_dir = temp_directory / "monorepo"
    project_dir.mkdir()

    pyproject_content = """
[project]
name = "monorepo-backend"
version = "0.1.0"
"""
    (project_dir / "pyproject.toml").write_text(pyproject_content)

    package_json = {
        "name": "monorepo-frontend",
        "version": "1.0.0"
    }
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))

    return project_dir


@pytest.fixture
def empty_project_dir(temp_directory: Path) -> Path:
    """Create an empty directory with no project markers."""
    project_dir = temp_directory / "empty-project"
    project_dir.mkdir()
    return project_dir


@pytest.fixture
def project_with_env(temp_directory: Path) -> Path:
    """Create a project with .env file."""
    project_dir = temp_directory / "project-with-env"
    project_dir.mkdir()

    (project_dir / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    (project_dir / ".env").write_text("SECRET_KEY=test123\nDATABASE_URL=postgres://localhost/db\n")

    return project_dir


# Cleanup service fixtures


@pytest.fixture
def usage_stats_file(temp_directory: Path) -> Path:
    """Create a mock usage stats file."""
    stats_dir = temp_directory / ".claude-orchestrator"
    stats_dir.mkdir(parents=True, exist_ok=True)
    stats_file = stats_dir / ".worktree_stats.json"

    now = datetime.now()
    old_date = (now - timedelta(days=30)).isoformat()

    stats_data = {
        "/path/to/old-worktree": {
            "branch_name": "feature/old",
            "created_at": old_date,
            "last_accessed": old_date,
            "access_count": 5
        },
        "/path/to/recent-worktree": {
            "branch_name": "feature/recent",
            "created_at": now.isoformat(),
            "last_accessed": now.isoformat(),
            "access_count": 10
        }
    }

    stats_file.write_text(json.dumps(stats_data, indent=2))
    return stats_file


# Mock fixtures for tmux


@pytest.fixture
def mock_libtmux_server() -> MagicMock:
    """Create a mock libtmux server."""
    server = MagicMock()
    server.has_session.return_value = False
    server.sessions = []
    return server


@pytest.fixture
def mock_libtmux_session() -> MagicMock:
    """Create a mock libtmux session."""
    session = MagicMock()
    session.name = "cwt-test-session"
    session.id = "$1"
    session.attached_count = 0

    window = MagicMock()
    window.panes = [MagicMock()]
    window.panes[0].current_path = "/tmp/test"
    session.windows = [window]
    session.active_window = window

    return session


@pytest.fixture
def mock_subprocess_run():
    """Fixture to mock subprocess.run for git commands."""
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        yield mock_run
