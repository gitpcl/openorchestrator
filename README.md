# Open Orchestrator

[![CI](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml) [![License](https://img.shields.io/github/license/gitpcl/openorchestrator)](LICENSE)

A lean Git Worktree + AI agent orchestration tool for parallel development workflows. Coordinate multiple AI coding sessions across isolated branches from a Textual-based **control plane** — a prioritized decision surface (NEEDS YOU / READY TO SHIP / IN FLIGHT / BACKGROUND) where every row carries a verb action. Supports Claude Code, Pi, OpenCode, and Droid. Optional **herdr multiplexer backend** swaps the rendering surface; optional **Agno intelligence layer** adds AI-powered planning, quality gating, and merge conflict resolution; optional **MCP peer communication** enables agent-to-agent messaging and coordination.

## Overview

Open Orchestrator enables developers to work on multiple tasks simultaneously by creating isolated worktrees, each with its own AI coding session and tmux session. Start with `owt new "task description"` — it auto-generates a branch name, creates the worktree, installs dependencies, copies `.env`, and starts the AI tool. Run `owt` to launch the **control plane** — four prioritized sections, verb-per-row actions, empty sections hidden so you always see the most important thing first. The legacy card grid is still available behind `owt --legacy-cards` for one release.

![Open Orchestrator demo](assets/demo.gif)

> **Agent Teams vs Open Orchestrator:** [Claude Code's Agent Teams](https://code.claude.com/docs/en/agent-teams) coordinate multiple AI agents within the *same codebase*. Open Orchestrator manages multiple *isolated worktrees* (different branches, different directories, independent environments). They're complementary — use Agent Teams for intra-branch collaboration, Open Orchestrator for cross-branch orchestration.

## Highlights

- **40+ commands** — focused CLI surface, no bloat (see [docs/commands.md](docs/commands.md))
- **Control Plane UI** — Textual sectioned decision surface; each row exposes verb actions (`[s]hip`, `[r]eview`, `[a]ttach`, `[f]ix`, `[m]erge`, `[x] dismiss`); empty sections hidden
- **Pluggable multiplexer backends** — tmux by default, **[herdr](https://herdr.dev)** opt-in via `--herdr` or `[backend] mode = "herdr" | "auto"` (see [docs/multiplexer-backends.md](docs/multiplexer-backends.md))
- **Conflict Guard** — real-time file overlap detection between parallel agents; warns before merge when two branches touch the same files
- **AI-Powered Planning** — `owt plan "Build auth system"` decomposes a goal into a dependency-aware DAG, spawns agents in parallel, auto-injects parent context into child tasks
- **Orchestrator Agent** — `owt orchestrate` drives a plan end-to-end into a feature branch with coordination, user presence detection, and stop/resume
- **Patchable automated sessions** — orchestrated and batch agents run in live tmux-backed provider sessions, receive their task automatically, stay patchable via `owt attach`, and still export `OWT_AUTOMATED=1` so hooks can treat them as automation
- **MCP Peer Communication** — agents discover each other via `list_peers`, exchange messages via `send_message`/`check_messages`, and coordinate file edits via `get_peer_files`
- **Autopilot Loops** — `owt batch tasks.toml` runs Karpathy-style autonomous loops with DAG-aware scheduling
- **Agent Broadcast** — `owt send --all "Run tests"` fans out instructions to all active agents
- **Merge Queue** — `owt queue` shows optimal merge order; `owt queue --ship` ships all completed work intelligently
- **Memory + Recall** — `owt memory add/search/consolidate/list/mine` plus a SQLite + FTS5 backed structured fact store with 4-layer token-budgeted stack, temporal knowledge graph, and contradiction detection. Pure stdlib `sqlite3` — zero new dependencies
- **Swarm Mode** — `owt swarm start "goal" -w worktree` launches a coordinator + specialized workers in tmux panes within one worktree
- **Critic Pattern** — `owt critic ship|merge|delete <name>` runs a pre-action safety review with denial tracking
- **Dream Mode** — `owt dream enable` starts a background daemon that periodically consolidates memory, surfaces stale worktrees, and detects knowledge-graph contradictions
- **Multi-palette Theming** — auto-detects terminal background; four palettes (`dark`, `light`, `dark-ansi`, `light-ansi`); `--theme` flag overrides
- **Headless Mode** — `owt new "task" --headless` for CI/CD; `owt wait` polls until agent finishes
- **Quality Gate** — `owt ship` optionally runs AI quality review before merging (with Agno)
- **AI Conflict Resolution** — merge conflicts can be resolved semantically by an AI agent before falling back to manual resolution
- **Two-phase merge** — `owt merge` catches conflicts early with file overlap warnings, supports `--rebase`, `--strategy ours|theirs`, `--leave-conflicts`
- **Plugin Architecture** — register custom AI tools via config without code changes; built-in support for Claude, Pi, OpenCode, Droid
- **Structured Logging** — correlation IDs, per-worktree context, JSON output (`--log-format json`) for log aggregation
- **Diagnostics** — `owt doctor` finds orphaned worktrees/sessions/status entries; `owt config validate` checks config; `owt db health` reports database stats
- **7 dependencies** — click, pydantic, rich, textual, toml, gitpython, libtmux (+ optional `agno`, `mcp`)

## Installation

### Requirements

- Python 3.10+
- Git
- tmux
- An AI coding tool (Claude Code, Pi, OpenCode, or Droid)

### Install from PyPI

```bash
pip install open-orchestrator

# With Agno intelligence layer (AI-powered planning, quality gate, conflict resolution)
pip install open-orchestrator[agno]

# With MCP peer communication (agent-to-agent messaging)
pip install open-orchestrator[mcp]

# Both
pip install open-orchestrator[agno,mcp]
```

### Install from source

```bash
git clone https://github.com/gitpcl/openorchestrator.git
cd openorchestrator
uv pip install -e .

# With optional features
uv pip install -e ".[agno]"        # Intelligence layer
uv pip install -e ".[mcp]"         # Peer communication
uv pip install -e ".[agno,mcp]"    # Both
```

## Quick Start

```bash
# Launch the control plane (default) — sectioned decision surface
owt

# Create a worktree with AI agent (one command does everything)
owt new "Add user authentication with JWT"

# Hand off to the agent's session (via the active backend — tmux or herdr)
owt attach auth-jwt
# Or press 'a' on the row in the control plane

# Interact from the CLI
owt send auth-jwt "Fix the failing tests"
owt switch auth-jwt    # Jump to that tmux session

# Ship when done (commit + merge + delete in one shot)
owt ship auth-jwt
# Or press 's' on the READY TO SHIP row in the control plane
```

## Documentation

| Topic | Where |
|-------|-------|
| Full CLI reference, control plane, workflow patterns | [docs/commands.md](docs/commands.md) |
| Config file schema, env vars, AI tool support, Agno + MCP layers | [docs/configuration.md](docs/configuration.md) |
| tmux vs herdr multiplexer backends | [docs/multiplexer-backends.md](docs/multiplexer-backends.md) |
| Herdr integration deep dive — protocol, troubleshooting, named sessions | [docs/herdr-integration.md](docs/herdr-integration.md) |
| Security model and trust boundaries | [docs/security.md](docs/security.md) |

## Development

```bash
uv pip install -e .
uv run pytest
uv run ruff check src/
uv run mypy src/
```

## License

MIT
