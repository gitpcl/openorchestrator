# Testing Guide

This guide explains how to run tests for the Open Orchestrator project.

## Quick Start

### Run All Tests
```bash
make test
```

### Run Tests Excluding Slow Tests
```bash
make test-fast
```

### Run with Coverage Report
```bash
make test-cov
```

## Docker Test Environment

The project includes a Docker-based test environment for isolated, reproducible testing across platforms.

### Prerequisites
- Docker and Docker Compose installed
- Docker daemon running

### Run Tests in Docker
```bash
# Run automated test suite
make test-docker

# Or using docker compose directly
docker compose -f docker-compose.test.yml up --build
```

### Interactive Docker Testing
For manual test execution and debugging:
```bash
# Start interactive shell
make test-docker-interactive

# Or using docker compose directly
docker compose -f docker-compose.test.yml run --rm test-interactive

# Inside the container, run specific tests
pytest tests/test_skill_installer.py -v
pytest -m gh_cli  # Run only tests requiring GitHub CLI
```

## Docker Test Environment Details

### Services

#### test-runner
- Automatically runs the full test suite with coverage
- Generates coverage reports in `htmlcov/` and `coverage.xml`
- Mounts project directory for live code changes
- Injects `GITHUB_TOKEN` environment variable for PR tests

#### test-interactive
- Provides interactive bash shell for manual testing
- Same environment as test-runner
- Useful for debugging test failures

### Configuration Files

- `Dockerfile.test`: Python 3.11-slim with git, tmux, curl, and all project dependencies
- `docker-compose.test.yml`: Service definitions and environment configuration

## Test Markers

Tests are categorized using pytest markers:

```bash
# Run only tests requiring GitHub CLI
pytest -m gh_cli

# Run only tmux-dependent tests
pytest -m tmux

# Run only Textual TUI tests
pytest -m textual

# Run only slow tests (>1 second)
pytest -m slow

# Exclude slow tests
pytest -m "not slow"
```

## Coverage Reporting

### Coverage Configuration
- Target: 90% coverage for new modules
- Source: `src/open_orchestrator/`
- Branch coverage enabled
- Reports: Terminal, HTML, and XML formats

### Generate Coverage Reports
```bash
# Terminal report with missing lines
pytest --cov=src/open_orchestrator --cov-report=term-missing

# HTML report (opens in browser)
make test-cov

# All report formats
make test
```

### Coverage Reports Location
- HTML: `htmlcov/index.html`
- XML: `coverage.xml`
- Terminal: Displayed after test run

## Test Organization

### Test Structure
```
tests/
├── conftest.py                      # Shared fixtures (30+ fixtures)
├── test_docker_infrastructure.py    # Docker & pytest configuration tests
├── test_skill_installer.py          # SkillInstaller unit tests
├── test_dashboard.py                # Dashboard TUI integration tests
├── test_process_manager.py          # ProcessManager unit & CLI tests
├── test_hooks.py                    # HookService unit tests
├── test_session.py                  # SessionManager unit tests
├── test_pr_linker.py                # PRLinker unit tests
├── test_status.py                   # StatusTracker & TokenUsage tests
├── test_worktree.py                 # WorktreeManager unit tests
├── test_tmux_manager.py             # TmuxManager unit tests
├── test_cli.py                      # CLI integration tests
├── test_cleanup.py                  # CleanupService tests
└── test_sync.py                     # SyncService tests
```

### Test Fixtures
`tests/conftest.py` provides 30+ reusable fixtures including:
- `temp_directory`: Temporary directory for file operations
- `git_repo`: Initialized git repository
- `git_worktree`: Git worktree setup
- `cli_runner`: Click CLI test runner
- `mock_libtmux_server`: Mocked tmux operations
- `mock_subprocess`: Mocked subprocess calls
- `skills_source_dir`: Temporary skills directory
- `hooks_config`: Mock hook configuration
- And many more...

## Linting and Formatting

### Run Linting Checks
```bash
make lint
```

### Auto-format Code
```bash
make format
```

### Linting Tools
- **ruff**: Fast Python linter (replaces flake8, isort, etc.)
- **mypy**: Static type checker

## Continuous Integration

The Docker test environment is designed for use in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests in Docker
  run: docker compose -f docker-compose.test.yml up --build --exit-code-from test-runner
```

## Troubleshooting

### Docker Build Failures
If Docker build fails, try:
```bash
# Rebuild without cache
docker compose -f docker-compose.test.yml build --no-cache
```

### Permission Issues
If you encounter permission issues with volume mounts:
```bash
# Check Docker user permissions
docker compose -f docker-compose.test.yml run --rm test-interactive whoami
```

### Test Failures
For debugging test failures:
```bash
# Run specific test with verbose output
pytest tests/test_skill_installer.py::TestSkillInstaller::test_symlink_installation -vv

# Run with pdb debugger on failure
pytest --pdb tests/test_skill_installer.py

# Show local variables on failure
pytest -l tests/test_skill_installer.py
```

## Development Workflow

### Typical Test-Driven Development Flow

1. **Write test first**:
   ```bash
   # Create/edit test file
   vim tests/test_new_feature.py
   ```

2. **Run test (should fail)**:
   ```bash
   pytest tests/test_new_feature.py -v
   ```

3. **Implement feature**:
   ```bash
   vim src/open_orchestrator/core/new_feature.py
   ```

4. **Run test again (should pass)**:
   ```bash
   pytest tests/test_new_feature.py -v
   ```

5. **Check coverage**:
   ```bash
   pytest tests/test_new_feature.py --cov=src/open_orchestrator/core/new_feature
   ```

6. **Run linting**:
   ```bash
   make lint
   ```

7. **Format code**:
   ```bash
   make format
   ```

8. **Run full test suite**:
   ```bash
   make test
   ```

## Cleaning Up

Remove test artifacts and caches:
```bash
make clean
```

This removes:
- `.pytest_cache/`
- `htmlcov/`
- `.coverage`
- `coverage.xml`
- `.mypy_cache/`
- `.ruff_cache/`
- `__pycache__/` directories
- `*.pyc` files
