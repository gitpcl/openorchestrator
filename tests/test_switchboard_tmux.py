"""Tests for ``open_orchestrator.core.switchboard_tmux``.

Cover terminal background detection, OSC-11 parsing, the session/keybinding
lifecycle in ``_install_switchboard_keys``, and the ``launch_switchboard``
control-flow branches. Every external boundary (``subprocess``, ``libtmux``,
``termios``/``tty``, environment variables, the Textual app) is mocked so the
tests are hermetic and never touch a real tmux server.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core import switchboard_tmux as st

# ---------------------------------------------------------------------------
# _resolve_worktree_from_session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        ("owt-feature-auth", "feature-auth"),
        ("owt-", ""),
        ("not-prefixed", None),
        ("", None),
    ],
)
def test_resolve_worktree_from_session(session: str, expected: str | None) -> None:
    assert st._resolve_worktree_from_session(session) == expected


# ---------------------------------------------------------------------------
# _shell_quote
# ---------------------------------------------------------------------------


def test_shell_quote_escapes_spaces_and_quotes() -> None:
    quoted = st._shell_quote("a b 'c'")
    # shlex.quote wraps in single quotes when the value contains specials.
    assert quoted.startswith("'") and quoted.endswith("'")
    # And the inner content is preserved (single quotes are escaped via ''\'').
    assert "a b" in quoted


# ---------------------------------------------------------------------------
# _parse_osc11_response
# ---------------------------------------------------------------------------


def test_parse_osc11_short_channels() -> None:
    # 2-digit channels — parsed as full bytes.
    assert st._parse_osc11_response("\033]11;rgb:1a/2b/3c\033\\") == "#1a2b3c"


def test_parse_osc11_long_channels() -> None:
    # 4-digit channels — high byte is taken.
    assert st._parse_osc11_response("\033]11;rgb:1a1a/2b2b/3c3c\033\\") == "#1a2b3c"


def test_parse_osc11_no_match_returns_none() -> None:
    assert st._parse_osc11_response("no color here") is None


# ---------------------------------------------------------------------------
# detect_terminal_background
# ---------------------------------------------------------------------------


def test_detect_terminal_background_returns_none_when_not_tty() -> None:
    with patch.object(st.sys.stdin, "isatty", return_value=False):
        assert st.detect_terminal_background() is None


def test_detect_terminal_background_returns_none_when_stdout_not_tty() -> None:
    with (
        patch.object(st.sys.stdin, "isatty", return_value=True),
        patch.object(st.sys.stdout, "isatty", return_value=False),
    ):
        assert st.detect_terminal_background() is None


def test_detect_terminal_background_handles_exception() -> None:
    # stdin/stdout look like TTYs but termios raises — function swallows.
    with (
        patch.object(st.sys.stdin, "isatty", return_value=True),
        patch.object(st.sys.stdout, "isatty", return_value=True),
        patch.object(st.sys.stdin, "fileno", side_effect=OSError("no fd")),
    ):
        assert st.detect_terminal_background() is None


# ---------------------------------------------------------------------------
# _is_inside_switchboard_session
# ---------------------------------------------------------------------------


def test_is_inside_switchboard_returns_false_when_tmux_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    assert st._is_inside_switchboard_session() is False


def test_is_inside_switchboard_true_when_session_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    fake = MagicMock()
    fake.stdout = f"{st.SWITCHBOARD_SESSION}\n"
    with patch.object(st.subprocess, "run", return_value=fake) as mock_run:
        assert st._is_inside_switchboard_session() is True
    args, kwargs = mock_run.call_args
    assert args[0] == ["tmux", "display-message", "-p", "#S"]
    assert kwargs["timeout"] == st.TMUX_TIMEOUT


def test_is_inside_switchboard_false_when_session_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    fake = MagicMock()
    fake.stdout = "owt-other-session\n"
    with patch.object(st.subprocess, "run", return_value=fake):
        assert st._is_inside_switchboard_session() is False


def test_is_inside_switchboard_handles_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    with patch.object(
        st.subprocess,
        "run",
        side_effect=subprocess.CalledProcessError(1, ["tmux"]),
    ):
        assert st._is_inside_switchboard_session() is False


def test_is_inside_switchboard_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    with patch.object(st.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)):
        assert st._is_inside_switchboard_session() is False


# ---------------------------------------------------------------------------
# _install_switchboard_keys
# ---------------------------------------------------------------------------


def _capture_install_calls(major: int, minor: int) -> list[list[str]]:
    """Run ``_install_switchboard_keys`` with a stubbed tmux version and capture
    the first positional argument (the argv) passed to every ``subprocess.run``
    invocation.
    """
    with (
        patch.object(st.subprocess, "run") as mock_run,
        patch.object(st.TmuxManager, "get_tmux_version", return_value=(major, minor)),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        st._install_switchboard_keys()
    return [list(call.args[0]) for call in mock_run.call_args_list]


def test_install_keys_binds_all_modern_tmux_shortcuts() -> None:
    calls = _capture_install_calls(3, 2)

    # Always starts with an Alt+b unbind to clear the legacy mapping.
    assert calls[0] == ["tmux", "unbind-key", "-n", "M-b"]

    # Each of Alt+c / Alt+s / Alt+m / Alt+d should be bound exactly once.
    bound_keys: dict[str, list[str]] = {}
    for argv in calls:
        if len(argv) >= 4 and argv[1] == "bind-key" and argv[2] == "-n":
            bound_keys.setdefault(argv[3], argv)

    assert set(bound_keys) == {"M-c", "M-s", "M-m", "M-d"}

    # Alt+s switches back to the switchboard session.
    assert "switch-client" in bound_keys["M-s"]
    assert st.SWITCHBOARD_SESSION in bound_keys["M-s"]

    # Modern tmux (>=3.2) uses display-popup for create/merge/delete.
    for key in ("M-c", "M-m", "M-d"):
        assert "display-popup" in bound_keys[key], f"{key} should use display-popup"


def test_install_keys_falls_back_to_new_window_on_old_tmux() -> None:
    calls = _capture_install_calls(3, 1)

    bound_keys: dict[str, list[str]] = {}
    for argv in calls:
        if len(argv) >= 4 and argv[1] == "bind-key" and argv[2] == "-n":
            bound_keys.setdefault(argv[3], argv)

    assert set(bound_keys) == {"M-c", "M-s", "M-m", "M-d"}
    # Old tmux uses new-window instead of display-popup.
    for key in ("M-c", "M-m", "M-d"):
        assert "new-window" in bound_keys[key], f"{key} should use new-window"
        assert "display-popup" not in bound_keys[key]


def test_install_keys_passes_timeout_on_every_call() -> None:
    with (
        patch.object(st.subprocess, "run") as mock_run,
        patch.object(st.TmuxManager, "get_tmux_version", return_value=(3, 2)),
    ):
        st._install_switchboard_keys()

    for call in mock_run.call_args_list:
        assert call.kwargs.get("timeout") == st.TMUX_TIMEOUT
        assert call.kwargs.get("check") is False


# ---------------------------------------------------------------------------
# launch_switchboard
# ---------------------------------------------------------------------------


def test_launch_runs_textual_app_when_already_inside_switchboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWT_BACKGROUND", "#101010")

    fake_app = MagicMock()
    fake_module = MagicMock()
    fake_module.SwitchboardApp = MagicMock(return_value=fake_app)

    with (
        patch.object(st, "_is_inside_switchboard_session", return_value=True),
        patch.dict(
            "sys.modules",
            {"open_orchestrator.core.switchboard": fake_module},
        ),
    ):
        st.launch_switchboard()

    fake_module.SwitchboardApp.assert_called_once_with(detected_bg="#101010")
    fake_app.run.assert_called_once()


def test_launch_creates_session_and_switches_when_inside_other_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside switchboard but inside another tmux: create session, install keys, switch."""
    monkeypatch.delenv("OWT_BACKGROUND", raising=False)

    fake_tmux = MagicMock()
    fake_tmux.session_exists.return_value = False
    fake_tmux.is_inside_tmux.return_value = True

    with (
        patch.object(st, "_is_inside_switchboard_session", return_value=False),
        patch.object(st, "detect_terminal_background", return_value="#1a1a1a"),
        patch.object(st, "TmuxManager", return_value=fake_tmux),
        patch.object(st, "_install_switchboard_keys") as mock_install,
        patch.object(st.subprocess, "run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        st.launch_switchboard()

    fake_tmux.session_exists.assert_called_once_with(st.SWITCHBOARD_SESSION)
    mock_install.assert_called_once()

    argvs = [list(call.args[0]) for call in mock_run.call_args_list]
    # set-environment for OWT_BACKGROUND (background was detected).
    assert ["tmux", "set-environment", "-g", "OWT_BACKGROUND", "#1a1a1a"] in argvs
    # New session created with switchboard name.
    assert any(av[:3] == ["tmux", "new-session", "-d"] and st.SWITCHBOARD_SESSION in av for av in argvs)
    # switch-client because we're inside tmux already.
    assert ["tmux", "switch-client", "-t", st.SWITCHBOARD_SESSION] in argvs
    # And we did NOT attach (that's the bare-terminal branch).
    assert not any("attach-session" in av for av in argvs)


def test_launch_attaches_when_outside_tmux_and_session_already_exists() -> None:
    """Outside tmux, session already exists: no set-environment, no new-session,
    no switch-client — only attach-session."""
    fake_tmux = MagicMock()
    fake_tmux.session_exists.return_value = True
    fake_tmux.is_inside_tmux.return_value = False

    with (
        patch.object(st, "_is_inside_switchboard_session", return_value=False),
        patch.object(st, "detect_terminal_background", return_value=None),
        patch.object(st, "TmuxManager", return_value=fake_tmux),
        patch.object(st, "_install_switchboard_keys") as mock_install,
        patch.object(st.subprocess, "run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        st.launch_switchboard()

    mock_install.assert_called_once()
    argvs = [list(call.args[0]) for call in mock_run.call_args_list]
    # No new-session call since session already exists.
    assert not any("new-session" in av for av in argvs)
    # No set-environment because background detection returned None.
    assert not any("set-environment" in av for av in argvs)
    # Final call attaches.
    assert ["tmux", "attach-session", "-t", st.SWITCHBOARD_SESSION] in argvs
    # Attach uses timeout=None to allow blocking.
    attach_call = next(c for c in mock_run.call_args_list if list(c.args[0])[:2] == ["tmux", "attach-session"])
    assert attach_call.kwargs.get("timeout") is None


def test_launch_skips_set_environment_when_background_not_detected() -> None:
    """When background detection fails, no OWT_BACKGROUND propagation."""
    fake_tmux = MagicMock()
    fake_tmux.session_exists.return_value = False
    fake_tmux.is_inside_tmux.return_value = False

    with (
        patch.object(st, "_is_inside_switchboard_session", return_value=False),
        patch.object(st, "detect_terminal_background", return_value=None),
        patch.object(st, "TmuxManager", return_value=fake_tmux),
        patch.object(st, "_install_switchboard_keys"),
        patch.object(st.subprocess, "run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        st.launch_switchboard()

    argvs = [list(call.args[0]) for call in mock_run.call_args_list]
    assert not any("set-environment" in av for av in argvs)
    # But new-session was still called.
    assert any("new-session" in av for av in argvs)
