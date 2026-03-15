# Contributing to Open Orchestrator

Thanks for your interest in contributing! Here's how to get started.

## Dev Setup

```bash
git clone https://github.com/gitpcl/openorchestrator.git
cd openorchestrator
uv pip install -e ".[dev]"
```

You'll also need **tmux** and **Git** installed.

## Running Tests

```bash
# Full test suite with coverage
pytest -v --cov=src/open_orchestrator

# Quick run (no coverage)
pytest

# Lint + type check
ruff check src/ tests/
mypy src/
```

## Code Style

- **Formatter:** ruff (line length 130)
- **Type hints:** required on all public functions (Python 3.10+ syntax)
- **Dependencies:** click, pydantic, rich, textual, toml, gitpython, libtmux — avoid adding new ones without discussion
- **Target:** Python 3.10+

## Pull Request Process

1. Fork the repo and create a branch from `main`
2. Make your changes with tests where applicable
3. Ensure CI passes: `ruff check src/ tests/` + `mypy src/` + `pytest`
4. Open a PR against `main` with a clear description of the change

## Reporting Issues

Please use the [issue templates](https://github.com/gitpcl/openorchestrator/issues/new/choose) — there are forms for bug reports and feature requests.

## Architecture

See the [README](README.md#architecture) for the project structure. Key points:

- `cli.py` — all 15 commands live here (click)
- `core/switchboard.py` — Textual-based card grid UI
- `core/status.py` — SQLite + WAL for AI activity tracking
- `models/` — Pydantic data models
- Tests mock tmux via `conftest.py` fixtures (`mock_libtmux_server`, `mock_libtmux_session`)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
