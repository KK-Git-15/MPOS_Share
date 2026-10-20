"""Phase 8 acceptance (CLAUDE.md §3.3.4): files survive injected failures."""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mpos.engine import Engine
from mpos.events import ShardAcked
from mpos.faults.injector import LinkFaults
from mpos.net.messages import Message, MessageType
from mpos.net.transfer import TransferConfig
from tests.helpers import free_port_block

RELAYS = [f"r{i}" for i in range(5)]
FAST = TransferConfig(
    segment_size=64 * 1024,
    ack_timeout=0.4,
    retry_backoff=0.02,
    max_retries=10,
    complete_timeout=30,
)


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[Engine]:
    eng = Engine()
    eng.faults.reseed(1234)
    await eng.start_local_cluster(
        tmp_path / "cluster", relays=5, base_port=free_port_block(7), config=FAST
    )
    for node in eng.running_nodes:  # deterministic route choice per test
        eng.service(node)._rng = random.Random(node)
    yield eng
    await eng.shutdown()


def _file(tmp_path: Path, size: int) -> Path:
    path = tmp_path / "payload.bin"
    path.write_bytes(os.urandom(size))
    return path


async def _send_and_verify(
    engine: Engine, tmp_path: Path, source: Path, config: TransferConfig = FAST
) -> int:
    result = await engine.send_file("alice", source, "bob", config)
    received = tmp_path / "cluster" / "bob" / "inbox" / source.name
    assert received.read_bytes() == source.read_bytes()
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    return result.retries


async def test_n_minus_k_lost_shards_need_no_retries(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lose exactly N-K shards of every segment, with retries disabled."""
    service = engine.service("alice")
    real_send_on = service._send_on

    async def lossy(path: list[str], dest: str, message: Message) -> None:
        if message.type is MessageType.SHARD and message.header["shard"] >= 4:
            return  # shards 4 and 5 of every segment vanish
        await real_send_on(path, dest, message)

    monkeypatch.setattr(service, "_send_on", lossy)
    no_retries = TransferConfig(segment_size=64 * 1024, ack_timeout=0.4, max_retries=0)

    retries = await _send_and_verify(
        engine, tmp_path, _file(tmp_path, 1_000_000), no_retries
    )
    assert retries == 0


@pytest.mark.parametrize("victim", RELAYS)
async def test_any_single_relay_killed_mid_transfer(
    engine: Engine, tmp_path: Path, victim: str
) -> None:
    sub = engine.bus.subscribe()
    task = asyncio.create_task(
        engine.send_file("alice", _file(tmp_path, 3_000_000), "bob", FAST)
    )

    # Kill the relay once the transfer is well under way
    acked = 0
    async with asyncio.timeout(10):
        while acked < 30:
            if isinstance(await sub.get(), ShardAcked):
                acked += 1
    await engine.kill_node(victim)

    result = await task
    received = tmp_path / "cluster" / "bob" / "inbox" / "payload.bin"
    assert hashlib.sha256(received.read_bytes()).hexdigest() == result.sha256


async def test_corrupting_relay_is_routed_around(
    engine: Engine, tmp_path: Path
) -> None:
    engine.faults.set_node("r2", LinkFaults(corrupt=1.0))

    await _send_and_verify(engine, tmp_path, _file(tmp_path, 1_000_000))

    corrupted = engine.faults.stats.corrupted
    rejected = sum(engine.node(n).stats.dropped_bad_layer for n in engine.running_nodes)
    assert corrupted > 0
    assert rejected == corrupted  # every corrupted onion caught at the next hop


async def test_blackhole_relay_is_routed_around(engine: Engine, tmp_path: Path) -> None:
    engine.faults.blackhole("r3")

    retries = await _send_and_verify(engine, tmp_path, _file(tmp_path, 1_000_000))
    assert engine.faults.stats.blackholed > 0
    assert retries > 0


async def test_duplicates_everywhere_are_harmless(
    engine: Engine, tmp_path: Path
) -> None:
    engine.faults.set_global(LinkFaults(duplicate=1.0))

    await _send_and_verify(engine, tmp_path, _file(tmp_path, 500_000))
    assert engine.faults.stats.duplicated > 0


async def test_partition_cuts_sender_off_from_some_relays(
    engine: Engine, tmp_path: Path
) -> None:
    engine.faults.partition({"alice"}, {"r0", "r1"})

    await _send_and_verify(engine, tmp_path, _file(tmp_path, 500_000))
    assert engine.faults.stats.partitioned > 0


@pytest.mark.parametrize("drop", [0.05, 0.15])
async def test_random_packet_loss_on_every_link(
    engine: Engine, tmp_path: Path, drop: float
) -> None:
    engine.faults.set_global(LinkFaults(drop=drop))

    # Losses are absorbed by K-of-N coding and, when that is not enough,
    # by retries; either way the file must arrive intact
    await _send_and_verify(engine, tmp_path, _file(tmp_path, 500_000))
    assert engine.faults.stats.dropped > 0
