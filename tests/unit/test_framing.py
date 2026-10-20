from __future__ import annotations

import asyncio
import os
import struct

import pytest

from mpos.errors import FramingError
from mpos.net.framing import (
    HEADER_SIZE,
    MAGIC,
    MAX_FRAME,
    VERSION,
    Frame,
    FrameType,
    decode_frame,
    decode_header,
    encode_frame,
    encode_header,
    read_frame,
)


def _reader(data: bytes, *, eof: bool = True) -> asyncio.StreamReader:
    reader = asyncio.StreamReader(limit=MAX_FRAME * 2)
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


def _raw_header(
    *,
    magic: bytes = MAGIC,
    version: int = VERSION,
    type_: int = FrameType.ONION,
    length: int = 0,
) -> bytes:
    return struct.pack("!2sBBBIII", magic, version, type_, 0, 7, 9, length)


# ============================================================
# ENCODE / DECODE
# ============================================================


def test_header_layout_is_exact() -> None:
    raw = encode_frame(
        Frame(FrameType.ONION, b"abc", circuit_id=0x01020304, stream_id=5, flags=0x80)
    )

    assert HEADER_SIZE == 17
    assert raw == (
        b"MP"
        + bytes([2, 3, 0x80])
        + bytes.fromhex("01020304")
        + bytes.fromhex("00000005")
        + bytes.fromhex("00000003")
        + b"abc"
    )


@pytest.mark.parametrize("frame_type", list(FrameType))
def test_roundtrip_every_type(frame_type: FrameType) -> None:
    frame = Frame(frame_type, os.urandom(100), circuit_id=2**32 - 1, stream_id=42)

    assert decode_frame(encode_frame(frame)) == frame


def test_empty_body() -> None:
    frame = Frame(FrameType.PING)

    assert decode_frame(encode_frame(frame)) == frame
    assert len(encode_frame(frame)) == HEADER_SIZE


def test_body_exactly_at_cap_is_allowed() -> None:
    frame = Frame(FrameType.ONION, bytes(MAX_FRAME))

    assert decode_frame(encode_frame(frame)).body == frame.body


def test_oversized_body_rejected_on_encode() -> None:
    with pytest.raises(FramingError, match="exceeds"):
        encode_frame(Frame(FrameType.ONION, bytes(MAX_FRAME + 1)))


def test_custom_cap() -> None:
    with pytest.raises(FramingError):
        encode_frame(Frame(FrameType.ONION, b"12345"), max_frame=4)
    with pytest.raises(FramingError):
        decode_header(_raw_header(length=5), max_frame=4)


@pytest.mark.parametrize(
    "frame",
    [
        Frame(FrameType.ONION, circuit_id=-1),
        Frame(FrameType.ONION, circuit_id=2**32),
        Frame(FrameType.ONION, stream_id=2**32),
        Frame(FrameType.ONION, flags=256),
    ],
)
def test_field_ranges(frame: Frame) -> None:
    with pytest.raises(FramingError, match="out of range"):
        encode_frame(frame)


@pytest.mark.parametrize(
    ("header", "match"),
    [
        (_raw_header(magic=b"XX"), "magic"),
        (_raw_header(version=1), "version"),
        (_raw_header(type_=0), "Unknown frame type"),
        (_raw_header(type_=99), "Unknown frame type"),
        (_raw_header(length=MAX_FRAME + 1), "exceeds"),
        (_raw_header(length=2**32 - 1), "exceeds"),
    ],
)
def test_bad_headers_rejected(header: bytes, match: str) -> None:
    with pytest.raises(FramingError, match=match):
        decode_header(header)


def test_decode_frame_length_mismatch() -> None:
    raw = encode_frame(Frame(FrameType.ONION, b"abcd"))

    with pytest.raises(FramingError, match="header says"):
        decode_frame(raw[:-1])
    with pytest.raises(FramingError, match="header says"):
        decode_frame(raw + b"x")
    with pytest.raises(FramingError, match="Header must be"):
        decode_frame(raw[:5])


# ============================================================
# STREAM READS
# ============================================================


async def test_read_several_frames_back_to_back() -> None:
    frames = [
        Frame(FrameType.HELLO, b"node-a"),
        Frame(FrameType.ONION, os.urandom(5000), circuit_id=1, stream_id=1),
        Frame(FrameType.PING),
    ]
    reader = _reader(b"".join(encode_frame(f) for f in frames))

    assert [await read_frame(reader) for _ in frames] == frames
    assert await read_frame(reader) is None


async def test_clean_eof_returns_none() -> None:
    assert await read_frame(_reader(b"")) is None


@pytest.mark.parametrize("cut", [1, HEADER_SIZE - 1])
async def test_truncated_header(cut: int) -> None:
    raw = encode_frame(Frame(FrameType.ONION, b"body"))

    with pytest.raises(FramingError, match="Truncated header"):
        await read_frame(_reader(raw[:cut]))


async def test_truncated_body() -> None:
    raw = encode_frame(Frame(FrameType.ONION, b"0123456789"))

    with pytest.raises(FramingError, match="Truncated body"):
        await read_frame(_reader(raw[:-3]))


async def test_oversized_header_rejected_before_body_arrives() -> None:
    """The cap is enforced on the header alone; we never wait for the body."""
    reader = _reader(_raw_header(length=MAX_FRAME + 1), eof=False)

    with pytest.raises(FramingError, match="exceeds"):
        await asyncio.wait_for(read_frame(reader), timeout=1)


async def test_timeout() -> None:
    reader = _reader(encode_frame(Frame(FrameType.PING))[:5], eof=False)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await read_frame(reader)


def test_encode_header_matches_frame_prefix() -> None:
    frame = Frame(FrameType.ONION, os.urandom(300), circuit_id=7, stream_id=2)

    assert encode_header(frame) == encode_frame(frame)[:HEADER_SIZE]
