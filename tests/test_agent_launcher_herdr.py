"""Sprint 025 P7 — end-to-end ``owt new --herdr`` integration test.

The earlier sprint tests assert flag parsing and unit-boundary contracts.
Those checks pass even when ``--herdr`` is wired only as advisory and
the real spawn path still goes through tmux. This test guards against
that regression by asserting:

1. The herdr backend's ``create_session`` is the one that fires (not
   :class:`TmuxManager.create_worktree_session`).
2. The status DB row written by the launcher carries ``backend_kind
   = 'herdr'`` and the pane id reported by the fake herdr socket.
3. No tmux session is created as a side effect.

The fake herdr socket is the same flavor used in
``tests/test_herdr_backend.py`` so we can run without a real herdr
process.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest
from open_orchestrator.core.herdr_backend import HerdrBackend
from open_orchestrator.core.status import StatusConfig, StatusTracker
from open_orchestrator.models.backend import BackendKind
from open_orchestrator.models.worktree_info import SessionType

# ── fake herdr socket ────────────────────────────────────────────────


async def _serve(sock: Path, calls: list[dict]) -> asyncio.AbstractServer:
    """Start a fake herdr socket that records every call."""

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                payload = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            calls.append(payload)
            method = payload.get("method")
            if method == "workspace.create":
                result: dict | bool = {"workspace_id": "ws-1", "root_pane_id": "pane-herdr-1"}
            else:
                result = True
            writer.write((json.dumps({"id": payload["id"], "result": result}) + "\n").encode("utf-8"))
            await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(_handler, path=str(sock))


@pytest.fixture
async def fake_herdr(herdr_socket_path: Path) -> AsyncIterator[tuple[Path, list[dict]]]:
    calls: list[dict] = []
    server = await _serve(herdr_socket_path, calls)
    try:
        yield herdr_socket_path, calls
    finally:
        server.close()
        await server.wait_closed()


# ── helpers ──────────────────────────────────────────────────────────


class _FakeTool:
    name = "claude"
    binary = "claude"
    supports_hooks = False
    supports_headless = True
    supports_plan_mode = True
    task_via_args = False
    install_hint = ""

    def get_command(self, *, executable_path=None, plan_mode=False, prompt=None, worktree=None):  # noqa: ANN001
        return "claude"

    def is_installed(self) -> bool:
        return True

    def get_known_paths(self):
        return []

    def install_hooks(self, *_a, **_k) -> bool:  # noqa: ANN002, ANN003
        return False


def _make_launcher(tmp_path: Path, backend: HerdrBackend) -> AgentLauncher:
    worktree = SimpleNamespace(name="wt-feat", path=tmp_path / "wt-feat", branch="feat/wt")
    (tmp_path / "wt-feat").mkdir(exist_ok=True)

    wt_manager = MagicMock()
    wt_manager.list_all.return_value = []
    wt_manager.create.return_value = worktree
    wt_manager.git_root = tmp_path

    # Pass a StatusConfig — not a pre-built tracker. The launcher will
    # construct its own tracker inside the worker thread that runs
    # ``launch()``, avoiding SQLite's "object created in another thread"
    # restriction when the test bridges sync ``launch()`` over async with
    # ``asyncio.to_thread``.
    return AgentLauncher(
        repo_path=str(tmp_path),
        wt_manager=wt_manager,
        status_config=StatusConfig(storage_path=tmp_path / "status.db"),
        config=SimpleNamespace(
            environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False),
            recall_enabled=False,
        ),
        backend=backend,
    )


# ── tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owt_new_herdr_creates_workspace_and_records_backend(
    fake_herdr: tuple[Path, list[dict]],
    tmp_path: Path,
) -> None:
    """End-to-end: launching with HERDR routes through herdr, not tmux."""
    sock, calls = fake_herdr

    backend = HerdrBackend(socket_path=str(sock))
    launcher = _make_launcher(tmp_path, backend)

    request = LaunchRequest(
        branch="feat/wt",
        base_branch="main",
        ai_tool="claude",
        mode=LaunchMode.INTERACTIVE,
        prompt="Build the thing",
        session_type=SessionType.WORKTREE,
        backend_kind=BackendKind.HERDR,
    )

    with (
        patch("open_orchestrator.core.agent_launcher.get_registry") as reg,
        patch("open_orchestrator.core.agent_launcher._setup_pane_environment"),
    ):
        reg.return_value.get.return_value = _FakeTool()
        result = await asyncio.to_thread(launcher.launch, request)

    # 1. The herdr socket received workspace.create + pane.send_text(s).
    methods = [c["method"] for c in calls]
    assert "workspace.create" in methods, f"expected workspace.create in {methods}"
    # send_text fires twice: once in create_session for agent_command, once for the prompt.
    assert methods.count("pane.send_text") >= 1

    # 2. LaunchResult reflects herdr backend + no tmux session.
    assert result.backend_kind == BackendKind.HERDR
    assert result.tmux_session is None
    assert result.backend_session_id == "pane-herdr-1"

    # 3. Status DB row carries herdr metadata.
    tracker = StatusTracker(StatusConfig(storage_path=tmp_path / "status.db"))
    status = tracker.get_status("wt-feat")
    assert status is not None
    assert status.backend_kind == "herdr"
    assert status.backend_session_id == "pane-herdr-1"
    assert status.backend_meta.get("workspace_id") == "ws-1"
    assert status.tmux_session is None  # crucially: no tmux session created


@pytest.mark.asyncio
async def test_owt_new_herdr_status_round_trip_via_get_backend_session(
    fake_herdr: tuple[Path, list[dict]],
    tmp_path: Path,
) -> None:
    """``StatusTracker.get_backend_session`` reconstructs the BackendSession."""
    sock, _calls = fake_herdr

    backend = HerdrBackend(socket_path=str(sock))
    launcher = _make_launcher(tmp_path, backend)

    request = LaunchRequest(
        branch="feat/wt",
        base_branch="main",
        ai_tool="claude",
        mode=LaunchMode.INTERACTIVE,
        prompt="x",
        backend_kind=BackendKind.HERDR,
    )

    with (
        patch("open_orchestrator.core.agent_launcher.get_registry") as reg,
        patch("open_orchestrator.core.agent_launcher._setup_pane_environment"),
    ):
        reg.return_value.get.return_value = _FakeTool()
        await asyncio.to_thread(launcher.launch, request)

    tracker = StatusTracker(StatusConfig(storage_path=tmp_path / "status.db"))
    recorded = tracker.get_backend_session("wt-feat")
    assert recorded is not None
    assert recorded.kind == BackendKind.HERDR
    assert recorded.id == "pane-herdr-1"
    assert recorded.meta.get("workspace_id") == "ws-1"
