"""Sprint 025: ``owt new`` and ``owt attach`` flag wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main


def test_owt_new_advertises_herdr_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["new", "--help"])
    assert result.exit_code == 0
    assert "--herdr" in result.output
    assert "--tmux" in result.output


def test_owt_attach_advertises_herdr_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["attach", "--help"])
    assert result.exit_code == 0
    assert "--herdr" in result.output
    assert "--tmux" in result.output


def test_herdr_and_tmux_are_mutually_exclusive_on_new() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["new", "Test task", "--yes", "--herdr", "--tmux"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_herdr_and_tmux_are_mutually_exclusive_on_attach() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["attach", "wt", "--herdr", "--tmux"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_owt_new_herdr_fails_clearly_when_unreachable() -> None:
    runner = CliRunner()
    with patch("open_orchestrator.core.backend_factory.detect_herdr", return_value=False):
        result = runner.invoke(main, ["new", "Test task", "--yes", "--herdr"])
    assert result.exit_code != 0
    assert "herdr" in result.output.lower()


def test_owt_new_herdr_incompatible_with_headless() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["new", "Test", "--yes", "--herdr", "--headless"])
    assert result.exit_code != 0
    assert "incompatible" in result.output.lower() or "headless" in result.output.lower()


def test_owt_attach_invokes_backend_attach(tmp_path) -> None:  # noqa: ANN001
    """When attach succeeds, ``backend.attach`` is the only path called."""
    from open_orchestrator.models.backend import BackendKind, BackendSession

    runner = CliRunner()
    fake_backend = MagicMock()
    fake_backend.kind.value = "tmux"
    recorded = BackendSession(kind=BackendKind.TMUX, id="owt-wt", worktree_name="wt")

    wt_info = MagicMock()
    wt_info.name = "wt"
    wt_info.is_main = False
    wt_info.branch = "feat/x"

    with (
        patch("open_orchestrator.core.backend_factory.select_backend_for_session", return_value=fake_backend),
        patch("open_orchestrator.commands.worktree.get_worktree_manager") as wt_get,
        patch("open_orchestrator.commands.worktree.get_status_tracker") as tr_get,
    ):
        wt_get.return_value.get.return_value = wt_info
        tr_get.return_value.get_status.return_value = MagicMock(session_type="worktree")
        tr_get.return_value.get_backend_session.return_value = recorded
        result = runner.invoke(main, ["attach", "wt"])
    # The DB-recorded session is preferred over session_for(), and
    # routed via select_backend_for_session so socket_path survives.
    fake_backend.attach.assert_called_once_with(recorded)
    del result  # exit code from mocked attach is not meaningful


def _wt(name: str) -> MagicMock:
    info = MagicMock()
    info.name = name
    info.is_main = False
    info.branch = f"feat/{name}"
    return info


class TestAttachForcedOverrideReResolution:
    """Sprint 026 P4: forced --tmux/--herdr re-resolves via the forced backend
    rather than coercing the recorded session id."""

    def test_force_tmux_on_herdr_recorded_re_resolves(self) -> None:
        """``--tmux`` on a herdr-recorded session re-resolves via tmux."""
        from open_orchestrator.models.backend import BackendKind, BackendSession

        runner = CliRunner()
        recorded = BackendSession(kind=BackendKind.HERDR, id="pane-9", worktree_name="wt")
        forced_tmux = MagicMock()
        forced_tmux.kind.value = "tmux"
        # Forced backend finds a tmux session for this worktree.
        forced_tmux.session_for.return_value = BackendSession(kind=BackendKind.TMUX, id="owt-wt", worktree_name="wt")

        with (
            patch("open_orchestrator.core.backend_factory.select_backend", return_value=forced_tmux),
            patch("open_orchestrator.commands.worktree.get_worktree_manager") as wt_get,
            patch("open_orchestrator.commands.worktree.get_status_tracker") as tr_get,
        ):
            wt_get.return_value.get.return_value = _wt("wt")
            tr_get.return_value.get_status.return_value = MagicMock(session_type="worktree")
            tr_get.return_value.get_backend_session.return_value = recorded
            result = runner.invoke(main, ["attach", "wt", "--tmux"])

        # The forced tmux backend was asked for a session under that name —
        # never given the herdr pane id.
        forced_tmux.session_for.assert_called_once_with("wt")
        forced_tmux.attach.assert_called_once()
        # Attached session is the tmux one, not the herdr one.
        attached = forced_tmux.attach.call_args.args[0]
        assert attached.kind == BackendKind.TMUX
        assert attached.id == "owt-wt"
        del result

    def test_force_tmux_without_alt_session_errors_clearly(self) -> None:
        """When the forced backend has no session, raise with a clear message."""
        from open_orchestrator.models.backend import BackendKind, BackendSession

        runner = CliRunner()
        recorded = BackendSession(kind=BackendKind.HERDR, id="pane-9", worktree_name="wt")
        forced_tmux = MagicMock()
        forced_tmux.kind.value = "tmux"
        forced_tmux.session_for.return_value = None  # no tmux session exists

        with (
            patch("open_orchestrator.core.backend_factory.select_backend", return_value=forced_tmux),
            patch("open_orchestrator.commands.worktree.get_worktree_manager") as wt_get,
            patch("open_orchestrator.commands.worktree.get_status_tracker") as tr_get,
        ):
            wt_get.return_value.get.return_value = _wt("wt")
            tr_get.return_value.get_status.return_value = MagicMock(session_type="worktree")
            tr_get.return_value.get_backend_session.return_value = recorded
            result = runner.invoke(main, ["attach", "wt", "--tmux"])

        assert result.exit_code != 0
        # Error must name both the forced backend and the recorded one so
        # the user can correct the flag.
        assert "tmux" in result.output.lower()
        assert "herdr" in result.output.lower()
        forced_tmux.attach.assert_not_called()

    def test_force_herdr_on_tmux_recorded_re_resolves(self) -> None:
        """Mirror: ``--herdr`` on a tmux-recorded session re-resolves via herdr."""
        from open_orchestrator.models.backend import BackendKind, BackendSession

        runner = CliRunner()
        recorded = BackendSession(kind=BackendKind.TMUX, id="owt-wt", worktree_name="wt")
        forced_herdr = MagicMock()
        forced_herdr.kind.value = "herdr"
        forced_herdr.session_for.return_value = BackendSession(kind=BackendKind.HERDR, id="pane-3", worktree_name="wt")

        with (
            patch("open_orchestrator.core.backend_factory.select_backend", return_value=forced_herdr),
            patch("open_orchestrator.commands.worktree.get_worktree_manager") as wt_get,
            patch("open_orchestrator.commands.worktree.get_status_tracker") as tr_get,
        ):
            wt_get.return_value.get.return_value = _wt("wt")
            tr_get.return_value.get_status.return_value = MagicMock(session_type="worktree")
            tr_get.return_value.get_backend_session.return_value = recorded
            result = runner.invoke(main, ["attach", "wt", "--herdr"])

        forced_herdr.session_for.assert_called_once_with("wt")
        forced_herdr.attach.assert_called_once()
        attached = forced_herdr.attach.call_args.args[0]
        assert attached.kind == BackendKind.HERDR
        assert attached.id == "pane-3"
        del result
