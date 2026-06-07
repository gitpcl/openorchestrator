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

Each status row carries four fields written at create-time so later
commands route correctly without re-passing flags:

| Column                | Meaning                                                  |
|-----------------------|----------------------------------------------------------|
| `session_type`        | `"worktree"` (default) or `"branch"` (in-place branch session) |
| `backend_kind`        | `"tmux"` or `"herdr"` — picked by the launcher           |
| `backend_session_id`  | tmux session name OR herdr pane id                        |
| `backend_meta`        | JSON with `workspace_id`, `socket`, and `herdr_session` (herdr) |

`owt attach <name>`, `owt send <name> "msg"`, `owt switch <name>`, and
`owt delete <name>` look at the recorded row to dispatch to the right
adapter — including the exact herdr socket path. Use `--herdr` /
`--tmux` only when you want to override what was recorded.

### `--tmux` / `--herdr` on `owt attach`

Forcing a backend re-resolves the session via that backend's
`session_for(name)` lookup rather than coercing the recorded id (the
id formats are different between tmux and herdr — coercing would
silently misroute). If the forced backend has no session for the
worktree, the command errors with a clear message instead of failing
later in the attach handshake.

## Branch-mode parity

Branch-mode sessions (`owt branch …` / `owt new --in-place …`) live
only in the status DB — there's no separate worktree on disk. Sprint
026 P5 made them first-class across the CLI:

| Command            | Branch-mode behavior                                    |
|--------------------|---------------------------------------------------------|
| `owt send <name>`  | Falls back to status DB when `WorktreeManager.get` raises |
| `owt switch <name>`| Same — attaches via the recorded backend                  |
| `owt delete <name>`| Routes through branch teardown: `delete_branch=True`, `pop_stash=True` |
| `owt doctor`       | Reconciles branch rows against `git branch --list`, never against the worktree list |

## `owt new --headless` with `[backend] mode = "herdr"`

Headless mode never touches a multiplexer — it runs a detached
subprocess and exits. Sprint 026 P6 made the launcher skip backend
resolution entirely on the headless path, so a CI configuration of
`[backend] mode = "herdr"` no longer fails when herdr isn't
installed:

```bash
# Works even with no herdr installed.
owt new "Run my batch job" --headless
```

## Agent prompt submission (TUI agents)

Herdr's `pane.send_text` types text into the pane *as text* — it
doesn't synthesize a real Enter key event. For TUI agents (pi, claude
in TUI mode, droid) that's a problem: they read raw stdin and treat a
literal `\n` as "newline in input", not "submit". So owt routes every
agent-facing message through a single chokepoint
(`HerdrBackend._send_line()`) that delivers Enter as a carriage
return.

```bash
# Default — sends `<body>\r` via pane.send_text. Matches what a
# physical Enter key delivers to stdin in raw mode.
owt new "Build the thing" --herdr
```

### `OWT_HERDR_SUBMIT` escape hatch

If your herdr build needs a different terminator, override per shell:

```bash
# CRLF terminator embedded in pane.send_text.
export OWT_HERDR_SUBMIT='text:\r\n'

# Body via pane.send_text, then a real key event via pane.send_keys.
export OWT_HERDR_SUBMIT='keys:Enter'      # most TUIs
export OWT_HERDR_SUBMIT='keys:Return'     # some keymaps name it Return
export OWT_HERDR_SUBMIT='keys:C-m'        # the literal Enter byte
```

Unknown values (e.g. `OWT_HERDR_SUBMIT=foo:bar`) log a warning and
fall back to the default. `\r` / `\n` escapes are expanded so the
variable stays readable in a shell.

### Empirical matrix (manual verification)

The default terminator (`text:\r`) is **empirically verified** against
herdr **v0.6.8**: pi and claude both submit the typed prompt on the
first attempt with no `OWT_HERDR_SUBMIT` override
(`HerdrBackend.submit_prompt` confirms the pane leaves `idle`). The
bare-line-feed variant (`text:\n`) does **not** submit — that is the bug
the `_send_line` chokepoint fixes. droid was not exercised (no active
subscription on the test host); the submission mechanism is
agent-agnostic, so the result is expected to carry over. See
[`tests/manual/herdr_submit_matrix.md`](../tests/manual/herdr_submit_matrix.md)
for the full table and procedure. If a future herdr build needs a
different working default, re-run the matrix and open an issue with the
results.

## Known limitations

- **Plan mode + automated mode are honored only by tmux** (they are
  propagated via the agent command and `OWT_AUTOMATED=1` env var, which
  herdr's `pane.send_text` doesn't currently set on the pane shell).

## Out of scope

- herdr triggering owt commands (the socket allows this — owt does not
  consume incoming triggers today).
- Bundling herdr — install it yourself from herdr.dev.
