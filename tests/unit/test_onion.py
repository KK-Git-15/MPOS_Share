from __future__ import annotations

import os

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from mpos.crypto.keys import generate_private_key, public_key_bytes
from mpos.crypto.onion import (
    LAYER_OVERHEAD,
    MAX_NODE_ID_BYTES,
    PAYLOAD_BUCKET,
    Command,
    Hop,
    build_onion,
    layer_parts,
    layers_remaining,
    onion_size,
    padded_payload_size,
    peel_layer,
)
from mpos.errors import LayerDecryptError

NAMES = ["guard", "middle", "exit", "receiver"]


@pytest.fixture
def nodes() -> dict[str, X25519PrivateKey]:
    return {name: generate_private_key() for name in NAMES}


def _route(nodes: dict[str, X25519PrivateKey], names: list[str]) -> list[Hop]:
    return [Hop(name, nodes[name].public_key()) for name in names]


def _walk(nodes: dict[str, X25519PrivateKey], first: str, onion: bytes) -> list[str]:
    """Follow the onion through the network the way relays will in 6d."""
    visited, current = [], first

    while True:
        visited.append(current)
        layer = peel_layer(onion, nodes[current])
        if layer.command is Command.DELIVER:
            return visited + [layer.payload.hex()]
        assert layer.next_hop is not None
        current, onion = layer.next_hop, layer.payload


def _flip(blob: bytes, pos: int) -> bytes:
    return blob[:pos] + bytes([blob[pos] ^ 0x01]) + blob[pos + 1 :]


# ============================================================
# HAPPY PATH
# ============================================================


@pytest.mark.parametrize("size", [0, 1, 4091, 4092, 100_000, 1024 * 1024])
def test_three_relays_plus_receiver_peel_in_order(
    nodes: dict[str, X25519PrivateKey], size: int
) -> None:
    payload = os.urandom(size)
    onion = build_onion(_route(nodes, NAMES), payload)

    assert _walk(nodes, "guard", onion) == [*NAMES, payload.hex()]


def test_each_hop_sees_only_its_successor(nodes: dict[str, X25519PrivateKey]) -> None:
    onion = build_onion(_route(nodes, ["guard", "middle", "exit"]), b"secret")

    g = peel_layer(onion, nodes["guard"])
    m = peel_layer(g.payload, nodes["middle"])
    e = peel_layer(m.payload, nodes["exit"])

    assert (g.command, g.next_hop) == (Command.RELAY, "middle")
    assert (m.command, m.next_hop) == (Command.RELAY, "exit")
    assert (e.command, e.next_hop, e.payload) == (Command.DELIVER, None, b"secret")
    assert b"secret" not in onion + g.payload + m.payload


def test_single_hop(nodes: dict[str, X25519PrivateKey]) -> None:
    onion = build_onion(_route(nodes, ["receiver"]), b"hi")

    assert peel_layer(onion, nodes["receiver"]).payload == b"hi"


def test_unicode_node_ids() -> None:
    a, b = generate_private_key(), generate_private_key()
    route = [
        Hop("nœud-α", a.public_key()),
        Hop("b" * MAX_NODE_ID_BYTES, b.public_key()),
    ]

    first = peel_layer(build_onion(route, b"x"), a)

    assert first.next_hop == "b" * MAX_NODE_ID_BYTES
    assert peel_layer(first.payload, b).payload == b"x"


def test_onions_are_randomized(nodes: dict[str, X25519PrivateKey]) -> None:
    route = _route(nodes, NAMES)

    assert build_onion(route, b"same") != build_onion(route, b"same")


# ============================================================
# SIZES
# ============================================================


def test_payload_size_hidden_within_bucket(nodes: dict[str, X25519PrivateKey]) -> None:
    route = _route(nodes, NAMES)

    small = build_onion(route, b"x")
    large = build_onion(route, os.urandom(PAYLOAD_BUCKET - 4))

    assert len(small) == len(large) == onion_size(1, 4)


def test_bucket_boundaries() -> None:
    assert padded_payload_size(0) == PAYLOAD_BUCKET
    assert padded_payload_size(PAYLOAD_BUCKET - 4) == PAYLOAD_BUCKET
    assert padded_payload_size(PAYLOAD_BUCKET - 3) == 2 * PAYLOAD_BUCKET


def test_layer_overhead_is_constant(nodes: dict[str, X25519PrivateKey]) -> None:
    """Documents the known limitation: size reveals layers remaining."""
    onion = build_onion(_route(nodes, NAMES), b"x")
    sizes = []

    for name in NAMES:
        sizes.append(len(onion))
        onion = peel_layer(onion, nodes[name]).payload

    assert LAYER_OVERHEAD == 127
    assert [a - b for a, b in zip(sizes, sizes[1:], strict=False)] == [
        LAYER_OVERHEAD
    ] * 3


