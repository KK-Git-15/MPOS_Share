from __future__ import annotations

import itertools
import os
import uuid
from pathlib import Path

import pytest

from mpos.coding import erasure
from mpos.coding.shard_crypto import (
    KEY_SIZE,
    NONCE_SIZE,
    TAG_SIZE,
    decrypt_shard,
    derive_shard_key,
    encrypt_shard,
    generate_key,
    shard_aad,
)
from mpos.errors import DecryptError

REPO_ROOT = Path(__file__).resolve().parents[2]
TID = uuid.UUID(int=1).bytes


def _flip(blob: bytes, pos: int) -> bytes:
    return blob[:pos] + bytes([blob[pos] ^ 0x01]) + blob[pos + 1 :]


def test_roundtrip_and_blob_layout() -> None:
    key = generate_key()
    pt = os.urandom(1000)
    blob = encrypt_shard(pt, key)

    assert len(key) == KEY_SIZE
    assert len(blob) == NONCE_SIZE + len(pt) + TAG_SIZE
    assert decrypt_shard(blob, key) == pt


def test_roundtrip_with_aad() -> None:
    key = generate_key()
    aad = shard_aad(TID, 3, 5)
    blob = encrypt_shard(b"payload", key, aad)

    assert decrypt_shard(blob, key, aad) == b"payload"


def test_nonce_is_fresh_each_time() -> None:
    key = generate_key()

    assert (
        encrypt_shard(b"same", key)[:NONCE_SIZE]
        != encrypt_shard(b"same", key)[:NONCE_SIZE]
    )


@pytest.mark.parametrize("region", ["nonce", "ciphertext", "tag"])
def test_any_bit_flip_is_rejected(region: str) -> None:
    key = generate_key()
    blob = encrypt_shard(os.urandom(64), key)
    pos = {"nonce": 0, "ciphertext": NONCE_SIZE + 10, "tag": len(blob) - 1}[region]

    with pytest.raises(DecryptError):
        decrypt_shard(_flip(blob, pos), key)


def test_wrong_key_rejected() -> None:
    blob = encrypt_shard(b"secret", generate_key())

    with pytest.raises(DecryptError):
        decrypt_shard(blob, generate_key())


def test_aad_mismatch_rejected() -> None:
    """A shard replayed under another position must not authenticate."""
    key = generate_key()
    blob = encrypt_shard(b"secret", key, shard_aad(TID, 0, 1))

    for wrong in (shard_aad(TID, 0, 2), shard_aad(TID, 1, 1), b""):
        with pytest.raises(DecryptError):
            decrypt_shard(blob, key, wrong)


@pytest.mark.parametrize("length", [0, NONCE_SIZE, NONCE_SIZE + TAG_SIZE - 1])
def test_truncated_blob_rejected(length: int) -> None:
    key = generate_key()
    blob = encrypt_shard(b"secret", key)

    with pytest.raises(DecryptError):
        decrypt_shard(blob[:length], key)


def test_bad_key_length() -> None:
    with pytest.raises(ValueError):
        encrypt_shard(b"x", b"short")
    with pytest.raises(ValueError):
        decrypt_shard(b"\0" * 40, b"short")


def test_derived_keys_are_deterministic_and_distinct() -> None:
    file_key = generate_key()
    keys = {
        (seg, sid): derive_shard_key(file_key, TID, seg, sid)
        for seg, sid in itertools.product(range(3), range(6))
    }

    assert derive_shard_key(file_key, TID, 2, 5) == keys[(2, 5)]
    assert len(set(keys.values())) == len(keys)
    assert all(len(k) == KEY_SIZE for k in keys.values())
    assert derive_shard_key(file_key, uuid.UUID(int=2).bytes, 0, 0) != keys[(0, 0)]


def test_position_validation() -> None:
    with pytest.raises(ValueError):
        shard_aad(b"short", 0, 0)
    with pytest.raises(ValueError):
        shard_aad(TID, -1, 0)
    with pytest.raises(ValueError):
        derive_shard_key(b"short", TID, 0, 0)


def test_full_sender_receiver_pipeline_with_two_lost_shards() -> None:
    """encode -> encrypt -> lose 2 of 6 -> decrypt -> decode == original."""
    data = os.urandom(50_000)
    file_key = generate_key()
    seg = erasure.encode(data, 4, 6)

    sent = {
        s.index: encrypt_shard(
            s.data,
            derive_shard_key(file_key, TID, 0, s.index),
            shard_aad(TID, 0, s.index),
        )
        for s in seg.shards
    }

    for lost in itertools.combinations(range(6), 2):
        received = [
            erasure.Shard(
                i,
                decrypt_shard(
                    blob, derive_shard_key(file_key, TID, 0, i), shard_aad(TID, 0, i)
                ),
            )
            for i, blob in sent.items()
            if i not in lost
        ]

        assert erasure.decode(received, 4, 6, len(data)) == data


def test_decrypts_phase2_files_on_disk() -> None:
    """Empty AAD keeps wire compatibility with Phase_2/encryptor.py output."""
    enc_dir, key_dir = REPO_ROOT / "encrypted_shards", REPO_ROOT / "keys"
    if not (enc_dir / "encrypted_shard_00.bin").exists():
        pytest.skip("Run Phase_2/encryptor.py first")

    for i in range(6):
        blob = (enc_dir / f"encrypted_shard_{i:02d}.bin").read_bytes()
        key = (key_dir / f"key_{i:02d}.bin").read_bytes()
        expected = (REPO_ROOT / "shards" / f"shard_{i:02d}.bin").read_bytes()

        assert decrypt_shard(blob, key) == expected
