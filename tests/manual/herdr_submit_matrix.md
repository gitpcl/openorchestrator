# Herdr Prompt Submission Matrix (Manual)

**Status:** Not yet run. The default terminator (`text:\r`) is chosen
based on raw-mode TTY convention, not empirical observation against a
live herdr build.

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
| Carriage return    | `text:\r`                |          |              |             |
| CRLF               | `text:\r\n`              |          |              |             |
| Named Enter        | `keys:Enter`             |          |              |             |
| Named Return       | `keys:Return`            |          |              |             |
| Ctrl-M             | `keys:C-m`               |          |              |             |
| Raw `\r` via keys  | `keys:\r`                |          |              |             |
| Bare line feed     | `text:\n`                |          |              |             |

Mark `Y` (submitted on first attempt), `N` (still in input box), `—`
(agent not installed / didn't apply).

## Lock-in

Once filled in:

1. Pick the **first variant in the table above** that scores `Y` across
   all installed TUI agents.
2. Update the defaults in `src/open_orchestrator/core/herdr_backend.py`:
   * For a `text:<term>` winner — set `_DEFAULT_SUBMIT_MODE = "text"` and
     `_DEFAULT_SUBMIT_TERMINATOR = <term>`.
   * For a `keys:<key>` winner — set `_DEFAULT_SUBMIT_MODE = "keys"` and
     `_DEFAULT_SUBMIT_TERMINATOR = <key>`.
3. Update the matching unit tests in `tests/test_herdr_backend.py`
   (`test_send_text_default_appends_carriage_return` and friends) so the
   defaults stay locked in.
4. Update the "Agent prompt submission" section of
   `docs/herdr-integration.md` to describe the locked-in default and
   move this file to "completed" status (or delete it once herdr's
   behavior is stable across builds).

If **no** variant submits across all three agents, fill in this section
with what you observed and open a GitHub issue — that means herdr's
`pane.send_text` / `pane.send_keys` semantics differ from what raw-mode
PTY normally accepts, and we need a longer conversation with herdr's
maintainers about the right path forward.

## Acceptance

Sprint 026 P6a is considered complete only when:

- [ ] The matrix above is fully filled in against a known herdr build.
- [ ] `_DEFAULT_SUBMIT_MODE` / `_DEFAULT_SUBMIT_TERMINATOR` are locked
      to the empirical winner.
- [ ] `docs/herdr-integration.md` documents the locked-in default.
- [ ] All three TUI agents submit on the first attempt with no
      `OWT_HERDR_SUBMIT` override set.