# ============================================================
# REJECTION
# ============================================================


@pytest.mark.parametrize("hop_index", range(4))
@pytest.mark.parametrize("region", ["version", "ephemeral", "nonce", "body", "tag"])
def test_tampering_at_any_hop_is_rejected_by_that_hop(
    nodes: dict[str, X25519PrivateKey], hop_index: int, region: str
) -> None:
    onion = build_onion(_route(nodes, NAMES), os.urandom(500))

    for name in NAMES[:hop_index]:
        onion = peel_layer(onion, nodes[name]).payload

    pos = {"version": 0, "ephemeral": 5, "nonce": 40, "body": 200, "tag": -1}[region]
    tampered = _flip(onion, pos % len(onion))

    with pytest.raises(LayerDecryptError):
        peel_layer(tampered, nodes[NAMES[hop_index]])


def test_body_tamper_surfaces_invalid_tag(nodes: dict[str, X25519PrivateKey]) -> None:
    onion = _flip(build_onion(_route(nodes, NAMES), b"x"), 300)

    with pytest.raises(LayerDecryptError) as info:
        peel_layer(onion, nodes["guard"])

    assert isinstance(info.value.__cause__, InvalidTag)


def test_wrong_node_cannot_peel(nodes: dict[str, X25519PrivateKey]) -> None:
    onion = build_onion(_route(nodes, NAMES), b"x")

    for name in ["middle", "exit", "receiver"]:
        with pytest.raises(LayerDecryptError):
            peel_layer(onion, nodes[name])


def test_layers_cannot_be_skipped(nodes: dict[str, X25519PrivateKey]) -> None:
    """A guard that drops a layer's prefix cannot hand exit a valid layer."""
    onion = build_onion(_route(nodes, NAMES), b"x")
    middle_layer = peel_layer(onion, nodes["guard"]).payload

    with pytest.raises(LayerDecryptError):
        peel_layer(middle_layer, nodes["exit"])


@pytest.mark.parametrize("cut", [0, 1, LAYER_OVERHEAD - 1, LAYER_OVERHEAD + 10])
def test_truncated_onion(nodes: dict[str, X25519PrivateKey], cut: int) -> None:
    onion = build_onion(_route(nodes, NAMES), b"x")

    with pytest.raises(LayerDecryptError):
        peel_layer(onion[:cut], nodes["guard"])


def test_low_order_ephemeral_key_rejected(nodes: dict[str, X25519PrivateKey]) -> None:
    onion = build_onion(_route(nodes, NAMES), b"x")
    forged = onion[:1] + bytes(32) + onion[33:]

    with pytest.raises(LayerDecryptError):
        peel_layer(forged, nodes["guard"])


def test_layer_bound_to_recipient_pubkey(nodes: dict[str, X25519PrivateKey]) -> None:
    """KDF salt includes the node's own public key."""
    assert len(public_key_bytes(nodes["guard"])) == 32
    onion = build_onion([Hop("guard", nodes["guard"].public_key())], b"x")

    with pytest.raises(LayerDecryptError):
        peel_layer(onion, generate_private_key())


# ============================================================
# BUILD VALIDATION
# ============================================================


def test_empty_route_rejected() -> None:
    with pytest.raises(ValueError, match="at least one hop"):
        build_onion([], b"x")


@pytest.mark.parametrize("bad_id", ["", "x" * (MAX_NODE_ID_BYTES + 1), "é" * 33])
def test_bad_node_ids_rejected(bad_id: str) -> None:
    with pytest.raises(ValueError, match="node_id"):
        build_onion([Hop(bad_id, generate_private_key().public_key())], b"x")


# ============================================================
# INSPECTION HELPERS
# ============================================================


def test_layer_parts_split_public_fields(nodes: dict[str, X25519PrivateKey]) -> None:
    onion = build_onion(_route(nodes, NAMES), b"x")
    parts = layer_parts(onion)

    assert parts.version == 2
    assert len(parts.ephemeral_key) == 32 and len(parts.nonce) == 12
    assert len(parts.tag) == 16
    assert parts.prefix + parts.ciphertext + parts.tag == onion
    with pytest.raises(ValueError):
        layer_parts(onion[:20])


@pytest.mark.parametrize("payload_len", [0, 1, 4092, 4093, 300_000])
@pytest.mark.parametrize("hops", [1, 2, 3, 4, 6])
def test_layers_remaining_is_recoverable_from_size(payload_len: int, hops: int) -> None:
    """The documented size leak: any relay can count the layers left."""
    assert layers_remaining(onion_size(payload_len, hops)) == hops


def test_layers_remaining_rejects_impossible_sizes() -> None:
    assert layers_remaining(10) is None
    assert layers_remaining(PAYLOAD_BUCKET + 1) is None
