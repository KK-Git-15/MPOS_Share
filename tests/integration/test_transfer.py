"""Step 6e acceptance: whole files across 7 loopback nodes, sha256 verified."""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

import mpos.net.transfer as transfer_module
from mpos.crypto.keys import generate_private_key
from mpos.crypto.onion import build_onion
from mpos.errors import IntegrityError, TransferTimeout
from mpos.net.directory import Directory, NodeInfo
from mpos.net.messages import Message, MessageType, encode_message
from mpos.net.node import NodeDaemon
from mpos.net.transfer import ReceivedFile, TransferConfig, TransferService

RELAYS = [f"r{i}" for i in range(5)]
SEGMENT = 64 * 1024

FAST = TransferConfig(
    segment_size=SEGMENT,
    ack_timeout=1.0,
    retry_backoff=0.05,
    complete_timeout=10.0,
)


@dataclass
class Net:
    nodes: dict[str, NodeDaemon]
    services: dict[str, TransferService]
    tmp: Path

    @property
    def sender(self) -> TransferService:
        return self.services["alice"]

    @property
    def receiver(self) -> TransferService:
        return self.services["bob"]

    def inbox(self, name: str) -> Path:
        return self.tmp / name / "inbox"


async def _make_net(tmp: Path, config: TransferConfig, seed: int = 1) -> Net:
    directory = Directory()
    nodes: dict[str, NodeDaemon] = {}
    services: dict[str, TransferService] = {}

    for name in ["alice", "bob", *RELAYS]:
        key = generate_private_key()
        node = NodeDaemon(name, key, directory)
        port = await node.start()
        directory.add(NodeInfo(name, "127.0.0.1", port, key.public_key()))
        nodes[name] = node

        if name in ("alice", "bob"):
            service = TransferService(
                node,
                key,
                tmp / name / "inbox",
                config=config,
                rng=random.Random(seed),
            )
            service.start()
            services[name] = service

    return Net(nodes, services, tmp)


async def _close(net: Net) -> None:
    for service in net.services.values():
        await service.stop()
    # Let in-flight ACK copies land before servers close. A peer dialing in
    # the same loop iteration as server.close() trips a harmless CPython
    # asyncio race ("Exception ignored ... _SelectorTransport.__del__").
    await asyncio.sleep(0.1)
    for node in net.nodes.values():
        await node.stop()


@pytest.fixture
async def net(tmp_path: Path) -> AsyncIterator[Net]:
    network = await _make_net(tmp_path, FAST)
    yield network
    await _close(network)


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    return path


async def _next_received(service: TransferService) -> ReceivedFile:
    async with asyncio.timeout(10):
        return await service.received.get()


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


# ============================================================
# ACCEPTANCE
# ============================================================


async def test_loopback_transfer_sha256_matches(net: Net) -> None:
    source = _write(net.tmp / "src" / "dataset.bin", 3 * 1024 * 1024 + 123)

    result = await net.sender.send_file(source, "bob")
    received = await _next_received(net.receiver)

    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.sha256 == received.sha256 == expected
    assert received.ok and received.path == net.inbox("bob") / "dataset.bin"
    assert received.path.read_bytes() == source.read_bytes()
    assert result.segments == 49
    assert result.retries == 0
    assert list(net.inbox("bob").glob("*.part")) == []

    # Shards were spread across every relay, not funnelled through one
    assert all(net.nodes[r].stats.relayed > 0 for r in RELAYS)
    # The endpoints never relayed for each other
    assert net.nodes["alice"].stats.relayed == net.nodes["bob"].stats.relayed == 0


@pytest.mark.parametrize("size", [0, 1, 3, SEGMENT, SEGMENT + 1])
async def test_edge_sizes(net: Net, size: int) -> None:
    source = _write(net.tmp / "src" / f"f{size}.bin", size)

    await net.sender.send_file(source, "bob")
    received = await _next_received(net.receiver)

    assert received.ok and received.path is not None
    assert received.path.read_bytes() == source.read_bytes()


async def test_concurrent_transfers_both_directions(net: Net) -> None:
    a = _write(net.tmp / "src" / "a.bin", 500_000)
    b = _write(net.tmp / "src" / "b.bin", 300_000)
    c = _write(net.tmp / "src" / "c.bin", 200_000)

    results = await asyncio.gather(
        net.sender.send_file(a, "bob"),
        net.sender.send_file(b, "bob"),
        net.receiver.send_file(c, "alice"),
    )

    assert len({r.transfer_id for r in results}) == 3
    assert (net.inbox("bob") / "a.bin").read_bytes() == a.read_bytes()
    assert (net.inbox("bob") / "b.bin").read_bytes() == b.read_bytes()
    assert (net.inbox("alice") / "c.bin").read_bytes() == c.read_bytes()


