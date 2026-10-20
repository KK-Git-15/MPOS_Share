from __future__ import annotations

import random
from collections import Counter

import pytest

from mpos.crypto.keys import generate_private_key
from mpos.net.directory import Directory, NodeInfo
from mpos.net.paths import choose_path


def _directory(relays: int, *, extra: tuple[str, ...] = ("alice", "bob")) -> Directory:
    names = [*extra, *(f"r{i}" for i in range(relays))]
    return Directory(
        NodeInfo(name, "127.0.0.1", 9000 + i, generate_private_key().public_key())
        for i, name in enumerate(names)
    )


def test_distinct_hops_and_endpoints_excluded() -> None:
    directory = _directory(5)
    rng = random.Random(0)

    for _ in range(200):
        path = choose_path(directory, hops=3, exclude={"alice", "bob"}, rng=rng)
        assert len(set(path)) == 3
        assert not {"alice", "bob"} & set(path)


def test_too_few_relays() -> None:
    with pytest.raises(ValueError, match="Need 3 relays"):
        choose_path(
            _directory(2), hops=3, exclude={"alice", "bob"}, rng=random.Random()
        )


def test_relay_role_required() -> None:
    directory = _directory(3)
    only = directory.get("r0")
    directory.add(NodeInfo("r0", only.host, only.port, only.public_key, ("receiver",)))

    with pytest.raises(ValueError):
        choose_path(directory, hops=3, exclude={"alice", "bob"}, rng=random.Random())


def test_usage_spreads_paths_over_disjoint_nodes() -> None:
    directory = _directory(6)
    usage: Counter[str] = Counter()
    rng = random.Random(1)

    a = choose_path(directory, hops=3, exclude={"alice", "bob"}, rng=rng, usage=usage)
    b = choose_path(directory, hops=3, exclude={"alice", "bob"}, rng=rng, usage=usage)

    assert not set(a) & set(b)
    assert sum(usage.values()) == 6


def test_penalized_nodes_avoided_while_alternatives_exist() -> None:
    directory = _directory(5)
    penalty = Counter({"r0": 3, "r1": 1})
    rng = random.Random(2)

    for _ in range(50):
        path = choose_path(
            directory, hops=3, exclude={"alice", "bob"}, rng=rng, penalty=penalty
        )
        assert set(path) == {"r2", "r3", "r4"}

    # With every other relay excluded, penalized nodes are still usable
    path = choose_path(
        directory,
        hops=3,
        exclude={"alice", "bob", "r3", "r4"},
        rng=rng,
        penalty=penalty,
    )
    assert set(path) == {"r0", "r1", "r2"}


def test_seeded_rng_is_reproducible() -> None:
    directory = _directory(8)

    def paths(seed: int) -> list[list[str]]:
        rng = random.Random(seed)
        return [
            choose_path(directory, hops=3, exclude={"alice"}, rng=rng) for _ in range(5)
        ]

    assert paths(7) == paths(7)
