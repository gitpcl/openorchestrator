"""Sprint 025: HerdrBackend exercised against a canned async server.

We monkeypatch ``asyncio.run`` calls through the client by providing a
fake server (same flavor as ``test_herdr_client.py``) and pointing the
backend at it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from open_orchestrator.core.herdr_backend import HerdrBackend
from open_orchestrator.core.herdr_client import HerdrError


async def _fake_server(sock: Path, *, handler):  # noqa: ANN001, ANN202
    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                payload = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            try:
                response = await handler(payload)
            except Exception as err:  # noqa: BLE001
                response = {"id": payload.get("id"), "error": {"message": str(err), "code": -1}}
            if response is None:
                continue
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(_serve, path=str(sock))


def test_attach_argv_helper() -> None:
    assert HerdrBackend.attach_argv("pane-7") == ["herdr", "agent", "attach", "pane-7"]


@pytest.fixture
def short_sock() -> Path:
    import os
    import tempfile

    fd, name = tempfile.mkstemp(prefix="owt-herdr-", suffix=".sock", dir="/tmp")
    os.close(fd)
    os.unlink(name)
    return Path(name)


@pytest.mark.asyncio
async def test_create_session_calls_workspace_create(short_sock: Path) -> None:
    sock = short_sock
    seen = {"calls": []}

    async def handler(payload):  # noqa: ANN001
        seen["calls"].append(payload["method"])
        if payload["method"] == "workspace.create":
            return {
                "id": payload["id"],
                "result": {"workspace_id": "ws-1", "root_pane_id": "pane-1"},
            }
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        session = await asyncio.to_thread(
            backend.create_session,
            "wt-feat",
            "/tmp/wt-feat",
            agent_command="claude",
        )
        assert session.id == "pane-1"
        assert session.meta["workspace_id"] == "ws-1"
        assert "workspace.create" in seen["calls"]
        assert "pane.send_text" in seen["calls"]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_report_agent_state_is_non_fatal(short_sock: Path) -> None:
    sock = short_sock

    async def handler(payload):  # noqa: ANN001
        # Always fail report_agent
        if payload["method"] == "pane.report_agent":
            return {"id": payload["id"], "error": {"message": "down", "code": 1}}
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        from open_orchestrator.models.backend import BackendKind, BackendSession

        sess = BackendSession(kind=BackendKind.HERDR, id="p", worktree_name="wt")
        # Should NOT raise even though herdr returned an error
        await asyncio.to_thread(backend.report_agent_state, sess, "working", "doing things")
    finally:
        server.close()
        await server.wait_closed()


def test_create_session_raises_when_socket_missing() -> None:
    backend = HerdrBackend(socket_path="/tmp/owt-nope-does-not-exist.sock")
    with pytest.raises(HerdrError):
        backend.create_session("wt", "/tmp/wt", agent_command="x")
