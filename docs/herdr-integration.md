# Herdr Multiplexer Backend

Sprint 025 added [herdr](https://herdr.dev) as an optional multiplexer backend.
owt remains the orchestration brain; herdr becomes the rendering surface
when you opt in.

## tl;dr

```bash
# Default — uses tmux, no changes from before
owt new "Refactor billing"

# Use herdr instead (one-off)
owt new "Refactor billing" --herdr

# Make herdr the default in this repo
cat >> .worktreerc.toml <<'EOF'
[backend]
mode = "herdr"        # tmux | herdr | auto
herdr_session = "default"
EOF

# Auto-detect — herdr if installed and its socket is alive, tmux otherwise
[backend]
mode = "auto"
```

## What changes when you opt in

| Aspect                | tmux (default)                | herdr backend                          |
|-----------------------|-------------------------------|----------------------------------------|
| Default UX            | Unchanged                     | Unchanged for everyone else            |
| Multiplexer UI        | tmux conventions              | herdr's window manager + sidebar       |
| Mouse-native          | No                            | Yes                                    |
| Agent state in UI     | Status DB only                | Status DB + sidebar via `pane.report_agent` |
| Remote attach         | `tmux attach`                 | `herdr --remote`                       |
| Install footprint     | tmux only                     | tmux *or* herdr (still optional)        |

## Selection precedence

1. `--herdr` / `--tmux` on the command line (per invocation)
2. `[backend] mode` in `.worktreerc.toml`
3. `tmux` as the safe default

`mode = "auto"` reaches for herdr first (via `which herdr` + `HerdrClient.ping()`)
and falls back to tmux when herdr isn't reachable.

## Configuration

```toml
# .worktreerc.toml
[backend]
mode          = "auto"      # tmux | herdr | auto
herdr_session = "default"   # named herdr session (selects which socket)
# herdr_socket = "/custom/path/to/herdr.sock"  # only if you've moved the socket
```

The named session resolves to:

- `default` → `$XDG_CONFIG_HOME/herdr/herdr.sock`
- any other name → `$XDG_CONFIG_HOME/herdr/sessions/<name>/herdr.sock`

## What owt sends to herdr

Each owt worktree maps to one herdr workspace; the agent runs in that
workspace's root pane.

| owt action            | herdr RPC                                |
|-----------------------|------------------------------------------|
| `owt new --herdr`     | `workspace.create` + `pane.send_text`    |
| `owt send`            | `pane.send_text`                         |
| `owt attach --herdr`  | `herdr agent attach <pane_id>`           |
| status update         | `pane.report_agent` (non-fatal)          |
| `owt delete`          | `pane.close` + `workspace.close`         |

Status forwarding is best-effort: SQLite is always source of truth.
If herdr is down, owt keeps working — the sidebar just falls behind.

## Troubleshooting

**`herdr is not installed or its socket is not reachable`**
- Install: `curl https://herdr.dev/install.sh | sh`
- Verify the daemon is running: `herdr status`
- Confirm the socket: `ls $XDG_CONFIG_HOME/herdr/herdr.sock`
- If you use a named session, point `[backend] herdr_session` at it

**`--herdr is incompatible with --headless`**
- Headless mode has no terminal to host. Use `--tmux` (default) for CI.

**Multiple herdr daemons** (e.g. work + personal)
- `[backend] herdr_session = "work"` selects
  `~/.config/herdr/sessions/work/herdr.sock`.

**Falling back to tmux temporarily**
- `--tmux` on any command overrides config.
- Or set `OWT_BACKEND_MODE=tmux` in the environment for a single shell.

## Architecture

```
                   ┌─────────────────────────────┐
                   │   commands/ (owt CLI)       │
                   └──────────────┬──────────────┘
                                  │
                  ┌───────────────▼─────────────────┐
                  │ MultiplexerBackend  (protocol)  │
                  └───────────────┬─────────────────┘
                                  │
              ┌───────────────────┴─────────────────────┐
              │                                         │
   ┌──────────▼──────────┐                  ┌───────────▼────────────┐
   │ TmuxBackend         │                  │ HerdrBackend           │
   │  → TmuxManager      │                  │  → HerdrClient (RPC)   │
   └─────────────────────┘                  └────────────────────────┘
```

Call sites depend only on `MultiplexerBackend`; concrete adapters live
behind `TmuxBackend` and `HerdrBackend`. The factory at
`core/backend_factory.py` is the single resolution point.

## Status DB vs herdr sidebar

owt writes the canonical state into SQLite (`~/.open-orchestrator/status.db`).
With herdr enabled, the tracker *also* calls
`backend.report_agent_state(session, state, message)` after each write so
herdr's sidebar reflects the same picture. If that call fails, the
SQLite write is unaffected and the control plane (`owt`) still works.

## What gets recorded per worktree

Each status row carries three fields written at create-time so later
commands route correctly without re-passing flags:

| Column                | Meaning                                                  |
|-----------------------|----------------------------------------------------------|
| `backend_kind`        | `"tmux"` or `"herdr"` — picked by the launcher           |
| `backend_session_id`  | tmux session name OR herdr pane id                        |
| `backend_meta`        | JSON with backend-specific extras (e.g. `workspace_id`)  |

`owt attach <name>`, `owt send <name> "msg"`, and `owt delete <name>`
all look at `backend_kind` (then `backend_session_id`) to dispatch to
the right adapter. Use `--herdr` / `--tmux` only when you want to
override what was recorded.

## Known limitations

- **Orchestrator and batch runs are tmux-only today.** `owt orchestrate`,
  `owt batch`, `owt swarm`, and `owt subagent` create sessions through
  `TmuxManager` directly because their pane-split / coordinator-worker
  topology has no direct herdr analogue yet. They are unaffected by
  `--herdr` and will continue to create tmux sessions even when
  `[backend] mode = "herdr"` is set.
- **Plan mode + automated mode are honored only by tmux** (they are
  propagated via the agent command and `OWT_AUTOMATED=1` env var, which
  herdr's `pane.send_text` doesn't currently set on the pane shell).
- The `switchboard` (`owt --legacy-cards`) hosts its *own* tmux session;
  this is unaffected by the backend choice for agent sessions.

## Out of scope (not in this sprint)

- Pushing DAG / orchestration progress into herdr's UI (could be a follow-up).
- herdr triggering owt commands (the socket allows this — owt does not
  consume incoming triggers today).
- Bundling herdr — install it yourself from herdr.dev.
