# Multiplexer Backends (tmux / herdr)

How Open Orchestrator hosts agent sessions. For an overview, see the [README](../README.md). For full herdr protocol, troubleshooting, and named-session walkthroughs, see [herdr-integration.md](herdr-integration.md).

By default owt uses **tmux** to host agent sessions. You can opt in to **[herdr](https://herdr.dev)** as the multiplexer backend — owt becomes the orchestration brain, herdr the rendering surface. tmux remains the default; herdr is purely additive.

```bash
# one-off
owt new "Refactor billing" --herdr
owt attach my-feature --herdr

# project-wide via .worktreerc.toml
[backend]
mode = "auto"               # tmux | herdr | auto
herdr_session = "default"   # named herdr session (selects which socket)
```

## Selection Precedence

1. `--herdr` / `--tmux` on the command line (per invocation)
2. `[backend] mode` in `.worktreerc.toml`
3. `tmux` as the safe default

`mode = "auto"` picks herdr when installed and reachable, otherwise tmux. Status updates from owt's tracker are forwarded to herdr's sidebar via `pane.report_agent` (non-fatal — SQLite is source of truth).

## Per-Worktree Backend Records

**Recorded per worktree:** each status row carries `session_type` (`worktree` | `branch`), `backend_kind`, `backend_session_id`, and `backend_meta` (e.g. herdr `workspace_id` + `socket` + `herdr_session`) so `owt attach <name>`, `owt send <name>`, `owt switch <name>`, and `owt delete <name>` route to the right backend (and the right socket for custom herdr deployments) without re-passing flags.

**Force override on `owt attach`:** `--tmux` / `--herdr` re-resolve the session via `backend.session_for(name)` on the forced backend rather than coercing the recorded id (tmux session names and herdr pane ids are different shapes). If the forced backend has no session for that worktree, the command errors clearly instead of misrouting.

**Branch-mode parity:** `owt send`, `owt switch`, and `owt delete` all work on in-place branch sessions (created via `owt branch` / `owt new --in-place`) — they fall back to the status DB when `WorktreeManager.get` raises. `owt doctor` reconciles branch rows against the branch list, never against the worktree list.

**`--headless` + `[backend] mode = "herdr"`:** Headless launches skip backend resolution entirely (the detached subprocess never touches a multiplexer), so CI configurations that set `mode = "herdr"` keep working even without herdr installed.

**TUI prompt submission:** Herdr's `pane.send_text` doesn't synthesize a real Enter event, so owt routes every agent-facing message through `_send_line()` which appends `\r` by default (raw-mode TTY convention). Override per shell with `OWT_HERDR_SUBMIT=text:\r\n` or `OWT_HERDR_SUBMIT=keys:Enter` if your herdr build needs something different. See [`herdr-integration.md`](herdr-integration.md#agent-prompt-submission-tui-agents) for the full escape hatch.

## What owt sends to herdr

| owt action            | herdr RPC                              |
|-----------------------|----------------------------------------|
| `owt new --herdr`     | `workspace.create` + `pane.send_text`  |
| `owt send`            | `pane.send_text` (per worktree's recorded backend) |
| `owt attach --herdr`  | `herdr agent attach <pane_id>` (exec)  |
| status update (hooks) | `pane.report_agent` (non-fatal)        |
| `owt delete`          | `pane.close` + `workspace.close`       |

## Architecture

Call sites depend only on `core/multiplexer.py::MultiplexerBackend`; `AgentLauncher`, `commands/agent.py`, `commands/worktree.py`, and `commands/doctor.py` resolve a backend via `core/backend_factory.py` and never touch `TmuxManager` directly. Concrete adapters live behind `core/tmux_backend.py` (wraps `TmuxManager`) and `core/herdr_backend.py` (wraps `HerdrClient` JSON-RPC over Unix socket).

## Known Limitation

`owt orchestrate`, `owt batch`, `owt swarm`, and `owt subagent` create sessions through tmux directly today — they ignore `--herdr` and continue to use tmux even when `[backend] mode = "herdr"` is set. The standalone `owt new` flow is fully herdr-aware.

See [`herdr-integration.md`](herdr-integration.md) for the full configuration, troubleshooting, named-session walkthrough, and protocol reference.
