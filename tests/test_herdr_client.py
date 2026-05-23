"""Sprint 025: HerdrClient against a fake newline-JSON socket server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from open_orchestrator.core.herdr_client import HerdrClient, HerdrError, default_socket_path


async def _run_fake_server(socket_path: Path, *, handler) -> asyncio.AbstractServer:  # noqa: ANN001
    """Start an asyncio Unix server that calls ``handler(payload)`` per request."""

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

    return await asyncio.start_unix_server(_serve, path=str(socket_path))


def test_default_socket_path() -> None:
    assert default_socket_path().name == "herdr.sock"
    assert "sessions/work" in str(default_socket_path("work"))


@pytest.fixture
def short_sock(herdr_socket_path: Path) -> Path:
    """Backwards-compat alias for the shared ``herdr_socket_path`` fixture."""
    return herdr_socket_path


@pytest.mark.asyncio
async def test_call_returns_result(short_sock: Path) -> None:
    sock = short_sock

    async def handler(payload):  # noqa: ANN001
        return {"id": payload["id"], "result": {"echo": payload["params"]}}

    server = await _run_fake_server(sock, handler=handler)
    try:
        client = HerdrClient(socket_path=sock)
        await client.connect()
        try:
            result = await client.call("ping", {"hello": "world"})
            assert result == {"echo": {"hello": "world"}}
        finally:
            await client.close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_call_surfaces_error(short_sock: Path) -> None:
    sock = short_sock

    async def handler(payload):  # noqa: ANN001
        return {"id": payload["id"], "error": {"message": "boom", "code": 42}}

    server = await _run_fake_server(sock, handler=handler)
    try:
        client = HerdrClient(socket_path=sock)
        await client.connect()
        try:
            with pytest.raises(HerdrError) as exc:
                await client.call("ping", {})
            assert "boom" in str(exc.value)
            assert exc.value.code == 42
        finally:
            await client.close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_ping_returns_false_when_socket_missing(short_sock: Path) -> None:
    sock = short_sock
    client = HerdrClient(socket_path=sock)
    assert await client.ping() is False


@pytest.mark.asyncio
async def test_call_timeout(short_sock: Path) -> None:
    sock = short_sock

    async def handler(payload):  # noqa: ANN001, ARG001
        await asyncio.sleep(2)
        return None

    server = await _run_fake_server(sock, handler=handler)
    try:
        client = HerdrClient(socket_path=sock, request_timeout=0.2)
        await client.connect()
        try:
            with pytest.raises(HerdrError) as exc:
                await client.call("ping", {})
            assert "timed out" in str(exc.value).lower()
        finally:
            await client.close()
    finally:
        server.close()
        await server.wait_closed()
