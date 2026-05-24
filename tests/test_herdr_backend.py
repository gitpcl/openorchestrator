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
def short_sock(herdr_socket_path: Path) -> Path:
    """Backwards-compat alias for the shared ``herdr_socket_path`` fixture."""
    return herdr_socket_path


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


# ── response shape tolerance ──────────────────────────────────────


@pytest.mark.parametrize(
    "payload,expected_ws,expected_pane",
    [
        # Canonical shape we originally coded against.
        ({"workspace_id": "ws-1", "root_pane_id": "p-1"}, "ws-1", "p-1"),
        # Alt names: id + pane_id.
        ({"id": "ws-2", "pane_id": "p-2"}, "ws-2", "p-2"),
        # Composite shape: sibling workspace + root_pane sub-objects.
        # This is what real herdr's `workspace_created` returns.
        (
            {
                "type": "workspace_created",
                "workspace": {"workspace_id": "ws-real", "label": "feat-x"},
                "tab": {"tab_id": "ws-real:1"},
                "root_pane": {
                    "pane_id": "ws-real:1:1",
                    "terminal_id": "term_abc",
                    "workspace_id": "ws-real",
                },
            },
            "ws-real",
            "ws-real:1:1",
        ),
        # Composite shape with nested panes array.
        ({"workspace": {"id": "ws-4", "panes": [{"id": "p-4"}, {"id": "p-5"}]}}, "ws-4", "p-4"),
        # Envelope under "data".
        ({"data": {"workspace_id": "ws-6", "root_pane_id": "p-6"}}, "ws-6", "p-6"),
        # Integer ids get stringified.
        ({"workspace_id": 7, "root_pane_id": 8}, "7", "8"),
        # CamelCase root pane key.
        ({"id": "ws-9", "rootPaneId": "p-9"}, "ws-9", "p-9"),
    ],
)
def test_extract_workspace_pane_shapes(payload, expected_ws, expected_pane) -> None:  # noqa: ANN001
    from open_orchestrator.core.herdr_backend import _extract_workspace_pane

    ws_id, pane_id = _extract_workspace_pane(payload)
    assert ws_id == expected_ws
    assert pane_id == expected_pane


def test_extract_workspace_pane_returns_empty_for_garbage() -> None:
    from open_orchestrator.core.herdr_backend import _extract_workspace_pane

    assert _extract_workspace_pane(None) == ("", "")
    assert _extract_workspace_pane("not-a-dict") == ("", "")
    assert _extract_workspace_pane({}) == ("", "")
    assert _extract_workspace_pane([]) == ("", "")


@pytest.mark.asyncio
async def test_create_session_falls_back_to_workspace_get_for_pane_id(short_sock: Path) -> None:
    """Some herdr builds return only a workspace id; we must probe for the pane."""
    sock = short_sock
    seen: list[str] = []

    async def handler(payload):  # noqa: ANN001
        method = payload["method"]
        seen.append(method)
        if method == "workspace.create":
            return {"id": payload["id"], "result": {"workspace_id": "ws-late"}}
        if method == "workspace.get":
            return {"id": payload["id"], "result": {"id": "ws-late", "root_pane_id": "p-late"}}
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        session = await asyncio.to_thread(
            backend.create_session,
            "wt-late",
            "/tmp/wt-late",
            agent_command="claude",
        )
    finally:
        server.close()
        await server.wait_closed()

    assert session.id == "p-late"
    assert session.meta["workspace_id"] == "ws-late"
    assert "workspace.create" in seen
    assert "workspace.get" in seen  # probe fired
    assert "pane.send_text" in seen  # agent_command then went through
    assert "workspace.panes" not in seen  # the timing-out RPC must never be probed


