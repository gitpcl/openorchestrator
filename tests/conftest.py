"""
Pytest configuration and shared fixtures for Open Orchestrator tests.
"""

import json
import subprocess
import tempfile
from collections.abc import Generator
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def temp_directory() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir).resolve()


@pytest.fixture
def temp_dir(temp_directory: Path) -> Path:
    """Alias for temp_directory for backward compatibility."""
    return temp_directory


@pytest.fixture
def git_repo(temp_directory: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository for tests."""
    repo_path = temp_directory / "test-repo"
    repo_path.mkdir()

    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True)

    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True)

    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True)

    readme = repo_path / "README.md"
    readme.write_text("# Test Repository\n")

    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)

    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True)

    yield repo_path


@pytest.fixture
def git_worktree(git_repo: Path, temp_directory: Path) -> Generator[Path, None, None]:
    """Create a git worktree for tests."""
    worktree_path = temp_directory / "test-worktree"

    subprocess.run(["git", "worktree", "add", "-b", "test-branch", str(worktree_path)], cwd=git_repo, capture_output=True)

    yield worktree_path

    subprocess.run(["git", "worktree", "remove", "--force", str(worktree_path)], cwd=git_repo, capture_output=True)


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

    package_json = {"name": "test-project", "version": "1.0.0", "dependencies": {}}
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    (project_dir / "package-lock.json").write_text("{}")

    return project_dir


@pytest.fixture
def node_yarn_project_dir(temp_directory: Path) -> Path:
    """Create a mock Node.js project with yarn."""
    project_dir = temp_directory / "node-yarn-project"
    project_dir.mkdir()

    package_json = {"name": "test-project", "version": "1.0.0", "packageManager": "yarn@4.0.0"}
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    (project_dir / "yarn.lock").write_text("# yarn lock file")

    return project_dir


@pytest.fixture
def node_pnpm_project_dir(temp_directory: Path) -> Path:
    """Create a mock Node.js project with pnpm."""
    project_dir = temp_directory / "node-pnpm-project"
    project_dir.mkdir()

    package_json = {"name": "test-project", "version": "1.0.0"}
    (project_dir / "package.json").write_text(json.dumps(package_json, indent=2))
    (project_dir / "pnpm-lock.yaml").write_text("lockfileVersion: 6.0")

    return project_dir


@pytest.fixture
def node_bun_project_dir(temp_directory: Path) -> Path:
    """Create a mock Node.js project with bun."""
    project_dir = temp_directory / "node-bun-project"
    project_dir.mkdir()

    package_json = {"name": "test-project", "version": "1.0.0"}
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

    composer_json = {"name": "test/project", "require": {}}
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

    package_json = {"name": "monorepo-frontend", "version": "1.0.0"}
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
    stats_dir = temp_directory / ".open-orchestrator"
    stats_dir.mkdir(parents=True, exist_ok=True)
    stats_file = stats_dir / ".worktree_stats.json"

    now = datetime.now()
    old_date = (now - timedelta(days=30)).isoformat()

    stats_data = {
        "/path/to/old-worktree": {
            "branch_name": "feature/old",
            "created_at": old_date,
            "last_accessed": old_date,
            "access_count": 5,
        },
        "/path/to/recent-worktree": {
            "branch_name": "feature/recent",
            "created_at": now.isoformat(),
            "last_accessed": now.isoformat(),
            "access_count": 10,
        },
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
    session.name = "owt-test-session"
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


# Skill installation fixtures


@pytest.fixture
def skills_source_dir(temp_directory: Path) -> Path:
    """
    Create a mock skills source directory with SKILL.md file.

    Simulates the structure of src/open_orchestrator/skills/open-orchestrator/
    for testing skill installation functionality.

    Returns:
        Path: Directory containing mock SKILL.md file
    """
    skills_dir = temp_directory / "skills-source" / "open-orchestrator"
    skills_dir.mkdir(parents=True)

    skill_content = """# Open Orchestrator Skill

Git Worktree + Claude Code orchestration tool for parallel development workflows.

## Commands

- `/worktree` - Main worktree management command
- `/wt-create` - Quick worktree creation
- `/wt-list` - List worktrees
- `/wt-status` - Show Claude activity across worktrees
- `/wt-cleanup` - Cleanup stale worktrees

## Usage

This is a test skill file for testing skill installation.
"""
    (skills_dir / "SKILL.md").write_text(skill_content)

    return skills_dir


@pytest.fixture
def mock_skills_dir(temp_directory: Path) -> Path:
    """
    Create a temporary target directory for skill installation testing.

    Simulates the ~/.claude/skills/ directory where skills are installed.

    Returns:
        Path: Empty directory for skill installation testing
    """
    skills_target = temp_directory / ".claude" / "skills"
    skills_target.mkdir(parents=True)

    return skills_target


# Hook testing fixtures


@pytest.fixture
def hooks_config(temp_directory: Path) -> Path:
    """
    Create a mock hooks configuration directory with sample hook configurations.

    Creates a .open-orchestrator directory with hooks.json containing
    sample webhook and shell command hooks for testing.

    Returns:
        Path: Directory containing hooks.json file
    """
    config_dir = temp_directory / ".open-orchestrator"
    config_dir.mkdir(parents=True, exist_ok=True)

    hooks_data = {
        "hooks": [
            {"type": "shell", "command": "echo 'Status changed: {status}'", "events": ["status_change"], "enabled": True},
            {
                "type": "webhook",
                "url": "https://example.com/webhook",
                "events": ["worktree_created", "worktree_deleted"],
                "enabled": True,
            },
        ]
    }

    hooks_file = config_dir / "hooks.json"
    hooks_file.write_text(json.dumps(hooks_data, indent=2))

    return config_dir


@pytest.fixture
def mock_subprocess():
    """
    Mock subprocess.run for hook execution testing.

    Provides a patched subprocess.run that returns successful execution results
    for testing shell command hooks without actually executing commands.

    Yields:
        MagicMock: Mocked subprocess.run function
    """
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Hook executed successfully"
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        yield mock_run


# Session management fixtures


@pytest.fixture
def temp_session_dir(temp_directory: Path) -> Path:
    """
    Create a temporary directory for session storage testing.

    Simulates the session storage directory structure used by SessionManager
    for storing Claude Code session data.

    Returns:
        Path: Empty directory for session storage testing
    """
    session_dir = temp_directory / ".open-orchestrator" / "sessions"
    session_dir.mkdir(parents=True)

    return session_dir


@pytest.fixture
def mock_session_store(temp_session_dir: Path) -> Path:
    """
    Create a pre-populated session store for testing.

    Creates sample session data files to test session copying,
    resuming, and management functionality.

    Returns:
        Path: Directory containing pre-populated session data files
    """
    # Create sample session data
    session_1 = temp_session_dir / "worktree-1"
    session_1.mkdir()

    session_data_1 = {
        "worktree_name": "worktree-1",
        "session_id": "abc123",
        "created_at": "2024-02-01T10:00:00",
        "last_message": "Implementing authentication feature",
        "message_count": 15,
        "conversation_data": {"messages": ["User: Add login", "Assistant: I'll help with that"]},
    }
    (session_1 / "session.json").write_text(json.dumps(session_data_1, indent=2))

    session_2 = temp_session_dir / "worktree-2"
    session_2.mkdir()

    session_data_2 = {
        "worktree_name": "worktree-2",
        "session_id": "def456",
        "created_at": "2024-02-01T11:00:00",
        "last_message": "Fixing bug in dashboard",
        "message_count": 8,
        "conversation_data": {"messages": ["User: Fix dashboard bug", "Assistant: Let me investigate"]},
    }
    (session_2 / "session.json").write_text(json.dumps(session_data_2, indent=2))

    return temp_session_dir


# PR linking fixtures


@pytest.fixture
def mock_gh_cli():
    """
    Mock GitHub CLI (gh) subprocess calls for PR linking testing.

    Provides patched subprocess.run that simulates GitHub CLI responses
    for testing PR creation, status checks, and linking without requiring
    actual GitHub API access.

    Yields:
        MagicMock: Mocked subprocess.run with GitHub CLI response simulation
    """
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0

        # Simulate gh pr list output
        pr_list_output = json.dumps(
            [
                {"number": 123, "title": "Add new feature", "state": "OPEN", "url": "https://github.com/owner/repo/pull/123"},
                {"number": 124, "title": "Fix bug", "state": "MERGED", "url": "https://github.com/owner/repo/pull/124"},
            ]
        )

        mock_result.stdout = pr_list_output
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        yield mock_run


@pytest.fixture
def pr_store(temp_directory: Path) -> Path:
    """
    Create a temporary directory for PR metadata storage testing.

    Simulates the directory structure used for storing PR linking metadata
    that maps worktrees to GitHub Pull Requests.

    Returns:
        Path: Empty directory for PR metadata storage testing
    """
    pr_dir = temp_directory / ".open-orchestrator" / "prs"
    pr_dir.mkdir(parents=True)

    return pr_dir


# Process manager fixtures


@pytest.fixture
def temp_pids(temp_directory: Path) -> Path:
    """
    Create a temporary directory for PID files and process logs.

    Simulates the directory structure used by ProcessManager for tracking
    non-tmux AI tool processes via PID files and log outputs.

    Returns:
        Path: Empty directory for PID file storage testing
    """
    pids_dir = temp_directory / ".open-orchestrator" / "processes"
    pids_dir.mkdir(parents=True)

    return pids_dir


@pytest.fixture
def mock_process() -> MagicMock:
    """
    Create a mock running process for process management testing.

    Provides a mock Process object with typical attributes (pid, status, etc.)
    for testing process lifecycle management without starting actual processes.

    Returns:
        MagicMock: Mock process object with standard attributes
    """
    process = MagicMock()
    process.pid = 12345
    process.returncode = None
    process.poll.return_value = None
    process.terminate.return_value = None
    process.kill.return_value = None
    process.wait.return_value = 0

    return process


# Dashboard testing fixtures


@pytest.fixture
def mock_status_tracker(temp_directory: Path) -> MagicMock:
    """
    Create a pre-configured StatusTracker with test data for dashboard testing.

    Provides a StatusTracker instance with multiple worktrees in different states
    (working, idle, blocked) for testing dashboard display and interaction.

    Returns:
        MagicMock: StatusTracker mock with pre-configured test worktree data
    """
    from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

    tracker = MagicMock()

    # Mock worktrees data
    worktree_1 = WorktreeAIStatus(
        worktree_name="feature-auth",
        worktree_path=str(temp_directory / "worktrees" / "feature-auth"),
        branch="feature/authentication",
        activity_status=AIActivityStatus.WORKING,
        current_task="Implementing JWT authentication",
        tmux_session="owt-feature-auth",
    )

    worktree_2 = WorktreeAIStatus(
        worktree_name="fix-dashboard",
        worktree_path=str(temp_directory / "worktrees" / "fix-dashboard"),
        branch="fix/dashboard-bug",
        activity_status=AIActivityStatus.IDLE,
        current_task=None,
        tmux_session="owt-fix-dashboard",
    )

    worktree_3 = WorktreeAIStatus(
        worktree_name="refactor-api",
        worktree_path=str(temp_directory / "worktrees" / "refactor-api"),
        branch="refactor/api-cleanup",
        activity_status=AIActivityStatus.BLOCKED,
        current_task="Refactoring API endpoints",
        tmux_session="owt-refactor-api",
    )

    tracker.get_all_statuses.return_value = [worktree_1, worktree_2, worktree_3]
    tracker.get_summary.return_value = MagicMock(
        total_worktrees=3, active_ai_sessions=2, idle_ai_sessions=1, blocked_ai_sessions=1, total_commands_sent=2
    )

    return tracker
