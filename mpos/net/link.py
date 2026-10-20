"""Persistent framed TCP links between nodes.

One Link carries many onions at once: each ONION frame gets a per-link
circuit_id, and a write lock keeps frames from different tasks from
interleaving on the socket.

Handshake (5 s timeout):

    dialer   -> HELLO     {"node_id": "<dialer>", "version": 2}
    acceptor -> HELLO_ACK {"node_id": "<acceptor>", "version": 2}

The dialer checks that the acceptor names itself as the node it meant to
reach. HELLO is not authenticated, so nodes only *send* over links they
dialed themselves (see node.py); onion layers stay confidential either way.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json

from mpos.errors import FramingError, HopUnreachable
from mpos.net.framing import (
    HEADER_SIZE,
    MAX_FRAME,
    VERSION,
    Frame,
    FrameType,
    encode_frame,
    read_frame,
    write_frame,
)

HANDSHAKE_TIMEOUT = 5.0
MAX_NODE_ID_BYTES = 64

_U32_MAX = 0xFFFF_FFFF


# ============================================================
# HELLO BODY
# ============================================================


def _hello_body(node_id: str) -> bytes:
    return json.dumps({"node_id": node_id, "version": VERSION}).encode("utf-8")


def _parse_hello(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FramingError(f"Malformed HELLO: {e}") from e

    if not isinstance(data, dict):
        raise FramingError("HELLO body is not an object")

    node_id = data.get("node_id")

    if (
        not isinstance(node_id, str)
        or not 1 <= len(node_id.encode()) <= MAX_NODE_ID_BYTES
    ):
        raise FramingError("HELLO has an invalid node_id")

    if data.get("version") != VERSION:
        raise FramingError(f"Peer speaks protocol version {data.get('version')}")

    return node_id


# ============================================================
# LINK
# ============================================================


class Link:
    """An open, handshaken connection to one peer."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        peer_id: str,
        outbound: bool,
        max_frame: int = MAX_FRAME,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = asyncio.Lock()
        self._circuit_ids = itertools.count(1)
        self._max_frame = max_frame
        self._closed = False
        self.peer_id = peer_id
        self.outbound = outbound

    def __repr__(self) -> str:
        direction = "->" if self.outbound else "<-"
        return f"Link({direction} {self.peer_id})"

    @property
    def is_closed(self) -> bool:
        return self._closed or self._writer.is_closing()

    def new_circuit_id(self) -> int:
        """Next circuit id on this link: 1..2**32-1, wrapping (0 is reserved)."""
        return (next(self._circuit_ids) - 1) % _U32_MAX + 1

    async def send(self, frame: Frame) -> None:
        """Send one frame. Raises HopUnreachable if the link is dead."""
        if self.is_closed:
            raise HopUnreachable(f"Link to {self.peer_id} is closed")

        async with self._write_lock:
            try:
                await write_frame(self._writer, frame, max_frame=self._max_frame)
            except (ConnectionError, OSError) as e:
                await self.close()
                raise HopUnreachable(f"Send to {self.peer_id} failed: {e}") from e

    async def send_truncated(self, frame: Frame) -> None:
        """Fault injection: write half of ``frame``, then cut the link.

        The peer sees the connection drop mid-frame (FramingError).
        """
        if self.is_closed:
            raise HopUnreachable(f"Link to {self.peer_id} is closed")

        data = encode_frame(frame, max_frame=self._max_frame)
        async with self._write_lock:
            self._writer.write(data[: max(HEADER_SIZE + 1, len(data) // 2)])
            with contextlib.suppress(ConnectionError, OSError):
                await self._writer.drain()
        await self.close()

    async def receive(self) -> Frame | None:
        """Next frame, or None once the peer has closed the link."""
        return await read_frame(self._reader, max_frame=self._max_frame)

    async def close(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._writer.close()

        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


# ============================================================
# HANDSHAKE
# ============================================================


async def _close_quietly(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def open_link(
    host: str,
    port: int,
    *,
    local_id: str,
    expected_peer_id: str,
    max_frame: int = MAX_FRAME,
    handshake_timeout: float = HANDSHAKE_TIMEOUT,
) -> Link:
    """Dial a peer and complete the HELLO handshake.

    Raises:
        HopUnreachable: connect/handshake failed, timed out, or the peer
            identified itself as a different node.
    """
    writer: asyncio.StreamWriter | None = None

    try:
        async with asyncio.timeout(handshake_timeout):
            reader, writer = await asyncio.open_connection(host, port)
            await write_frame(writer, Frame(FrameType.HELLO, _hello_body(local_id)))
            reply = await read_frame(reader, max_frame=max_frame)

        if reply is None or reply.type is not FrameType.HELLO_ACK:
            raise FramingError(f"Expected HELLO_ACK, got {reply}")

        peer_id = _parse_hello(reply.body)

        if peer_id != expected_peer_id:
            raise FramingError(f"Dialed {expected_peer_id!r} but peer is {peer_id!r}")

    except (OSError, TimeoutError, FramingError) as e:
        if writer is not None:
            await _close_quietly(writer)
        raise HopUnreachable(
            f"Cannot link to {expected_peer_id} at {host}:{port}: {e!r}"
        ) from e
    except asyncio.CancelledError:
        if writer is not None:
            writer.close()  # cancelled mid-dial (e.g. a heartbeat stopped)
        raise

    return Link(reader, writer, peer_id=peer_id, outbound=True, max_frame=max_frame)


async def accept_link(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    local_id: str,
    max_frame: int = MAX_FRAME,
    handshake_timeout: float = HANDSHAKE_TIMEOUT,
) -> Link:
    """Answer an inbound HELLO.

    Raises:
        FramingError, TimeoutError: the peer did not complete the handshake.
            The caller closes the connection.
    """
    async with asyncio.timeout(handshake_timeout):
        hello = await read_frame(reader, max_frame=max_frame)

    if hello is None or hello.type is not FrameType.HELLO:
        raise FramingError(f"Expected HELLO, got {hello}")

    peer_id = _parse_hello(hello.body)

    async with asyncio.timeout(handshake_timeout):
        await write_frame(writer, Frame(FrameType.HELLO_ACK, _hello_body(local_id)))

    return Link(reader, writer, peer_id=peer_id, outbound=False, max_frame=max_frame)
