from __future__ import annotations

import asyncio

import pytest

from runner.egress_proxy import normalize_allow, parse_connect, serve


def test_parse_connect_normalizes_and_rejects_other_requests() -> None:
    assert parse_connect(b"CONNECT API.Anthropic.com.:443 HTTP/1.1\r\nHost: x\r\n\r\n") == "api.anthropic.com:443"
    assert parse_connect(b"GET http://example.com/ HTTP/1.1\r\n\r\n") is None
    assert parse_connect(b"CONNECT example.com HTTP/1.1\r\n\r\n") is None
    assert parse_connect(b"CONNECT example.com:https HTTP/1.1\r\n\r\n") is None


def test_allowlist_entries_must_be_host_port() -> None:
    assert normalize_allow(["API.anthropic.com:443"]) == frozenset({"api.anthropic.com:443"})
    with pytest.raises(ValueError):
        normalize_allow(["api.anthropic.com"])


async def _exchange(proxy_port: int, request: bytes, then: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(request)
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    body = b""
    if b" 200 " in head and then:
        writer.write(then)
        await writer.drain()
        body = await reader.read(1024)
    writer.close()
    return head + body


def test_proxy_tunnels_allowed_targets_and_refuses_the_rest(capsys) -> None:
    async def scenario() -> list[bytes]:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = await reader.read(1024)
            writer.write(b"echo:" + data)
            await writer.drain()
            writer.close()

        target = await asyncio.start_server(echo, "127.0.0.1", 0)
        target_port = target.sockets[0].getsockname()[1]
        probe = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        proxy_port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        ready = asyncio.Event()
        allow = normalize_allow([f"localhost:{target_port}"])
        task = asyncio.create_task(serve("127.0.0.1", proxy_port, allow, ready))
        await ready.wait()
        try:
            return [
                await _exchange(proxy_port, f"CONNECT localhost:{target_port} HTTP/1.1\r\n\r\n".encode(), b"ping"),
                await _exchange(proxy_port, b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"),
                await _exchange(proxy_port, b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n"),
            ]
        finally:
            task.cancel()
            target.close()

    allowed, denied, plain = asyncio.run(scenario())
    assert allowed.startswith(b"HTTP/1.1 200") and allowed.endswith(b"echo:ping")
    assert denied.startswith(b"HTTP/1.1 403")
    assert plain.startswith(b"HTTP/1.1 405")
    log = capsys.readouterr().out
    assert '"decision": "allow"' in log and '"decision": "deny"' in log
