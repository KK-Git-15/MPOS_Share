from __future__ import annotations

import base64
import stat
from pathlib import Path

import pytest

from mpos.crypto.keys import (
    decode_public_key,
    derive_shared_key,
    encode_public_key,
    generate_private_key,
    load_private_key,
    public_key_bytes,
    public_key_from_bytes,
    save_private_key,
)


def test_save_load_roundtrip_with_owner_only_permissions(tmp_path: Path) -> None:
    key = generate_private_key()
    path = tmp_path / "alpha" / "identity.pem"

    save_private_key(key, path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert public_key_bytes(load_private_key(path)) == public_key_bytes(key)


def test_refuses_to_overwrite_identity(tmp_path: Path) -> None:
    path = tmp_path / "identity.pem"
    first = generate_private_key()
    save_private_key(first, path)

    with pytest.raises(FileExistsError):
        save_private_key(generate_private_key(), path)

    assert public_key_bytes(load_private_key(path)) == public_key_bytes(first)


def test_overwrite_when_asked(tmp_path: Path) -> None:
    path = tmp_path / "identity.pem"
    path.write_bytes(b"old")
    path.chmod(0o644)
    key = generate_private_key()

    save_private_key(key, path, overwrite=True)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert public_key_bytes(load_private_key(path)) == public_key_bytes(key)


def test_load_rejects_non_x25519_key(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path = tmp_path / "ed.pem"
    path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    with pytest.raises(ValueError, match="X25519"):
        load_private_key(path)


def test_public_key_base64_roundtrip() -> None:
    key = generate_private_key()
    text = encode_public_key(key)

    assert len(base64.b64decode(text)) == 32
    assert public_key_bytes(decode_public_key(text)) == public_key_bytes(key)
    assert encode_public_key(key.public_key()) == text


@pytest.mark.parametrize("text", ["not base64!!", base64.b64encode(b"short").decode()])
def test_bad_public_key_text(text: str) -> None:
    with pytest.raises(ValueError):
        decode_public_key(text)


def test_ecdh_both_sides_agree() -> None:
    a, b = generate_private_key(), generate_private_key()

    k_ab = derive_shared_key(a, b.public_key(), salt=b"s", info=b"i")
    k_ba = derive_shared_key(b, a.public_key(), salt=b"s", info=b"i")

    assert k_ab == k_ba
    assert len(k_ab) == 32


def test_salt_and_info_separate_keys() -> None:
    a, b = generate_private_key(), generate_private_key()
    base = derive_shared_key(a, b.public_key(), salt=b"s", info=b"i")

    assert derive_shared_key(a, b.public_key(), salt=b"t", info=b"i") != base
    assert derive_shared_key(a, b.public_key(), salt=b"s", info=b"j") != base


def test_low_order_point_rejected() -> None:
    with pytest.raises(ValueError):
        derive_shared_key(
            generate_private_key(), public_key_from_bytes(bytes(32)), salt=b"", info=b""
        )
