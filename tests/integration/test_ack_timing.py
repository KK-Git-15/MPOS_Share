"""Phase 9b-3: the receiver ACKs each valid shard on arrival (§3.4.2 item 7)."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mpos.engine import Engine
from mpos.errors import TransferTimeout
from mpos.events import ShardAcked
from mpos.net.messages import Message, MessageType
from mpos.net.transfer import TransferConfig
from tests.helpers import free_port_block

# The fixed window: these tests patch its send path (`_send_on`) and check
# that it reports no RTT-window fields. The rtt path has its own tests.
CONFIG = TransferConfig(
    segment_size=64 * 1024,
    ack_timeout=0.4,
    retry_backoff=0.02,
    max_retries=6,
    window_mode="fixed",
)


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[Engine]:
    eng = Engine(health_interval=None, seed=5)
    await eng.start_local_cluster(
        tmp_path / "cluster", relays=5, base_port=free_port_block(7)
    )
    yield eng
    await eng.shutdown()


def _file(tmp_path: Path, size: int) -> Path:
    path = tmp_path / "payload.bin"
    path.write_bytes(os.urandom(size))
    return path


async def test_a_lone_shard_is_acked_before_its_segment_can_decode(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ACKs waited for K shards, a lone shard would never be ACKed."""
    service = engine.service("alice")
    real = service._send_on

    async def only_shard_zero(path: list[str], dest: str, message: Message) -> None:
        if message.type is MessageType.SHARD and message.header["shard"] != 0:
            return
        await real(path, dest, message)

    monkeypatch.setattr(service, "_send_on", only_shard_zero)
    sub = engine.bus.subscribe(maxsize=100_000)
    one_try = TransferConfig(
        segment_size=64 * 1024,
        ack_timeout=0.4,
        max_retries=0,
        complete_timeout=2,
        window_mode="fixed",
    )

    with pytest.raises(TransferTimeout):
        await engine.send_file("alice", _file(tmp_path, 64 * 1024), "bob", one_try)

    acked = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if isinstance(event, ShardAcked):
            acked.append((event.segment, event.shard))
    assert acked == [(0, 0)]


async def test_duplicates_are_counted(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = engine.service("alice")
    real = service._send_on

    async def twice(path: list[str], dest: str, message: Message) -> None:
        await real(path, dest, message)
        if message.type is MessageType.SHARD:
            await real(path, dest, message)  # a fresh onion, as every send is

    monkeypatch.setattr(service, "_send_on", twice)
    source = _file(tmp_path, 4 * 64 * 1024)
    result = await engine.send_file("alice", source, "bob", CONFIG)

    received = tmp_path / "cluster" / "bob" / "inbox" / source.name
    assert hashlib.sha256(received.read_bytes()).hexdigest() == result.sha256
    # Every delivered shard arrived twice, so at least K per segment repeat
    assert engine.service("bob").stats.duplicate_shards >= 4 * result.segments


async def test_spare_shards_are_not_duplicates(engine: Engine, tmp_path: Path) -> None:
    result = await engine.send_file(
        "alice", _file(tmp_path, 6 * 64 * 1024), "bob", CONFIG
    )

    assert engine.service("bob").stats.duplicate_shards == 0
    assert (result.final_cwnd, result.peak_cwnd, result.srtt_ms) == (None, None, None)
