# Open Orchestrator

[![CI](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml) [![License](https://img.shields.io/github/license/gitpcl/openorchestrator)](LICENSE)

**The multi-provider cockpit for parallel AI coding.** Open Orchestrator doesn't try to be the agent — it *supervises* them. Run a native Claude Code workflow in one worktree, a Pi session in another, Droid or OpenCode in a third, and watch them all from one keyboard-driven **control plane**: a prioritized decision surface with three lanes — **NEEDS YOU / READY TO SHIP / IN FLIGHT** — where every row carries a verb action. **Conflict Guard** watches every worktree in real time and warns you the moment two agents start editing the same files, long before they collide at merge.

You own the cockpit; the AI tools own the engine.

## Overview

**The primary interface is one command: `owt`.** It launches the **control plane** — a Textual decision surface with three prioritized sections (empty sections hidden, so the most important thing is always on top) where you run the whole loop without memorizing CLI verbs:

- **`n`** — start work: type a task, pick how to run it (one worktree, or a native plan-first workflow), confirm.
- **`a`** — jump into the agent's session. **`s`** — ship a finished worktree. The footer shows exactly the keys that apply to the row you're on.

Each `owt new` auto-generates a branch name, creates the worktree, installs dependencies, copies `.env`, and starts your chosen AI tool. The CLI verbs below are the same actions exposed for scripting and CI — you rarely need to type them by hand.

![Open Orchestrator demo](assets/demo.gif)

> **Agent Teams vs Open Orchestrator:** [Claude Code's Agent Teams](https://code.claude.com/docs/en/agent-teams) coordinate multiple AI agents within the *same codebase*. Open Orchestrator supervises multiple *isolated worktrees* (different branches, different directories, independent environments) across *different AI providers*. They're complementary — use Agent Teams for intra-branch collaboration, Open Orchestrator as the cockpit that supervises Claude Code + Pi + Droid + OpenCode on one screen.

## Highlights

- **Control Plane cockpit** — the front door. Press `[n]ew` to start work (one worktree or a native plan-first workflow); each row exposes verb actions (`[s]hip`, `[a]ttach`, `[f]ix`, `[m]erge`); the footer is context-sensitive — it shows only the keys that apply to the focused row; empty lanes hidden. One persistent board across long-lived worktrees — something native tooling doesn't give you.
- **Conflict Guard** — real-time file-overlap detection between parallel agents; the merge queue surfaces an overlap count per worktree and warns before merge when two branches touch the same files. This is the safety net that makes running many agents at once sane.
- **Multi-provider plugin layer** — built-in support for **Claude Code, Pi, OpenCode, Droid, and ClawCore** (a one-shot code-as-action engine); register custom AI tools via config without code changes, including one-shot tools that take the task as argv (`task_via_args`). `owt new --ai-tool <name>` picks the engine per worktree; auto-detection picks the best installed tool when you don't.
- **Native workflow launch** — `owt new "task" --workflow` starts a plan-first native Claude Code workflow in a managed worktree and tracks it on the board alongside everything else. owt supervises native; it doesn't replace it.
- **Pluggable multiplexer backends** — tmux by default, **[herdr](https://herdr.dev)** opt-in via `--herdr` or `[backend] mode = "herdr" | "auto"` (see [docs/multiplexer-backends.md](docs/multiplexer-backends.md))
- **Two-phase merge + queue** — `owt merge` catches conflicts early with file-overlap warnings and supports `--rebase`, `--strategy ours|theirs`, `--leave-conflicts`; `owt queue` shows optimal merge order; `owt queue --ship` ships completed work in order.
- **Agent Broadcast** — `owt send --all "Run tests"` fans out instructions to all active agents; `owt send --working` targets only the busy ones.
- **Multi-palette Theming** — auto-detects terminal background; four palettes (`dark`, `light`, `dark-ansi`, `light-ansi`); `--theme` flag overrides
- **Headless Mode** — `owt new "task" --headless` for CI/CD; `owt wait` polls until an agent finishes
- **MCP Peer Communication** *(optional)* — agents discover each other via `list_peers` and exchange messages via `send_message`/`check_messages` (`pip install open-orchestrator[mcp]`)
- **Plugin Architecture** — register custom AI tools via config without code changes
- **Structured Logging** — correlation IDs, per-worktree context, JSON output (`--log-format json`) for log aggregation
- **Diagnostics** — `owt doctor` finds orphaned worktrees/sessions/status entries; `owt config validate` checks config; `owt db health` reports database stats
- **7 dependencies** — click, pydantic, rich, textual, toml, gitpython, libtmux (+ optional `mcp`)

## Installation

### Requirements

- Python 3.10+
- Git
- tmux
- An AI coding tool (Claude Code, Pi, OpenCode, or Droid)

### Install from PyPI

```bash
pip install open-orchestrator

# With MCP peer communication (agent-to-agent messaging)
pip install open-orchestrator[mcp]
```

### Install from source

```bash
git clone https://github.com/gitpcl/openorchestrator.git
cd openorchestrator
uv pip install -e .

# With optional peer communication
uv pip install -e ".[mcp]"
```

## Quick Start

The whole loop happens inside one command. Launch the control plane and drive it from the keyboard:

```bash
owt
```

| Key | What it does |
|-----|--------------|
| `n` | **Start work** — type a task, pick how to run it (one worktree, or a native plan-first workflow), confirm |
| `↑ ↓` | Move between rows; the footer updates to show that row's available actions |
| `a` | Jump into the focused worktree's agent session |
| `s` | Ship the focused READY TO SHIP worktree (commit + merge + delete) |
| `f` / `m` | Fix conflicts · merge |
| `q` | Quit |

That's the daily workflow — you never have to remember a subcommand. The equivalent CLI verbs exist for scripts and CI:

```bash
owt new "Add user authentication with JWT"        # what 'n' → one worktree runs
owt new "Build the billing system" --workflow     # what 'n' → native plan-first workflow runs
owt new "Port the parser to Rust" --ai-tool droid # supervise a different provider in its own worktree
owt send auth-jwt "Fix the failing tests"          # message an agent
owt ship auth-jwt                                  # what 's' runs
```

See [docs/commands.md](docs/commands.md) for the full reference.

## Documentation

| Topic | Where |
|-------|-------|
| Full CLI reference, control plane, workflow patterns | [docs/commands.md](docs/commands.md) |
| Config file schema, env vars, AI tool support, MCP layer | [docs/configuration.md](docs/configuration.md) |
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
