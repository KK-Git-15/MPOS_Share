from __future__ import annotations

import itertools
import os
from pathlib import Path

import pytest

from mpos.coding.erasure import Shard, decode, encode, padding_length, shard_size
from mpos.errors import ReassemblyError

REPO_ROOT = Path(__file__).resolve().parents[2]

# Sizes chosen to hit: single byte, < K, == K, non-multiple of K, Input.txt, 1 MiB+1
SIZES = [1, 3, 4, 5, 180, 1001, 1024 * 1024 + 1]


@pytest.mark.parametrize("size", SIZES)
def test_every_k_subset_rebuilds_exact_bytes(size: int) -> None:
    data = os.urandom(size)
    seg = encode(data, k=4, n=6)

    assert len(seg.shards) == 6
    assert {s.index for s in seg.shards} == set(range(6))

    for r in (4, 5, 6):
        for subset in itertools.combinations(seg.shards, r):
            assert decode(subset, 4, 6, size) == data


@pytest.mark.parametrize(("k", "n"), [(1, 1), (1, 3), (3, 3), (6, 8), (10, 14)])
def test_other_k_n(k: int, n: int) -> None:
    data = os.urandom(4097)
    seg = encode(data, k, n)

    # Use the last K shards (forces parity shards whenever N > K)
    assert decode(seg.shards[-k:], k, n, len(data)) == data


def test_shard_order_does_not_matter() -> None:
    data = os.urandom(999)
    seg = encode(data, 4, 6)
    shuffled = [seg.shards[5], seg.shards[1], seg.shards[4], seg.shards[2]]

    assert decode(shuffled, 4, 6, len(data)) == data


def test_fewer_than_k_shards_rejected() -> None:
    seg = encode(b"x" * 100, 4, 6)

    with pytest.raises(ReassemblyError, match="Need 4"):
        decode(seg.shards[:3], 4, 6, 100)


def test_duplicates_do_not_count_towards_k() -> None:
    seg = encode(b"x" * 100, 4, 6)
    dupes = [seg.shards[0], seg.shards[0], seg.shards[1], seg.shards[2]]

    with pytest.raises(ReassemblyError):
        decode(dupes, 4, 6, 100)


def test_out_of_range_index_rejected() -> None:
    seg = encode(b"x" * 100, 4, 6)
    bad = [*seg.shards[:3], Shard(6, seg.shards[3].data)]

    with pytest.raises(ReassemblyError, match="outside"):
        decode(bad, 4, 6, 100)


def test_wrong_size_shard_rejected() -> None:
    seg = encode(b"x" * 100, 4, 6)
    bad = [*seg.shards[:3], Shard(3, seg.shards[3].data + b"\0")]

    with pytest.raises(ReassemblyError, match="expected"):
        decode(bad, 4, 6, 100)


@pytest.mark.parametrize(("k", "n"), [(0, 6), (5, 4), (1, 257)])
def test_invalid_parameters(k: int, n: int) -> None:
    with pytest.raises(ValueError):
        encode(b"data", k, n)


def test_empty_data_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        encode(b"", 4, 6)


def test_size_helpers() -> None:
    assert shard_size(180, 4) == 45
    assert padding_length(180, 4) == 0
    assert shard_size(181, 4) == 46
    assert padding_length(181, 4) == 3


def test_matches_phase1_shards_on_disk() -> None:
    """Same input + K/N must reproduce Phase_1/encoder.py output byte-for-byte."""
    shard_dir = REPO_ROOT / "shards"
    if not (shard_dir / "shard_00.bin").exists():
        pytest.skip("Run Phase_1/encoder.py first to generate shards/")

    data = (REPO_ROOT / "Input" / "Input.txt").read_bytes()
    seg = encode(data, 4, 6)

    for shard in seg.shards:
        assert shard.data == (shard_dir / f"shard_{shard.index:02d}.bin").read_bytes()
