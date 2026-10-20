"""Phase 8b: heartbeat health, health-guided routing, resumable receiver."""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from mpos.engine import Engine
from mpos.errors import HopUnreachable
from mpos.events import (
    NodeHealth,
    SegmentDecoded,
    ShardSent,
    Subscription,
    TransferProgress,
)
from mpos.net.journal import JOURNAL_DIR
from mpos.net.transfer import TransferConfig
from tests.helpers import free_port_block

FAST = TransferConfig(
    segment_size=64 * 1024,
    ack_timeout=0.4,
    retry_backoff=0.05,
    max_retries=12,
    complete_timeout=30,
)


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[Engine]:
    eng = Engine(health_interval=0.1)
    await eng.start_local_cluster(
        tmp_path / "cluster", relays=5, base_port=free_port_block(7), config=FAST
    )
    for node in eng.running_nodes:
        eng.service(node)._rng = random.Random(node)
    yield eng
    await eng.shutdown()


async def _wait(predicate: Callable[[], bool], limit: float = 5) -> None:
    async with asyncio.timeout(limit):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.02)


def _drain(sub: Subscription) -> list[object]:
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    return events


# ============================================================
# HEARTBEATS
# ============================================================


async def test_ping_measures_rtt_and_fails_for_dead_peers(engine: Engine) -> None:
    rtt = await engine.node("alice").ping("r0")
    assert 0 < rtt < 1

    await engine.kill_node("r0")
    with pytest.raises(HopUnreachable):
        await engine.node("alice").ping("r0", limit=0.3)


async def test_killed_relay_marked_down_then_up_again(engine: Engine) -> None:
    sub = engine.bus.subscribe()
    monitor = engine.health("alice")
    assert monitor is not None
    await _wait(lambda: len(monitor.rtt_ms) == 6)  # every relay answered

    await engine.kill_node("r1")
    await _wait(lambda: "r1" in monitor.down)
    await engine.revive_node("r1")
    await _wait(lambda: "r1" not in monitor.down)

    verdicts = [
        e.up
        for e in _drain(sub)
        if isinstance(e, NodeHealth) and e.node_id == "alice" and e.peer_id == "r1"
    ]
    assert verdicts == [False, True]


async def test_partition_is_seen_only_from_the_cut_side(engine: Engine) -> None:
    engine.faults.partition({"alice"}, {"r2"})
    alice, bob = engine.health("alice"), engine.health("bob")
    assert alice is not None and bob is not None

    await _wait(lambda: "r2" in alice.down)
    await asyncio.sleep(0.3)
    assert "r2" not in bob.down


async def test_blackhole_passes_heartbeats_but_fails_probes(
    engine: Engine,
) -> None:
    """Pings cannot see a blackhole; onion probes (me -> relay -> me) can."""
    engine.faults.blackhole("r3")
    alice, bob = engine.health("alice"), engine.health("bob")
    assert alice is not None and bob is not None

    await _wait(lambda: "r3" in alice.suspect and "r3" in bob.suspect)
    assert "r3" not in alice.down  # it still answers heartbeats

    engine.faults.blackhole("r3", enabled=False)
    await _wait(lambda: "r3" not in alice.suspect)


async def test_healthy_relays_are_never_suspected(engine: Engine) -> None:
    await asyncio.sleep(0.8)  # several check rounds
    for node in ("alice", "bob"):
        monitor = engine.health(node)
        assert monitor is not None
        assert monitor.suspect == set() and monitor.down == set()


async def test_down_relays_are_avoided_before_any_loss(
    engine: Engine, tmp_path: Path
) -> None:
    monitor = engine.health("alice")
    assert monitor is not None
    await engine.kill_node("r4")
    await _wait(lambda: "r4" in monitor.down)

    sub = engine.bus.subscribe()
    source = tmp_path / "f.bin"
    source.write_bytes(os.urandom(700_000))
    result = await engine.send_file("alice", source, "bob", FAST)

    paths = [e.path for e in _drain(sub) if isinstance(e, ShardSent)]
    assert paths and all("r4" not in p for p in paths)
    assert result.retries == 0


# ============================================================
# RESUMABLE RECEIVER
# ============================================================


async def test_receiver_crash_mid_transfer_resumes(
    engine: Engine, tmp_path: Path
) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(os.urandom(4 * 1024 * 1024))
    sub = engine.bus.subscribe(maxsize=200_000)
    task = asyncio.create_task(engine.send_file("alice", source, "bob", FAST))

    decoded = 0
    async with asyncio.timeout(15):
        while decoded < 20:
            event = await sub.get()
            if isinstance(event, SegmentDecoded) and event.node_id == "bob":
                decoded += 1

    tid = next(iter(engine.service("bob")._incoming))
    file_key = engine.service("bob")._incoming[tid].file_key
    await engine.kill_node("bob")  # crash mid-transfer
    await asyncio.sleep(0.3)
    _drain(sub)
    await engine.revive_node("bob")

    result = await task

    # Progress after the restart starts from the journal, not from zero
    progress = [
        e.segments_done
        for e in _drain(sub)
        if isinstance(e, TransferProgress) and e.node_id == "bob"
    ]
    assert progress[0] >= 20
    assert progress[-1] == 64
    inbox = tmp_path / "cluster" / "bob" / "inbox"
    received = (inbox / "big.bin").read_bytes()
    assert (
        hashlib.sha256(received).hexdigest()
        == result.sha256
        == (hashlib.sha256(source.read_bytes()).hexdigest())
    )
    assert engine.service("bob").stats.resumed == 1

    # The journal never held a key
    journal_bytes = b"".join(
        p.read_bytes() for p in (inbox / JOURNAL_DIR).rglob("*") if p.is_file()
    )
    assert file_key not in journal_bytes
    assert file_key.hex().encode() not in journal_bytes


async def test_finished_transfer_survives_restart_for_status_queries(
    engine: Engine, tmp_path: Path
) -> None:
    source = tmp_path / "small.bin"
    source.write_bytes(os.urandom(100_000))
    result = await engine.send_file("alice", source, "bob", FAST)

    await engine.kill_node("bob")
    await engine.revive_node("bob")
    service = engine.service("bob")
    await _wait(lambda: result.transfer_id in service._incoming)

    state = service._incoming[result.transfer_id]
    assert state.result is not None and state.result.ok
    assert state.result.sha256 == result.sha256
    assert service.stats.resumed == 0  # finished, nothing to resume