async def test_existing_file_is_not_overwritten(net: Net) -> None:
    net.inbox("bob").mkdir(parents=True)
    (net.inbox("bob") / "report.txt").write_text("keep me")
    source = _write(net.tmp / "src" / "report.txt", 1000)

    await net.sender.send_file(source, "bob")
    received = await _next_received(net.receiver)

    assert received.path == net.inbox("bob") / "report (1).txt"
    assert (net.inbox("bob") / "report.txt").read_text() == "keep me"


# ============================================================
# FAILURES
# ============================================================


async def test_dead_relay_is_routed_around(net: Net) -> None:
    await net.nodes["r0"].stop()  # still listed in the directory
    source = _write(net.tmp / "src" / "f.bin", 400_000)

    result = await net.sender.send_file(source, "bob")
    received = await _next_received(net.receiver)

    assert received.ok
    assert received.path is not None
    assert received.path.read_bytes() == source.read_bytes()
    # Shards through r0 were lost; K-of-N coding or retries covered them
    assert result.sends >= 6 * result.segments


async def test_too_many_dead_relays_times_out(tmp_path: Path) -> None:
    config = TransferConfig(
        segment_size=SEGMENT, ack_timeout=0.3, retry_backoff=0.01, max_retries=1
    )
    net = await _make_net(tmp_path, config)
    try:
        for relay in RELAYS[:3]:
            await net.nodes[relay].stop()

        with pytest.raises(TransferTimeout):
            await net.sender.send_file(_write(tmp_path / "f.bin", 1000), "bob")
    finally:
        await _close(net)


async def test_integrity_failure_reported_to_sender(
    net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = transfer_module.sha256_file
    calls = 0

    def sender_lies_once(path: Path) -> str:
        nonlocal calls
        calls += 1
        return "0" * 64 if calls == 1 else real(path)

    monkeypatch.setattr(transfer_module, "sha256_file", sender_lies_once)

    with pytest.raises(IntegrityError):
        await net.sender.send_file(_write(net.tmp / "src" / "x.bin", 5000), "bob")

    received = await _next_received(net.receiver)
    assert not received.ok and received.path is None
    # No partial or published file left behind (only the .mpos journal)
    assert [p.name for p in net.inbox("bob").iterdir()] == [".mpos"]


async def test_forged_ack_is_ignored(net: Net) -> None:
    await net.receiver.stop()  # bob never answers, so the manifest stays pending
    task = asyncio.create_task(
        net.sender.send_file(_write(net.tmp / "src" / "f.bin", 1000), "bob")
    )
    await _wait_until(lambda: bool(net.sender._outgoing))
    tid = next(iter(net.sender._outgoing))

    forged = Message(MessageType.MANIFEST_ACK, {"transfer_id": tid, "mac": "00" * 32})
    route = [net.nodes["bob"].directory.get(n).hop for n in ["r1", "r2", "alice"]]
    await net.nodes["bob"].send_onion("r1", build_onion(route, encode_message(forged)))

    await _wait_until(lambda: net.sender.stats.forged_acks == 1)
    assert not net.sender._outgoing[tid].manifest_ack.done()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_garbage_payload_is_dropped(net: Net) -> None:
    route = [net.nodes["alice"].directory.get(n).hop for n in ["r0", "r1", "bob"]]
    await net.nodes["alice"].send_onion("r0", build_onion(route, b"\x09garbage"))

    await _wait_until(lambda: net.receiver.stats.dropped_messages == 1)

    # Still works afterwards
    source = _write(net.tmp / "src" / "ok.bin", 100)
    await net.sender.send_file(source, "bob")


async def test_invalid_requests(net: Net, tmp_path: Path) -> None:
    source = _write(tmp_path / "f.bin", 10)

    with pytest.raises(ValueError, match="Unknown receiver"):
        await net.sender.send_file(source, "ghost")
    with pytest.raises(ValueError, match="Unknown receiver"):
        await net.sender.send_file(source, "alice")

    for relay in RELAYS[:3]:
        net.nodes["alice"].directory.remove(relay)
    with pytest.raises(ValueError, match="Need 3 relays"):
        await net.sender.send_file(source, "bob")


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        TransferConfig(k=5, n=4)
    with pytest.raises(ValueError):
        TransferConfig(segment_size=0)
    with pytest.raises(ValueError):
        TransferConfig(hops=0)
