from __future__ import annotations

import pytest

from mpos.faults.injector import CLEAN, FaultInjector, LinkFaults


@pytest.mark.parametrize(
    "kwargs",
    [{"drop": -0.1}, {"corrupt": 1.5}, {"truncate": 2}, {"delay_ms": -1}],
)
def test_invalid_fault_values(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        LinkFaults(**kwargs)


def test_no_rules_means_clean_and_no_randomness_used() -> None:
    injector = FaultInjector(seed=1)
    state = injector.rng.getstate()

    assert injector.plan("a", "b") is CLEAN
    assert injector.rng.getstate() == state
    assert not injector.active


def test_rule_precedence_link_then_node_then_global() -> None:
    injector = FaultInjector()
    glob, node, link = LinkFaults(drop=0.1), LinkFaults(drop=0.2), LinkFaults(drop=0.3)
    injector.set_global(glob)
    injector.set_node("a", node)
    injector.set_link("a", "b", link)

    assert injector.rule_for("a", "b") is link
    assert injector.rule_for("a", "c") is node
    assert injector.rule_for("x", "y") is glob

    injector.set_link("a", "b", None)
    injector.set_node("a", LinkFaults())  # an inactive rule removes the override
    assert injector.rule_for("a", "b") is glob


def test_certain_faults() -> None:
    injector = FaultInjector(seed=3)
    injector.set_global(LinkFaults(drop=1.0, delay_ms=50))
    plan = injector.plan("a", "b")

    assert plan.drop and plan.delay == pytest.approx(0.05)
    assert injector.stats.dropped == injector.stats.delayed == 1


def test_probabilities_are_respected() -> None:
    injector = FaultInjector(seed=7)
    injector.set_global(LinkFaults(drop=0.3, duplicate=0.5))
    plans = [injector.plan("a", "b") for _ in range(10_000)]

    assert sum(p.drop for p in plans) / 10_000 == pytest.approx(0.3, abs=0.02)
    kept = [p for p in plans if not p.drop]
    assert sum(p.duplicate for p in kept) / len(kept) == pytest.approx(0.5, abs=0.02)
    assert injector.stats.dropped == sum(p.drop for p in plans)


def test_same_seed_same_decisions() -> None:
    def run(seed: int) -> list[object]:
        injector = FaultInjector(seed)
        injector.set_global(
            LinkFaults(drop=0.2, corrupt=0.2, jitter_ms=30, delay_ms=40)
        )
        return [injector.plan("a", "b") for _ in range(200)]

    assert run(11) == run(11)
    assert run(11) != run(12)


def test_jitter_never_makes_delay_negative() -> None:
    injector = FaultInjector(seed=1)
    injector.set_global(LinkFaults(delay_ms=5, jitter_ms=50))

    assert all(injector.plan("a", "b").delay >= 0 for _ in range(500))


def test_corrupt_flips_one_bit_after_the_layer_prefix() -> None:
    injector = FaultInjector(seed=5)
    onion = bytes(range(256)) * 4

    for _ in range(200):
        damaged = injector.corrupt(onion)
        diffs = [
            i for i, (x, y) in enumerate(zip(onion, damaged, strict=True)) if x != y
        ]
        assert len(diffs) == 1 and diffs[0] >= 45
        assert bin(onion[diffs[0]] ^ damaged[diffs[0]]).count("1") == 1

    assert injector.corrupt(b"") == b""


def test_partition_is_symmetric_and_heals() -> None:
    injector = FaultInjector()
    injector.partition({"a", "b"}, {"c"})

    assert injector.is_partitioned("a", "c") and injector.is_partitioned("c", "b")
    assert not injector.is_partitioned("a", "b")
    assert injector.stats.partitioned == 2

    injector.heal()
    assert not injector.is_partitioned("a", "c")
    with pytest.raises(ValueError):
        injector.partition({"a"}, {"a", "b"})


def test_blackhole_and_clear() -> None:
    injector = FaultInjector()
    injector.blackhole("r1")
    injector.set_global(LinkFaults(drop=0.5))

    assert injector.swallows("r1") and not injector.swallows("r2")
    assert injector.active
    assert any("blackhole: r1" in line for line in injector.describe())

    injector.clear()
    assert not injector.active and injector.describe() == []
    assert injector.stats.blackholed == 1  # statistics survive a clear


def test_replay_cache_expires_and_is_bounded() -> None:
    from mpos.net.node import ReplayCache

    now = [0.0]
    cache = ReplayCache(max_entries=3, ttl=10.0, clock=lambda: now[0])

    cache.add(b"a")
    assert cache.seen(b"a") and not cache.seen(b"b")

    now[0] = 11.0
    assert not cache.seen(b"a")  # expired

    for key in (b"1", b"2", b"3", b"4"):
        cache.add(key)
    assert len(cache) == 3 and not cache.seen(b"1")  # oldest evicted


def test_parse_fault_spec() -> None:
    from mpos.faults.injector import parse_fault_spec

    faults, blackhole = parse_fault_spec("drop=0.2, delay=50,jitter=10,corrupt=0.05")
    assert faults == LinkFaults(drop=0.2, delay_ms=50, jitter_ms=10, corrupt=0.05)
    assert not blackhole

    assert parse_fault_spec("blackhole") == (LinkFaults(), True)
    assert parse_fault_spec("") == (LinkFaults(), False)
    for bad in ("drop", "lose=0.1", "drop=lots", "drop=2"):
        with pytest.raises(ValueError):
            parse_fault_spec(bad)
