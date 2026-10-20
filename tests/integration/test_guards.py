"""Phase 9a entry guards on a real 7-node loopback cluster (§3.4.1)."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mpos.engine import Engine
from mpos.events import CircuitBuilt, Subscription
from mpos.net.guards import GuardConfig
from mpos.net.transfer import TransferConfig
from tests.helpers import free_port_block

GUARDED = TransferConfig(
    segment_size=64 * 1024,
    ack_timeout=0.4,
    retry_backoff=0.05,
    max_retries=12,
    complete_timeout=30,
    guards=GuardConfig(),
)
UNGUARDED = dataclasses.replace(GUARDED, guards=None)


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[Engine]:
    eng = Engine(health_interval=0.2, seed=11)
    await eng.start_local_cluster(
        tmp_path / "cluster", relays=5, base_port=free_port_block(7)
    )
    yield eng
    await eng.shutdown()


def _file(tmp_path: Path, size: int, name: str = "payload.bin") -> Path:
    path = tmp_path / name
    path.write_bytes(os.urandom(size))
    return path


def _first_hops(sub: Subscription, sender: str = "alice") -> list[str]:
    hops = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if (
            isinstance(event, CircuitBuilt)
            and event.node_id == sender
            and event.kind in ("SHARD", "MANIFEST")
        ):
            hops.append(event.path[0])
    return hops


async def _send(
    engine: Engine, tmp_path: Path, source: Path, config: TransferConfig
) -> None:
    result = await engine.send_file("alice", source, "bob", config)
    received = tmp_path / "cluster" / "bob" / "inbox" / source.name
    assert hashlib.sha256(received.read_bytes()).hexdigest() == result.sha256


def _guard_ids(engine: Engine) -> list[str]:
    guards = engine.service("alice")._guards
    assert guards is not None
    return guards.ids


async def test_every_first_hop_is_a_guard(engine: Engine, tmp_path: Path) -> None:
    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 700_000), GUARDED)

    hops = _first_hops(sub)
    guards = _guard_ids(engine)
    assert hops and set(hops) <= set(guards)
    assert engine.service("alice").stats.guard_fallbacks == 0

    path = tmp_path / "cluster" / "alice" / "guards.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_guard_killed_before_sending_is_covered(
    engine: Engine, tmp_path: Path
) -> None:
    await _send(engine, tmp_path, _file(tmp_path, 100_000, "warmup.bin"), GUARDED)
    guards = engine.service("alice")._guards
    assert guards is not None
    victim = next(g for g in guards.primaries if g != "bob")
    await engine.kill_node(victim)

    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 700_000), GUARDED)

    hops = _first_hops(sub)
    assert set(hops) <= set(guards.ids)
    assert victim not in hops[-10:]  # avoided once its failure is known


async def test_guard_set_is_stable_across_transfers_and_restart(
    engine: Engine, tmp_path: Path
) -> None:
    await _send(engine, tmp_path, _file(tmp_path, 100_000, "a.bin"), GUARDED)
    first = _guard_ids(engine)
    await _send(engine, tmp_path, _file(tmp_path, 100_000, "b.bin"), GUARDED)
    assert _guard_ids(engine) == first

    await engine.kill_node("alice")  # process-restart equivalent: state reloads
    await engine.revive_node("alice")
    await _send(engine, tmp_path, _file(tmp_path, 100_000, "c.bin"), GUARDED)
    assert _guard_ids(engine) == first


async def test_without_guards_first_hops_vary(engine: Engine, tmp_path: Path) -> None:
    """Control run: a 20-segment file uses at least 4 distinct first hops."""
    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 20 * 64 * 1024), UNGUARDED)

    assert len(set(_first_hops(sub))) >= 4
    assert engine.service("alice")._guards is None
    assert not (tmp_path / "cluster" / "alice" / "guards.json").exists()


def test_guards_are_on_by_default() -> None:
    """9a acceptance passed, so the default flipped in 9a-6 (§3.4.0)."""
    assert TransferConfig().guards == GuardConfig()


async def test_replies_end_on_one_of_the_senders_guards(
    engine: Engine, tmp_path: Path
) -> None:
    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 400_000), GUARDED)

    guards = set(_guard_ids(engine))
    replies = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if (
            isinstance(event, CircuitBuilt)
            and event.node_id == "bob"
            and event.kind in ("SHARD_ACK", "MANIFEST_ACK", "COMPLETE")
        ):
            replies.append(event.path)

    assert replies
    assert all(path[-1] in guards for path in replies)
    assert all(len(set(path)) == len(path) == 3 for path in replies)
    assert len({path[-1] for path in replies}) >= 2  # spread over the guards


async def test_reply_via_off_keeps_random_return_paths(
    engine: Engine, tmp_path: Path
) -> None:
    no_reply_via = dataclasses.replace(
        GUARDED, guards=GuardConfig(reply_via_guards=False)
    )
    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 400_000), no_reply_via)

    state = engine.service("bob")._incoming
    assert all(s.reply_via == [] for s in state.values())
    assert _first_hops(sub)  # transfer itself still used guards


# ============================================================
# 9a-5: events, failures mid-transfer, privacy, CLI
# ============================================================


def _events(sub: Subscription, kind: type) -> list[object]:
    found = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if isinstance(event, kind):
            found.append(event)
    return found


async def test_guard_events(engine: Engine, tmp_path: Path) -> None:
    from mpos.events import GuardSelected

    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 100_000, "a.bin"), GUARDED)
    selected = _events(sub, GuardSelected)

    assert len(selected) == 5
    assert {e.owner for e in selected} == {"alice"}  # type: ignore[attr-defined]
    roles = [e.role for e in selected]  # type: ignore[attr-defined]
    assert roles.count("primary") == 3 and roles.count("backup") == 2

    await _send(engine, tmp_path, _file(tmp_path, 100_000, "b.bin"), GUARDED)
    assert _events(sub, GuardSelected) == []  # unchanged set: nothing new


async def test_all_guards_down_falls_back_and_completes(
    engine: Engine, tmp_path: Path
) -> None:
    from mpos.events import GuardFallback

    await _send(engine, tmp_path, _file(tmp_path, 100_000, "a.bin"), GUARDED)
    guards = engine.service("alice")._guards
    assert guards is not None
    for guard in guards.guards:
        guard.down = True

    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 200_000, "b.bin"), GUARDED)

    assert _events(sub, GuardFallback)
    assert engine.service("alice").stats.guard_fallbacks > 0


async def test_guard_killed_mid_transfer(engine: Engine, tmp_path: Path) -> None:
    from mpos.events import ShardAcked

    await _send(engine, tmp_path, _file(tmp_path, 100_000, "warm.bin"), GUARDED)
    guards = engine.service("alice")._guards
    assert guards is not None
    victim = next(g for g in guards.primaries if g != "bob")

    sub = engine.bus.subscribe(maxsize=100_000)
    source = _file(tmp_path, 3_000_000)
    task = asyncio.create_task(engine.send_file("alice", source, "bob", GUARDED))
    acked = 0
    async with asyncio.timeout(10):
        while acked < 20:
            if isinstance(await sub.get(), ShardAcked):
                acked += 1
    await engine.kill_node(victim)

    result = await task
    received = tmp_path / "cluster" / "bob" / "inbox" / source.name
    assert hashlib.sha256(received.read_bytes()).hexdigest() == result.sha256


async def test_blackholed_guard_becomes_suspect_and_is_skipped(
    engine: Engine, tmp_path: Path
) -> None:
    await _send(engine, tmp_path, _file(tmp_path, 100_000, "warm.bin"), GUARDED)
    guards = engine.service("alice")._guards
    monitor = engine.health("alice")
    assert guards is not None and monitor is not None
    victim = next(g for g in guards.primaries if g != "bob")

    engine.faults.blackhole(victim)
    async with asyncio.timeout(5):
        while victim not in monitor.suspect:  # noqa: ASYNC110
            await asyncio.sleep(0.05)
    assert victim not in guards.usable(frozenset({"alice", "bob"}))

    sub = engine.bus.subscribe(maxsize=100_000)
    await _send(engine, tmp_path, _file(tmp_path, 700_000), GUARDED)
    assert victim not in _first_hops(sub)


async def test_no_log_line_reveals_guard_status(engine: Engine, tmp_path: Path) -> None:
    from mpos.events import LogRecord

    sub = engine.bus.subscribe(maxsize=100_000)
    engine.attach_logging()
    await _send(engine, tmp_path, _file(tmp_path, 300_000), GUARDED)
    guards = set(_guard_ids(engine))

    for record in _events(sub, LogRecord):
        message = record.message  # type: ignore[attr-defined]
        if "guard" in message.lower():
            assert not any(g in message.split() for g in guards), message


@pytest.mark.timeout(90)
def test_guards_cli_list_and_reset(tmp_path: Path) -> None:
    import signal
    import socket
    import subprocess
    import sys
    import time

    mpos = [sys.executable, "-m", "mpos"]
    base = free_port_block(7)
    cluster_dir = tmp_path / "cluster"
    source = _file(tmp_path, 300_000)
    proc = subprocess.Popen(
        [*mpos, "local-cluster", "--dir", str(cluster_dir), "--base-port", str(base)],
        # Never an unread PIPE: 64 KB of relay logs fills it and blocks the
        # cluster mid-transfer
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        for port in range(base + 1, base + 7):
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), 0.2).close()
                    break
                except OSError:
                    assert time.monotonic() < deadline
                    time.sleep(0.05)

        common = [
            "--directory",
            str(cluster_dir / "nodes.json"),
            "--data-dir",
            str(cluster_dir),
        ]
        sent = subprocess.run(
            [
                *mpos,
                "send",
                "--id",
                "alice",
                *common,
                "--bind",
                "127.0.0.1",
                "--file",
                str(source),
                "--to",
                "bob",
                "--guards",
                "3",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert sent.returncode == 0, sent.stdout + sent.stderr

        listed = subprocess.run(
            [*mpos, "guards", "list", "--id", "alice", *common],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert listed.returncode == 0, listed.stderr
        assert listed.stdout.count("primary") == 3
        assert listed.stdout.count("backup") == 2

        reset = subprocess.run(
            [*mpos, "guards", "reset", "--id", "alice", *common],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "Forgot" in reset.stdout
        assert not (cluster_dir / "alice" / "guards.json").exists()
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=15)
