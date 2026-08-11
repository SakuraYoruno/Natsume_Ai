"""Small user-space TCP forwarder for an explicitly bound acceptance port."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress


async def _copy(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    finally:
        with suppress(Exception):
            writer.write_eof()


async def _serve(
    *,
    listen_host: str,
    listen_port: int,
    target_host: str,
    target_port: int,
) -> None:
    async def handle(
        source_reader: asyncio.StreamReader,
        source_writer: asyncio.StreamWriter,
    ) -> None:
        try:
            target_reader, target_writer = await asyncio.open_connection(
                target_host,
                target_port,
            )
        except Exception:
            source_writer.close()
            await source_writer.wait_closed()
            return
        try:
            await asyncio.gather(
                _copy(source_reader, target_writer),
                _copy(target_reader, source_writer),
            )
        finally:
            target_writer.close()
            source_writer.close()
            with suppress(Exception):
                await target_writer.wait_closed()
            with suppress(Exception):
                await source_writer.wait_closed()

    server = await asyncio.start_server(handle, listen_host, listen_port)
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    args = parser.parse_args()
    for name in ("listen_port", "target_port"):
        if not 1 <= int(getattr(args, name)) <= 65_535:
            parser.error(f"{name.replace('_', '-')} must be in 1..65535")
    asyncio.run(
        _serve(
            listen_host=args.listen_host,
            listen_port=args.listen_port,
            target_host=args.target_host,
            target_port=args.target_port,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
