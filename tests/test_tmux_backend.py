"""Tests for TmuxBackend (multiplexer adapter)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.tmux_backend import TmuxBackend
from open_orchestrator.core.tmux_manager import TmuxSessionNotFoundError
from open_orchestrator.models.backend import BackendKind, BackendSession


@pytest.fixture
def mock_tmux() -> MagicMock:
    """Create a mock TmuxManager for TmuxBackend tests."""
    tmux = MagicMock()
    tmux.generate_session_name.side_effect = lambda name: f"owt-{name}"
    return tmux


@pytest.fixture
def backend(mock_tmux: MagicMock) -> TmuxBackend:
    return TmuxBackend(tmux=mock_tmux)


@pytest.fixture
def session() -> BackendSession:
    return BackendSession(kind=BackendKind.TMUX, id="owt-foo", worktree_name="foo")


class TestSessionFor:
    def test_returns_none_when_session_missing(self, backend: TmuxBackend, mock_tmux: MagicMock):
        mock_tmux.session_exists.return_value = False
        assert backend.session_for("foo") is None

    def test_returns_backend_session_when_present(self, backend: TmuxBackend, mock_tmux: MagicMock):
        mock_tmux.session_exists.return_value = True
        result = backend.session_for("foo")
        assert result is not None
        assert result.kind == BackendKind.TMUX
        assert result.id == "owt-foo"
        assert result.worktree_name == "foo"


class TestCreateSession:
    def test_delegates_to_tmux_manager(self, backend: TmuxBackend, mock_tmux: MagicMock):
        info = MagicMock()
        info.session_name = "owt-foo"
        mock_tmux.create_worktree_session.return_value = info

        result = backend.create_session(
            "foo",
            "/tmp/foo",
            agent_command="claude",
            plan_mode=True,
            automated=True,
        )

        assert result.kind == BackendKind.TMUX
        assert result.id == "owt-foo"
        assert result.worktree_name == "foo"
        mock_tmux.create_worktree_session.assert_called_once()
        kwargs = mock_tmux.create_worktree_session.call_args.kwargs
        assert kwargs["auto_start_ai"] is True
        assert kwargs["ai_tool"] == "claude"
        assert kwargs["plan_mode"] is True
        assert kwargs["automated"] is True

    def test_no_agent_command_defaults_to_claude(self, backend: TmuxBackend, mock_tmux: MagicMock):
        info = MagicMock()
        info.session_name = "owt-foo"
        mock_tmux.create_worktree_session.return_value = info

        backend.create_session("foo", "/tmp/foo")
        kwargs = mock_tmux.create_worktree_session.call_args.kwargs
        assert kwargs["auto_start_ai"] is False
        assert kwargs["ai_tool"] == "claude"


class TestGenerateSessionName:
    def test_delegates(self, backend: TmuxBackend, mock_tmux: MagicMock):
        assert backend.generate_session_name("foo") == "owt-foo"
        mock_tmux.generate_session_name.assert_called_with("foo")


class TestWaitAndPaste:
    def test_calls_wait_then_paste(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        backend.wait_and_paste(session, "hello", timeout=5)
        mock_tmux.wait_for_ai_ready.assert_called_once_with(session_name="owt-foo", timeout=5)
        mock_tmux.paste_to_pane.assert_called_once_with(session_name="owt-foo", text="hello")


class TestKill:
    def test_kills_session(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        backend.kill(session)
        mock_tmux.kill_session.assert_called_once_with("owt-foo")

    def test_swallows_not_found(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.kill_session.side_effect = TmuxSessionNotFoundError("gone")
        # Should not raise.
        backend.kill(session)


class TestIsAlive:
    def test_delegates(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.session_exists.return_value = True
        assert backend.is_alive(session) is True
        mock_tmux.session_exists.assert_called_with("owt-foo")


class TestSendText:
    def test_calls_send_keys_to_pane(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        backend.send_text(session, "ls")
        mock_tmux.send_keys_to_pane.assert_called_once_with("owt-foo", "ls")


class TestSendKeys:
    def test_raises_when_session_missing(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.session_exists.return_value = False
        with pytest.raises(TmuxSessionNotFoundError):
            backend.send_keys(session, "C-c")

    def test_invokes_tmux_send_keys(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.session_exists.return_value = True
        with patch("open_orchestrator.core.tmux_backend.subprocess.run") as mock_run:
            backend.send_keys(session, "Enter")
        assert mock_run.called
        argv = mock_run.call_args.args[0]
        assert argv[:4] == ["tmux", "send-keys", "-t", "owt-foo:0.0"]
        assert argv[-1] == "Enter"


class TestReadRecent:
    def test_returns_empty_when_session_missing(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.session_exists.return_value = False
        assert backend.read_recent(session) == ""

    def test_returns_stdout_on_success(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.session_exists.return_value = True
        completed = MagicMock()
        completed.stdout = "captured output"
        with patch("open_orchestrator.core.tmux_backend.subprocess.run", return_value=completed):
            assert backend.read_recent(session, lines=50) == "captured output"

    def test_returns_empty_on_called_process_error(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.session_exists.return_value = True
        with patch(
            "open_orchestrator.core.tmux_backend.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "tmux"),
        ):
            assert backend.read_recent(session) == ""

    def test_returns_empty_on_timeout(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.session_exists.return_value = True
        with patch(
            "open_orchestrator.core.tmux_backend.subprocess.run",
            side_effect=subprocess.TimeoutExpired("tmux", 3),
        ):
            assert backend.read_recent(session) == ""


class TestAttach:
    def test_switches_client_when_inside_tmux(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.is_inside_tmux.return_value = True
        backend.attach(session)
        mock_tmux.switch_client.assert_called_once_with("owt-foo")
        mock_tmux.attach.assert_not_called()

    def test_attaches_when_outside_tmux(self, backend: TmuxBackend, mock_tmux: MagicMock, session: BackendSession):
        mock_tmux.is_inside_tmux.return_value = False
        backend.attach(session)
        mock_tmux.attach.assert_called_once_with("owt-foo")
        mock_tmux.switch_client.assert_not_called()


class TestReportAgentState:
    def test_noop(self, backend: TmuxBackend, session: BackendSession):
        # No-op; just exercise the line.
        backend.report_agent_state(session, "idle", "ready")


class TestDefaultInit:
    def test_constructs_default_tmux_manager(self):
        with patch("open_orchestrator.core.tmux_backend.TmuxManager") as tm_cls:
            TmuxBackend()
            tm_cls.assert_called_once()
