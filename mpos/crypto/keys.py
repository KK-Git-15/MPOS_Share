"""X25519 identity keys and HKDF-SHA256 key agreement.

Every node has a long-term X25519 identity key. Its public half is
published in nodes.json as base64 of the raw 32 bytes; the private half is
stored as an unencrypted PKCS#8 PEM with mode 0600 and is never sent
anywhere.
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PUBLIC_KEY_SIZE: Final = 32
DERIVED_KEY_SIZE: Final = 32

_ALL_ZERO_SECRET: Final = bytes(32)


# ============================================================
# IDENTITY KEYS
# ============================================================


def generate_private_key() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


def save_private_key(
    key: X25519PrivateKey, path: Path, *, overwrite: bool = False
) -> None:
    """Write ``key`` as PEM, readable only by the owner (mode 0600).

    Refuses to replace an existing identity unless ``overwrite`` is set,
    since losing it invalidates this node's nodes.json entry.
    """
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    fd = os.open(path, flags, 0o600)

    with os.fdopen(fd, "wb") as f:
        f.write(pem)

    os.chmod(path, 0o600)  # O_CREAT mode is ignored when the file already existed


def load_private_key(path: Path) -> X25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)

    if not isinstance(key, X25519PrivateKey):
        raise ValueError(f"{path} does not contain an X25519 private key")

    return key


# ============================================================
# PUBLIC KEY ENCODING
# ============================================================


def public_key_bytes(key: X25519PrivateKey | X25519PublicKey) -> bytes:
    """Raw 32-byte public key."""
    public = key.public_key() if isinstance(key, X25519PrivateKey) else key

    return public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_from_bytes(raw: bytes) -> X25519PublicKey:
    if len(raw) != PUBLIC_KEY_SIZE:
        raise ValueError(f"X25519 public key must be {PUBLIC_KEY_SIZE} bytes")

    return X25519PublicKey.from_public_bytes(raw)


def encode_public_key(key: X25519PrivateKey | X25519PublicKey) -> str:
    """Base64 form used in nodes.json."""
    return base64.b64encode(public_key_bytes(key)).decode("ascii")


def decode_public_key(text: str) -> X25519PublicKey:
    try:
        raw = base64.b64decode(text, validate=True)
    except binascii.Error as e:
        raise ValueError(f"Invalid base64 public key: {e}") from e

    return public_key_from_bytes(raw)


# ============================================================
# KEY AGREEMENT
# ============================================================


def derive_shared_key(
    private: X25519PrivateKey,
    peer_public: X25519PublicKey,
    *,
    salt: bytes,
    info: bytes,
    length: int = DERIVED_KEY_SIZE,
) -> bytes:
    """X25519 ECDH followed by HKDF-SHA256.

    Raises:
        ValueError: the peer key is a low-order point (all-zero secret).
    """
    shared = private.exchange(peer_public)

    if shared == _ALL_ZERO_SECRET:
        raise ValueError("X25519 produced an all-zero shared secret")

    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(shared)