@pytest.mark.asyncio
async def test_session_for_resolves_via_workspace_list_and_pane_list(short_sock: Path) -> None:
    """session_for uses workspace.list + pane.list, never the timing-out find."""
    sock = short_sock
    seen: list[str] = []

    async def handler(payload):  # noqa: ANN001
        method = payload["method"]
        seen.append(method)
        if method == "workspace.list":
            return {
                "id": payload["id"],
                "result": {
                    "type": "workspace_list",
                    "workspaces": [
                        {"workspace_id": "w-aaa", "label": "other"},
                        {"workspace_id": "w-bbb", "label": "wt-target"},
                    ],
                },
            }
        if method == "pane.list":
            return {
                "id": payload["id"],
                "result": {
                    "type": "pane_list",
                    "panes": [
                        {"pane_id": "w-bbb-1", "workspace_id": "w-bbb", "focused": True},
                    ],
                },
            }
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        session = await asyncio.to_thread(backend.session_for, "wt-target")
    finally:
        server.close()
        await server.wait_closed()

    assert session is not None
    assert session.id == "w-bbb-1"
    assert session.meta["workspace_id"] == "w-bbb"
    assert "workspace.find" not in seen  # times out on herdr v0.6.1
    assert "workspace.panes" not in seen


@pytest.mark.asyncio
async def test_session_for_returns_none_for_unknown_label(short_sock: Path) -> None:
    """An unmatched label resolves to None without raising or hanging."""
    sock = short_sock

    async def handler(payload):  # noqa: ANN001
        if payload["method"] == "workspace.list":
            return {
                "id": payload["id"],
                "result": {"type": "workspace_list", "workspaces": [{"workspace_id": "w-x", "label": "nope"}]},
            }
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        session = await asyncio.to_thread(backend.session_for, "missing")
    finally:
        server.close()
        await server.wait_closed()

    assert session is None


@pytest.mark.asyncio
async def test_session_for_prefers_focused_pane(short_sock: Path) -> None:
    """When a workspace has multiple panes, the focused one is chosen."""
    sock = short_sock

    async def handler(payload):  # noqa: ANN001
        method = payload["method"]
        if method == "workspace.list":
            return {
                "id": payload["id"],
                "result": {"workspaces": [{"workspace_id": "w-1", "label": "wt"}]},
            }
        if method == "pane.list":
            return {
                "id": payload["id"],
                "result": {
                    "panes": [
                        {"pane_id": "w-1-1", "focused": False},
                        {"pane_id": "w-1-2", "focused": True},
                    ]
                },
            }
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        session = await asyncio.to_thread(backend.session_for, "wt")
    finally:
        server.close()
        await server.wait_closed()

    assert session is not None
    assert session.id == "w-1-2"


# ── submit-mode chokepoint (_send_line) ──────────────────────────


@pytest.mark.asyncio
async def test_send_text_default_sends_body_then_separate_cr(short_sock: Path) -> None:
    """Default path sends the body, then a STANDALONE "\\r", as two calls.

    Regression guard for the herdr-submit bug: a single "body\\r" blob does
    not submit in a TUI agent; the terminator must be its own send_text call.
    """
    sock = short_sock
    seen: list[dict] = []

    async def handler(payload):  # noqa: ANN001
        seen.append(payload)
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        from open_orchestrator.models.backend import BackendKind, BackendSession

        sess = BackendSession(kind=BackendKind.HERDR, id="pane-1", worktree_name="wt")
        # Clear any inherited env so we exercise the default path.
        import os

        os.environ.pop("OWT_HERDR_SUBMIT", None)
        await asyncio.to_thread(backend.send_text, sess, "hello world")
    finally:
        server.close()
        await server.wait_closed()

    submit_calls = [c["params"]["text"] for c in seen if c["method"] == "pane.send_text"]
    assert submit_calls == ["hello world", "\r"]


