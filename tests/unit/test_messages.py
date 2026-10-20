from __future__ import annotations

import struct
import uuid
from typing import Any

import pytest

from mpos.errors import ProtocolError
from mpos.net.messages import (
    MAX_SEGMENT_SIZE,
    Manifest,
    Message,
    MessageType,
    decode_message,
    encode_message,
    position_of,
    safe_filename,
    sign,
    transfer_id_of,
    verify,
)

TID = uuid.uuid4().hex
KEY = b"k" * 32


def _manifest(**overrides: Any) -> dict[str, Any]:
    header = Manifest(
        transfer_id=TID,
        sender="alpha",
        filename="report.pdf",
        size=10_000,
        sha256="ab" * 32,
        segment_size=4096,
        k=4,
        n=6,
        ephemeral_key="AAAA",
    ).to_header()
    header.update(overrides)
    return header


# ============================================================
# WIRE FORMAT
# ============================================================


def test_roundtrip_with_binary_body() -> None:
    message = Message(MessageType.SHARD, {"transfer_id": TID, "x": [1, 2]}, b"\0\xff")

    assert decode_message(encode_message(message)) == message


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01\x00",
        struct.pack("!BI", 1, 100) + b"{}",
        struct.pack("!BI", 99, 2) + b"{}",
        struct.pack("!BI", 1, 3) + b"{x}",
        struct.pack("!BI", 1, 2) + b"[]",
        struct.pack("!BI", 1, 2) + b"\xff\xfe",
    ],
)
def test_malformed_messages_rejected(data: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode_message(data)


# ============================================================
# MAC
# ============================================================


def test_sign_and_verify() -> None:
    signed = sign(Message(MessageType.SHARD_ACK, {"transfer_id": TID, "shard": 2}), KEY)

    assert verify(signed, KEY)
    assert verify(decode_message(encode_message(signed)), KEY)


def test_mac_rejects_tampering_wrong_key_and_missing_mac() -> None:
    signed = sign(Message(MessageType.SHARD_ACK, {"transfer_id": TID, "shard": 2}), KEY)
    tampered = Message(signed.type, {**signed.header, "shard": 3})
    retyped = Message(MessageType.MANIFEST_ACK, signed.header)

    assert not verify(tampered, KEY)
    assert not verify(retyped, KEY)
    assert not verify(signed, b"x" * 32)
    assert not verify(Message(MessageType.SHARD_ACK, {"transfer_id": TID}), KEY)


# ============================================================
# MANIFEST & FIELDS
# ============================================================


def test_manifest_roundtrip_and_segment_math() -> None:
    manifest = Manifest.from_header(_manifest())

    assert Manifest.from_header(manifest.to_header()) == manifest
    assert manifest.segment_count == 3
    assert [manifest.segment_length(i) for i in range(3)] == [4096, 4096, 1808]
    assert Manifest.from_header(_manifest(size=0)).segment_count == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"k": 7},
        {"k": 0},
        {"n": 257},
        {"size": -1},
        {"size": "10"},
        {"k": True},
        {"segment_size": MAX_SEGMENT_SIZE + 1},
        {"sha256": "AB" * 32},
        {"sha256": "ab"},
        {"transfer_id": "not-a-uuid"},
        {"transfer_id": TID.upper()},
        {"sender": ""},
    ],
)
def test_manifest_validation(overrides: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError):
        Manifest.from_header(_manifest(**overrides))


def test_manifest_filename_is_sanitized() -> None:
    manifest = Manifest.from_header(_manifest(filename="../../etc/passwd"))

    assert manifest.filename == "passwd"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("/abs/path/x.bin", "x.bin"),
        ("..\\..\\win.ini", "win.ini"),
        ("..", "received.bin"),
        ("dir/", "received.bin"),
        ("bad\x00\nname.txt", "badname.txt"),
        ("é" * 200, "é" * 127),
    ],
)
def test_safe_filename(name: str, expected: str) -> None:
    assert safe_filename(name) == expected


def test_position_bounds() -> None:
    manifest = Manifest.from_header(_manifest())

    assert position_of({"segment": 2, "shard": 5}, manifest) == (2, 5)
    for bad in ({"segment": 3, "shard": 0}, {"segment": 0, "shard": 6}, {}):
        with pytest.raises(ProtocolError):
            position_of(bad, manifest)


def test_transfer_id_validation() -> None:
    assert transfer_id_of({"transfer_id": TID}) == TID
    with pytest.raises(ProtocolError):
        transfer_id_of({"transfer_id": 5})
