"""CONNECT-only egress proxy with a host:port allowlist (stdlib only).

Runs inside the toolchain image as the only bridge between an agent container
on an internal Docker network and the internet. Only ``CONNECT host:port``
for allowlisted targets is tunneled; everything else gets 403/405. TLS is not
inspected. Every decision is logged as one JSON line on stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

READ_HEAD_SECONDS = 30
CONNECT_SECONDS = 15
MAX_HEAD_BYTES = 16384


def _log(decision: str, target: str, client: str, detail: str = "") -> None:
    record = {"ts": round(time.time(), 3), "decision": decision, "target": target, "client": client}
    if detail:
        record["detail"] = detail[:300]
    sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stdout.flush()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _reply(writer: asyncio.StreamWriter, status: str) -> None:
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
    try:
        await writer.drain()
    finally:
        writer.close()


def parse_connect(head: bytes) -> str | None:
    """Return the normalized ``host:port`` of a CONNECT request line, else None."""
    line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = line.split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT" or not parts[2].upper().startswith("HTTP/"):
        return None
    host, sep, port = parts[1].rpartition(":")
    if not sep or not host or not port.isdigit():
        return None
    return f"{host.lower().rstrip('.')}:{int(port)}"


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, allow: frozenset[str]) -> None:
    peer = writer.get_extra_info("peername")
    client = f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) else str(peer)
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_HEAD_SECONDS)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
        writer.close()
        return
    target = parse_connect(head)
    if target is None:
        _log("deny", head.split(b"\r\n", 1)[0].decode("latin-1", "replace")[:200], client, "not CONNECT")
        await _reply(writer, "405 Method Not Allowed")
        return
    if target not in allow:
        _log("deny", target, client, "not in allowlist")
        await _reply(writer, "403 Forbidden")
        return
    host, _, port = target.rpartition(":")
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), CONNECT_SECONDS
        )
    except (OSError, asyncio.TimeoutError) as exc:
        _log("error", target, client, f"{type(exc).__name__}: {exc}")
        await _reply(writer, "502 Bad Gateway")
        return
    _log("allow", target, client)
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))


async def serve(host: str, port: int, allow: frozenset[str], ready: asyncio.Event | None = None) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, allow), host, port, limit=MAX_HEAD_BYTES
    )
    bound = server.sockets[0].getsockname()
    sys.stdout.write(json.dumps({"listening": f"{bound[0]}:{bound[1]}", "allow": sorted(allow)}) + "\n")
    sys.stdout.flush()
    if ready is not None:
        ready.set()
    async with server:
        await server.serve_forever()


def normalize_allow(entries: list[str]) -> frozenset[str]:
    normalized = set()
    for entry in entries:
        host, sep, port = entry.strip().rpartition(":")
        if not sep or not host or not port.isdigit():
            raise ValueError(f"allowlist entries must be host:port, got {entry!r}")
        normalized.add(f"{host.lower().rstrip('.')}:{int(port)}")
    return frozenset(normalized)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="0.0.0.0:3128")
    parser.add_argument("--allow", action="append", default=[], help="host:port; repeatable")
    args = parser.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    try:
        asyncio.run(serve(host, int(port), normalize_allow(args.allow)))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