@pytest.mark.asyncio
async def test_send_text_text_crlf_override(short_sock: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OWT_HERDR_SUBMIT=text:\\r\\n delivers CRLF terminator."""
    sock = short_sock
    seen: list[dict] = []

    async def handler(payload):  # noqa: ANN001
        seen.append(payload)
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        monkeypatch.setenv("OWT_HERDR_SUBMIT", "text:\\r\\n")
        backend = HerdrBackend(socket_path=str(sock))
        from open_orchestrator.models.backend import BackendKind, BackendSession

        sess = BackendSession(kind=BackendKind.HERDR, id="pane-1", worktree_name="wt")
        await asyncio.to_thread(backend.send_text, sess, "hi")
    finally:
        server.close()
        await server.wait_closed()

    submit_calls = [c["params"]["text"] for c in seen if c["method"] == "pane.send_text"]
    # Body and the CRLF terminator are delivered as two separate calls.
    assert submit_calls == ["hi", "\r\n"]


@pytest.mark.asyncio
async def test_send_text_keys_enter_override(short_sock: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OWT_HERDR_SUBMIT=keys:Enter sends text + separate Enter key event."""
    sock = short_sock
    seen: list[dict] = []

    async def handler(payload):  # noqa: ANN001
        seen.append(payload)
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        monkeypatch.setenv("OWT_HERDR_SUBMIT", "keys:Enter")
        backend = HerdrBackend(socket_path=str(sock))
        from open_orchestrator.models.backend import BackendKind, BackendSession

        sess = BackendSession(kind=BackendKind.HERDR, id="pane-1", worktree_name="wt")
        await asyncio.to_thread(backend.send_text, sess, "build it")
    finally:
        server.close()
        await server.wait_closed()

    text_calls = [c for c in seen if c["method"] == "pane.send_text"]
    key_calls = [c for c in seen if c["method"] == "pane.send_keys"]
    assert len(text_calls) == 1
    assert text_calls[0]["params"]["text"] == "build it"  # body only, no terminator
    assert len(key_calls) == 1
    assert key_calls[0]["params"]["keys"] == "Enter"


@pytest.mark.asyncio
async def test_send_text_keys_failure_falls_back_to_carriage_return(short_sock: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When pane.send_keys errors, _send_line falls back to embedded \\r."""
    sock = short_sock
    seen: list[dict] = []

    async def handler(payload):  # noqa: ANN001
        seen.append(payload)
        if payload["method"] == "pane.send_keys":
            return {"id": payload["id"], "error": {"message": "unknown key", "code": -1}}
        return {"id": payload["id"], "result": True}

    server = await _fake_server(sock, handler=handler)
    try:
        monkeypatch.setenv("OWT_HERDR_SUBMIT", "keys:Enter")
        backend = HerdrBackend(socket_path=str(sock))
        from open_orchestrator.models.backend import BackendKind, BackendSession

        sess = BackendSession(kind=BackendKind.HERDR, id="pane-1", worktree_name="wt")
        await asyncio.to_thread(backend.send_text, sess, "fallback")
    finally:
        server.close()
        await server.wait_closed()

    text_calls = [c["params"]["text"] for c in seen if c["method"] == "pane.send_text"]
    # First text call delivers the body, second delivers the fallback \r.
    assert text_calls == ["fallback", "\r"]


def test_resolve_submit_mode_unknown_warns_and_uses_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown OWT_HERDR_SUBMIT mode logs a warning and returns the default."""
    import logging

    from open_orchestrator.core.herdr_backend import (
        _DEFAULT_SUBMIT_MODE,
        _DEFAULT_SUBMIT_TERMINATOR,
        _resolve_submit_mode,
    )

    monkeypatch.setenv("OWT_HERDR_SUBMIT", "weird:nope")
    with caplog.at_level(logging.WARNING):
        mode, terminator = _resolve_submit_mode()

    assert mode == _DEFAULT_SUBMIT_MODE
    assert terminator == _DEFAULT_SUBMIT_TERMINATOR
    assert "Unknown OWT_HERDR_SUBMIT mode" in caplog.text


def test_resolve_submit_mode_no_env_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OWT_HERDR_SUBMIT", raising=False)
    from open_orchestrator.core.herdr_backend import (
        _DEFAULT_SUBMIT_MODE,
        _DEFAULT_SUBMIT_TERMINATOR,
        _resolve_submit_mode,
    )

    mode, terminator = _resolve_submit_mode()
    assert mode == _DEFAULT_SUBMIT_MODE
    assert terminator == _DEFAULT_SUBMIT_TERMINATOR


@pytest.mark.asyncio
async def test_create_session_error_includes_raw_response(short_sock: Path) -> None:
    """When parsing fails, the raw herdr payload appears in the error message."""
    sock = short_sock

    async def handler(payload):  # noqa: ANN001
        if payload["method"] == "workspace.create":
            return {"id": payload["id"], "result": {"weird": "shape"}}
        return {"id": payload["id"], "result": None}

    server = await _fake_server(sock, handler=handler)
    try:
        backend = HerdrBackend(socket_path=str(sock))
        with pytest.raises(HerdrError) as exc:
            await asyncio.to_thread(
                backend.create_session,
                "wt-bad",
                "/tmp/wt-bad",
                agent_command="claude",
            )
    finally:
        server.close()
        await server.wait_closed()

    assert "weird" in str(exc.value)
