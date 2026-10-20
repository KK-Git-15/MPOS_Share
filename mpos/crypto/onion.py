"""Self-routing v2 onions (CLAUDE.md §3.1.3).

Replaces Phase_5/onion.py. Relays need no pre-shared key file: the
sender derives each hop's key from a fresh ephemeral X25519 key and the
hop's published identity key, and the next hop travels *inside* the
encrypted layer instead of being fixed when the relay starts.

Layer format:

    [1B version][32B ephemeral pubkey][12B nonce][AES-256-GCM(plaintext) || 16B tag]

    key       = HKDF-SHA256(X25519(eph, node), salt = eph_pub || node_pub,
                            info = "mpos-onion-v2")
    AAD       = version || eph_pub
    plaintext = routing header (66 B) || inner

Routing header: [1B command][1B node_id length][64B node_id, zero padded].
RELAY layers carry the next node_id and the next layer as ``inner``.
The DELIVER layer (last hop) carries ``[4B length][payload][zero padding]``
padded to a 4 KiB bucket, so relays cannot learn the real payload size.

Known limitation: each layer adds a constant LAYER_OVERHEAD bytes, so a
relay can infer how many layers remain from the size it receives.
Hiding that needs a Sphinx-style fixed-length header; out of scope.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mpos.crypto.keys import (
    PUBLIC_KEY_SIZE,
    derive_shared_key,
    public_key_bytes,
    public_key_from_bytes,
)
from mpos.errors import LayerDecryptError

ONION_VERSION: Final = 2
NONCE_SIZE: Final = 12
TAG_SIZE: Final = 16
MAX_NODE_ID_BYTES: Final = 64
PAYLOAD_BUCKET: Final = 4096

_KDF_INFO: Final = b"mpos-onion-v2"
_ROUTING: Final = struct.Struct(f"!BB{MAX_NODE_ID_BYTES}s")
_LENGTH: Final = struct.Struct("!I")

ROUTING_HEADER_SIZE: Final = _ROUTING.size  # 66
_PREFIX_SIZE: Final = 1 + PUBLIC_KEY_SIZE + NONCE_SIZE
LAYER_OVERHEAD: Final = _PREFIX_SIZE + TAG_SIZE + ROUTING_HEADER_SIZE  # 127


# ============================================================
# TYPES
# ============================================================


class Command(IntEnum):
    RELAY = 1
    DELIVER = 2


@dataclass(frozen=True, slots=True)
class Hop:
    """One node on a route: its directory id and identity public key."""

    node_id: str
    public_key: X25519PublicKey


@dataclass(frozen=True, slots=True)
class PeeledLayer:
    """Result of removing one layer.

    RELAY:   ``next_hop`` is set and ``payload`` is the onion to forward.
    DELIVER: ``next_hop`` is None and ``payload`` is the original payload.
    """

    command: Command
    next_hop: str | None
    payload: bytes


# ============================================================
# HELPERS
# ============================================================


def _encode_node_id(node_id: str) -> bytes:
    raw = node_id.encode("utf-8")

    if not 1 <= len(raw) <= MAX_NODE_ID_BYTES:
        raise ValueError(f"node_id must be 1..{MAX_NODE_ID_BYTES} UTF-8 bytes")

    return raw


def _layer_key(
    private: X25519PrivateKey, peer: X25519PublicKey, eph_pub: bytes, node_pub: bytes
) -> bytes:
    return derive_shared_key(private, peer, salt=eph_pub + node_pub, info=_KDF_INFO)


def padded_payload_size(payload_len: int) -> int:
    """Size of the DELIVER body after length prefix and bucket padding."""
    raw = _LENGTH.size + payload_len
    return -(-raw // PAYLOAD_BUCKET) * PAYLOAD_BUCKET


def onion_size(payload_len: int, hops: int) -> int:
    """Total onion size for a payload sent over ``hops`` layers."""
    return padded_payload_size(payload_len) + hops * LAYER_OVERHEAD


# ============================================================
# INSPECTION (public structure only, no keys involved)
# ============================================================


@dataclass(frozen=True, slots=True)
class LayerParts:
    """One layer as any observer of the wire sees it."""

    version: int
    ephemeral_key: bytes
    nonce: bytes
    ciphertext: bytes  # encrypted routing header + inner onion
    tag: bytes

    @property
    def prefix(self) -> bytes:
        """version || ephemeral key || nonce: the unencrypted layer prefix."""
        return bytes([self.version]) + self.ephemeral_key + self.nonce


def layer_parts(onion: bytes) -> LayerParts:
    """Split a layer into its public fields. Raises ValueError if too short."""
    if len(onion) < _PREFIX_SIZE + TAG_SIZE:
        raise ValueError("Too short to be an onion layer")

    return LayerParts(
        version=onion[0],
        ephemeral_key=onion[1 : 1 + PUBLIC_KEY_SIZE],
        nonce=onion[1 + PUBLIC_KEY_SIZE : _PREFIX_SIZE],
        ciphertext=onion[_PREFIX_SIZE:-TAG_SIZE],
        tag=onion[-TAG_SIZE:],
    )


def layers_remaining(onion_len: int) -> int | None:
    """How many layers an onion of ``onion_len`` bytes still carries.

    This is the size leak described in the module docstring, computed the
    way any relay could: every layer adds LAYER_OVERHEAD (127) bytes to a
    payload padded to a multiple of PAYLOAD_BUCKET (4096), and since 127
    and 4096 are coprime only one layer count fits. None if no count fits.
    """
    layers = 1
    while (rest := onion_len - layers * LAYER_OVERHEAD) >= PAYLOAD_BUCKET:
        if rest % PAYLOAD_BUCKET == 0:
            return layers
        layers += 1
    return None


def _wrap_layer(
    hop: Hop, command: Command, next_hop: str | None, inner: bytes
) -> bytes:
    next_raw = _encode_node_id(next_hop) if next_hop is not None else b""
    routing = _ROUTING.pack(command, len(next_raw), next_raw)

    eph = X25519PrivateKey.generate()
    eph_pub = public_key_bytes(eph)
    key = _layer_key(eph, hop.public_key, eph_pub, public_key_bytes(hop.public_key))

    prefix = bytes([ONION_VERSION]) + eph_pub
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, routing + inner, prefix)

    return prefix + nonce + ciphertext


# ============================================================
# BUILD
# ============================================================


def build_onion(route: Sequence[Hop], payload: bytes) -> bytes:
    """Wrap ``payload`` so that route[0] peels first and route[-1] delivers.

    Layers are applied innermost first: route[-1], ..., route[0].
    """
    if not route:
        raise ValueError("Route must contain at least one hop")

    for hop in route:
        _encode_node_id(hop.node_id)

    body = _LENGTH.pack(len(payload)) + payload
    inner = body + bytes(padded_payload_size(len(payload)) - len(body))

    inner = _wrap_layer(route[-1], Command.DELIVER, None, inner)

    for i in range(len(route) - 2, -1, -1):
        inner = _wrap_layer(route[i], Command.RELAY, route[i + 1].node_id, inner)

    return inner


# ============================================================
# PEEL
# ============================================================


def peel_layer(onion: bytes, private_key: X25519PrivateKey) -> PeeledLayer:
    """Remove the outermost layer with this node's identity key.

    Raises:
        LayerDecryptError: malformed layer, wrong key, or any tampering.
            The caller must drop the packet (CLAUDE.md §2.4).
    """
    if len(onion) < LAYER_OVERHEAD:
        raise LayerDecryptError("Onion layer too short")

    if onion[0] != ONION_VERSION:
        raise LayerDecryptError(f"Unsupported onion version {onion[0]}")

    prefix = onion[: 1 + PUBLIC_KEY_SIZE]
    eph_pub = prefix[1:]
    nonce = onion[1 + PUBLIC_KEY_SIZE : _PREFIX_SIZE]

    try:
        key = _layer_key(
            private_key,
            public_key_from_bytes(eph_pub),
            eph_pub,
            public_key_bytes(private_key),
        )
        plaintext = AESGCM(key).decrypt(nonce, onion[_PREFIX_SIZE:], prefix)
    except InvalidTag as e:
        raise LayerDecryptError("Onion layer authentication failed") from e
    except ValueError as e:  # low-order ephemeral key
        raise LayerDecryptError(f"Invalid ephemeral key: {e}") from e

    command_raw, id_len, id_field = _ROUTING.unpack(plaintext[:ROUTING_HEADER_SIZE])
    inner = plaintext[ROUTING_HEADER_SIZE:]

    # Authenticated by GCM, so anything malformed here is a sender bug.
    try:
        command = Command(command_raw)
    except ValueError:
        raise LayerDecryptError(f"Unknown onion command {command_raw}") from None

    if command is Command.RELAY:
        if not 1 <= id_len <= MAX_NODE_ID_BYTES:
            raise LayerDecryptError("RELAY layer without a valid next hop")
        try:
            next_hop = id_field[:id_len].decode("utf-8")
        except UnicodeDecodeError:
            raise LayerDecryptError("next hop is not valid UTF-8") from None
        return PeeledLayer(command, next_hop, inner)

    if len(inner) < _LENGTH.size:
        raise LayerDecryptError("DELIVER body too short")

    (length,) = _LENGTH.unpack(inner[: _LENGTH.size])
    end = _LENGTH.size + length

    if end > len(inner):
        raise LayerDecryptError("DELIVER length exceeds body")

    return PeeledLayer(command, None, inner[_LENGTH.size : end])
