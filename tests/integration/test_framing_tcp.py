"""Frames over a real loopback TCP socket (port 0, never fixed ports)."""

from __future__ import annotations

import asyncio
import os

from mpos.net.framing import Frame, FrameType, read_frame, write_frame


async def test_echo_frames_over_tcp() -> None:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            async with asyncio.timeout(5):
                frame = await read_frame(reader)
            if frame is None:
                break
            await write_frame(writer, frame)
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        # Includes a 4 MiB body so it spans many TCP segments
        sent = [
            Frame(FrameType.HELLO, b"alpha"),
            Frame(FrameType.ONION, os.urandom(4 * 1024 * 1024), circuit_id=9),
            Frame(FrameType.CLOSE, circuit_id=9, stream_id=3),
        ]

        async def send_all() -> None:
            for frame in sent:
                await write_frame(writer, frame)

        async def receive_all() -> list[Frame | None]:
            return [await read_frame(reader) for _ in sent]

        # Send and receive concurrently so large echoes cannot deadlock
        async with asyncio.timeout(10):
            _, received = await asyncio.gather(send_all(), receive_all())
        writer.close()
        await writer.wait_closed()

    assert received == sent
