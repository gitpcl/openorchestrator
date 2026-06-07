# Herdr Prompt Submission Matrix (Manual)

**Status:** Completed against herdr **v0.6.8** (daemon protocol 12) on
2026-06-07. The default terminator (`text:\r`, delivered as body and a
standalone `\r` via two separate `pane.send_text` calls — see
`HerdrBackend._send_line`) submits the typed prompt on the **first
attempt** for every testable TUI agent. The default is locked in;
`OWT_HERDR_SUBMIT` remains as the per-deployment escape hatch.

**Purpose:** Determine which value of `OWT_HERDR_SUBMIT` makes
`pane.send_text` / `pane.send_keys` deliver what each TUI agent
interprets as a *real Enter key event* — i.e. submits the typed prompt
rather than leaving a literal newline in the input box.

## Setup

Before running:

1. Install a current build of [herdr](https://herdr.dev) and confirm
   `herdr status` reports the daemon up.
2. Confirm `which claude`, `which pi`, and `which droid` all resolve.
   Skip rows for any agent not installed.
3. From an owt checkout: `uv pip install -e .`.

## Procedure (per row)

For each combination of agent × `OWT_HERDR_SUBMIT` value:

```bash
# 1. Reset state.
owt delete matrix-row 2>/dev/null || true

# 2. Export the variant under test.
export OWT_HERDR_SUBMIT='<variant>'   # see table below

# 3. Launch a fresh herdr workspace via owt.
owt new "verify submit works for <agent>" \
  --ai-tool <agent> --herdr --yes --branch matrix-row

# 4. Observe the agent pane in herdr. Did the prompt submit on its
#    own, or is it sitting in the input box waiting for Enter?
#    Record YES (submitted) or NO (still in input box).

# 5. Tear down.
owt delete matrix-row --yes
unset OWT_HERDR_SUBMIT
```

## Matrix to fill in

| Variant            | `OWT_HERDR_SUBMIT` value | pi (TUI) | claude (TUI) | droid (TUI) |
|--------------------|--------------------------|:--------:|:------------:|:-----------:|
| Carriage return    | `text:\r`                |    Y     |      Y       |      —      |
| CRLF               | `text:\r\n`              |    Y     |      Y       |      —      |
| Named Enter        | `keys:Enter`             |    —     |      —       |      —      |
| Named Return       | `keys:Return`            |    —     |      —       |      —      |
| Ctrl-M             | `keys:C-m`               |    —     |      —       |      —      |
| Raw `\r` via keys  | `keys:\r`                |    —     |      —       |      —      |
| Bare line feed     | `text:\n`                |    N     |      N       |      —      |

Mark `Y` (submitted on first attempt), `N` (still in input box), `—`
(agent not installed / didn't apply).

### Empirical notes (herdr v0.6.8, 2026-06-07)

- **`text:\r` (default) — `Y` for pi and claude.** `submit_prompt`
  reported `confirmed_submitted=True`: the pane's `agent_status` left
  `idle` on the first send, i.e. the agent accepted the prompt. This is
  the locked-in default.
- **`text:\r\n` — also `Y`** for pi and claude; the extra `\n` is
  harmless. Kept as an `OWT_HERDR_SUBMIT=text:\r\n` escape hatch.
- **`text:\n` (bare line feed) — `N`.** This is the original bug: the
  body lands in the input box but the line feed is treated as a literal
  newline-in-input, never a submit. It is why the two-call `body` + `\r`
  chokepoint exists.
- **`keys:*` rows — not exercised.** On herdr v0.6.8 `pane.send_keys`
  times out (same as v0.6.1), so the `keys:` path is only reachable via
  the `OWT_HERDR_SUBMIT=keys:<key>` override and falls back to a
  standalone `\r` on timeout. Left `—`; not needed because `text:\r`
  already wins.
- **droid — `—` (untestable).** No active droid subscription on the
  test host, so the agent never authenticates and never leaves `idle`;
  there is no agent to observe a submit against. Re-run this row once a
  subscription is available. The submission mechanism is agent-agnostic
  (a real Enter event to a raw-mode PTY), so the pi + claude result is
  expected to carry over.

## Lock-in (done)

Winner: **`text:\r`** — the first variant in the table, scoring `Y`
across every testable TUI agent (pi, claude; droid untestable — no
subscription).

1. ✅ First `Y`-across-the-board variant is `text:\r`.
2. ✅ Defaults in `src/open_orchestrator/core/herdr_backend.py` are
   `_DEFAULT_SUBMIT_MODE = "text"` and `_DEFAULT_SUBMIT_TERMINATOR = "\r"`.
3. ✅ `tests/test_herdr_backend.py` locks the default in
   (`test_send_text_default_sends_body_then_separate_cr` asserts the
   exact `["<body>", "\r"]` request stream) plus the `text:\r\n` and
   `keys:Enter` override streams.
4. ✅ `docs/herdr-integration.md` → "Agent prompt submission (TUI
   agents)" documents the locked-in default and the escape hatch.

If a future herdr build regresses (no variant submits across agents),
re-run this matrix, record observations here, and open a GitHub issue —
that means herdr's `pane.send_text` / `pane.send_keys` semantics drifted
from what a raw-mode PTY accepts as Enter.

## Acceptance

Sprint 026 P6a is complete:

- [x] The matrix above is filled in against herdr v0.6.8.
- [x] `_DEFAULT_SUBMIT_MODE` / `_DEFAULT_SUBMIT_TERMINATOR` are locked to
      the empirical winner (`text` / `\r`).
- [x] `docs/herdr-integration.md` documents the locked-in default.
- [x] Every testable TUI agent (pi, claude) submits on the first attempt
      with no `OWT_HERDR_SUBMIT` override set. droid is deferred until a
      subscription is available (untestable, not a failure of the
      default).
