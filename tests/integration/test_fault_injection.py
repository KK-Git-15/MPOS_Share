"""Each injected fault has exactly its intended effect on a real network."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable

import pytest

from mpos.crypto.keys import generate_private_key
from mpos.crypto.onion import Hop, build_onion
from mpos.errors import HopUnreachable
from mpos.events import FaultInjected, Subscription
from mpos.faults.injector import FaultInjector, LinkFaults
from mpos.net.directory import Directory, NodeInfo
from mpos.net.node import NodeDaemon

NAMES = ["alice", "guard", "middle", "exit", "bob"]
ROUTE = ["guard", "middle", "exit", "bob"]

Net = tuple[dict[str, NodeDaemon], FaultInjector, Subscription]


@pytest.fixture
async def net() -> AsyncIterator[Net]:
    directory = Directory()
    faults = FaultInjector(seed=42)
    nodes: dict[str, NodeDaemon] = {}
    bus_owner = None

    for name in NAMES:
        key = generate_private_key()
        node = NodeDaemon(
            name,
            key,
            directory,
            events=bus_owner.events if bus_owner else None,
            faults=faults,
        )
        bus_owner = bus_owner or node
        port = await node.start()
        directory.add(NodeInfo(name, "127.0.0.1", port, key.public_key()))
        nodes[name] = node

    sub = nodes["alice"].events.subscribe()
    yield nodes, faults, sub
    for node in nodes.values():
        await node.stop()


def _route(nodes: dict[str, NodeDaemon]) -> list[Hop]:
    return [nodes["alice"].directory.get(n).hop for n in ROUTE]


async def _send(nodes: dict[str, NodeDaemon], payload: bytes = b"x") -> None:
    await nodes["alice"].send_onion("guard", build_onion(_route(nodes), payload))


async def _wait(predicate: Callable[[], bool], limit: float = 5) -> None:
    async with asyncio.timeout(limit):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


def _faults_seen(sub: Subscription) -> list[FaultInjected]:
    seen = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if isinstance(event, FaultInjected):
            seen.append(event)
    return seen


async def test_clean_network_unaffected(net: Net) -> None:
    nodes, faults, _ = net
    await _send(nodes, b"hello")

    async with asyncio.timeout(5):
        assert await nodes["bob"].delivered.get() == b"hello"
    assert faults.stats.total() == 0


async def test_drop_loses_onions_silently(net: Net) -> None:
    nodes, faults, sub = net
    faults.set_node("guard", LinkFaults(drop=1.0))

    for _ in range(5):
        await _send(nodes)
    await _wait(lambda: faults.stats.dropped == 5)
    await asyncio.sleep(0.1)

    assert nodes["bob"].delivered.empty()
    assert nodes["middle"].stats.relayed == 0
    assert {(e.node_id, e.kind) for e in _faults_seen(sub)} == {("guard", "drop")}


async def test_every_corrupted_onion_is_rejected_by_the_next_hop(net: Net) -> None:
    """Acceptance §3.3.4: 100% detected, none forwarded."""
    nodes, faults, _ = net
    faults.set_link("guard", "middle", LinkFaults(corrupt=1.0))

    for i in range(20):
        await _send(nodes, bytes([i]) * 100)
    await _wait(lambda: nodes["middle"].stats.dropped_bad_layer == 20)
    await asyncio.sleep(0.1)

    assert faults.stats.corrupted == 20
    assert nodes["middle"].stats.relayed == 0
    assert nodes["exit"].stats.relayed == 0
    assert nodes["bob"].delivered.empty()


async def test_duplicates_are_dropped_as_replays(net: Net) -> None:
    nodes, faults, _ = net
    faults.set_link("exit", "bob", LinkFaults(duplicate=1.0))

    await _send(nodes, b"twice")
    async with asyncio.timeout(5):
        assert await nodes["bob"].delivered.get() == b"twice"
    await _wait(lambda: nodes["bob"].stats.dropped_replay == 1)

    assert faults.stats.duplicated == 1
    assert nodes["bob"].delivered.empty()  # the copy never reached the app


async def test_delay_slows_delivery(net: Net) -> None:
    nodes, faults, _ = net
    faults.set_node("middle", LinkFaults(delay_ms=250))
    started = time.perf_counter()

    await _send(nodes)
    async with asyncio.timeout(5):
        await nodes["bob"].delivered.get()

    assert time.perf_counter() - started >= 0.25
    assert faults.stats.delayed == 1


async def test_truncation_cuts_the_link_and_recovers(net: Net) -> None:
    nodes, faults, _ = net
    faults.set_link("guard", "middle", LinkFaults(truncate=1.0))

    await _send(nodes)
    await _wait(lambda: nodes["middle"].stats.protocol_errors == 1)
    assert nodes["bob"].delivered.empty()

    # The guard re-dials on the next send once the fault is gone
    faults.clear()
    await _send(nodes, b"after")
    async with asyncio.timeout(5):
        assert await nodes["bob"].delivered.get() == b"after"


async def test_partition_refuses_links_both_ways(net: Net) -> None:
    nodes, faults, _ = net
    faults.partition({"alice"}, {"guard"})

    with pytest.raises(HopUnreachable, match="partition"):
        await _send(nodes)

    faults.heal()
    await _send(nodes, b"healed")
    async with asyncio.timeout(5):
        assert await nodes["bob"].delivered.get() == b"healed"


async def test_partition_applies_to_existing_links(net: Net) -> None:
    nodes, faults, _ = net
    await _send(nodes)  # alice now has a pooled link to guard
    async with asyncio.timeout(5):
        await nodes["bob"].delivered.get()

    faults.partition({"alice"}, {"guard"})
    with pytest.raises(HopUnreachable):
        await _send(nodes)


async def test_blackhole_swallows_without_reporting(net: Net) -> None:
    nodes, faults, sub = net
    faults.blackhole("middle")

    for _ in range(3):
        await _send(nodes)
    await _wait(lambda: faults.stats.blackholed == 3)
    await asyncio.sleep(0.1)

    assert nodes["bob"].delivered.empty()
    # A blackhole looks healthy from outside: no drops, no errors reported
    middle = nodes["middle"].stats
    assert middle.dropped_bad_layer == middle.dropped_unreachable == 0
    assert nodes["guard"].stats.relayed == 3
    assert {e.kind for e in _faults_seen(sub)} == {"blackhole"}


# ============================================================
# REPLAY PROTECTION
# ============================================================


async def test_replayed_onion_is_dropped(net: Net) -> None:
    """An attacker re-sending a captured onion gets nothing through."""
    nodes, _, _ = net
    onion = build_onion(_route(nodes), b"once")

    await nodes["alice"].send_onion("guard", onion)
    await nodes["alice"].send_onion("guard", onion)  # replay

    async with asyncio.timeout(5):
        assert await nodes["bob"].delivered.get() == b"once"
    await _wait(lambda: nodes["guard"].stats.dropped_replay == 1)
    assert nodes["guard"].stats.relayed == 1


async def test_forged_packet_cannot_poison_the_replay_cache(net: Net) -> None:
    """Keys are recorded only after authentication succeeds."""
    nodes, _, _ = net
    real = build_onion(_route(nodes), b"real")
    forged = real[:33] + bytes(len(real) - 33)  # same one-time key, junk body

    await nodes["alice"].send_onion("guard", forged)
    await _wait(lambda: nodes["guard"].stats.dropped_bad_layer == 1)
    await nodes["alice"].send_onion("guard", real)

    async with asyncio.timeout(5):
        assert await nodes["bob"].delivered.get() == b"real"
    assert nodes["guard"].stats.dropped_replay == 0
