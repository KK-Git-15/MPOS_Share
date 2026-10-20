"""Phase 9b RTT-based window on a 7-node loopback cluster (§3.4.2).

Timing assertions compare against limits with generous margins and never
assert absolute milliseconds (CLAUDE.md §4.2).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mpos.engine import Engine
from mpos.events import ShardAcked
from mpos.faults.injector import LinkFaults
from mpos.net.congestion import CongestionConfig
from mpos.net.transfer import SendResult, TransferConfig
from tests.helpers import free_port_block

RTT = TransferConfig(
    segment_size=64 * 1024,
    ack_timeout=5.0,
    retry_backoff=0.02,
    max_backoff=0.5,
    max_retries=10,
    complete_timeout=30,
    window_mode="rtt",
    congestion=CongestionConfig(min_rto=0.3),
)
FIXED = dataclasses.replace(RTT, window_mode="fixed")


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[Engine]:
    eng = Engine(health_interval=0.5, seed=21)
    eng.faults.reseed(21)
    await eng.start_local_cluster(
        tmp_path / "cluster", relays=5, base_port=free_port_block(7)
    )
    yield eng
    await eng.shutdown()


def _file(tmp_path: Path, size: int, name: str = "payload.bin") -> Path:
    path = tmp_path / name
    path.write_bytes(os.urandom(size))
    return path


async def _send(
    engine: Engine, tmp_path: Path, source: Path, config: TransferConfig
) -> SendResult:
    result = await engine.send_file("alice", source, "bob", config)
    received = tmp_path / "cluster" / "bob" / "inbox" / source.name
    assert hashlib.sha256(received.read_bytes()).hexdigest() == result.sha256
    return result


def test_rtt_is_the_default() -> None:
    """9b acceptance passed, so the default flipped in 9b-7 (§3.4.0)."""
    assert TransferConfig().window_mode == "rtt"


async def test_rtt_mode_clean_transfer(engine: Engine, tmp_path: Path) -> None:
    result = await _send(engine, tmp_path, _file(tmp_path, 2_000_000), RTT)

    assert result.retries == 0
    assert engine.service("bob").stats.duplicate_shards == 0
    assert result.srtt_ms is not None and result.srtt_ms > 0
    assert result.final_cwnd is not None and result.peak_cwnd is not None
    assert result.peak_cwnd >= 6  # never below init_cwnd = N


async def test_memory_cap_bounds_inflight_bytes(engine: Engine, tmp_path: Path) -> None:
    cap = 60_000  # about 3 shards of a 64 KiB segment
    capped = dataclasses.replace(
        RTT, congestion=CongestionConfig(min_rto=0.3, max_inflight_bytes=cap)
    )
    result = await _send(engine, tmp_path, _file(tmp_path, 1_000_000), capped)

    assert result.peak_inflight_bytes is not None
    assert 0 < result.peak_inflight_bytes <= cap


async def test_window_grows_on_a_high_latency_path(
    engine: Engine, tmp_path: Path
) -> None:
    engine.faults.set_global(LinkFaults(delay_ms=50))

    result = await _send(engine, tmp_path, _file(tmp_path, 2_000_000), RTT)

    assert result.peak_cwnd is not None and result.peak_cwnd > 6
    assert result.srtt_ms is not None and result.srtt_ms > 150  # 4 links x 50 ms


async def test_blackholed_relay_rtt_mode(engine: Engine, tmp_path: Path) -> None:
    engine.faults.blackhole("r2")

    await _send(engine, tmp_path, _file(tmp_path, 700_000), RTT)


async def test_relay_killed_mid_transfer_rtt_mode(
    engine: Engine, tmp_path: Path
) -> None:
    sub = engine.bus.subscribe(maxsize=100_000)
    source = _file(tmp_path, 3_000_000)
    task = asyncio.create_task(engine.send_file("alice", source, "bob", RTT))
    acked = 0
    async with asyncio.timeout(15):
        while acked < 30:
            if isinstance(await sub.get(), ShardAcked):
                acked += 1
    await engine.kill_node("r3")

    result = await task
    received = tmp_path / "cluster" / "bob" / "inbox" / source.name
    assert hashlib.sha256(received.read_bytes()).hexdigest() == result.sha256


async def test_both_modes_deliver_the_same_bytes(
    engine: Engine, tmp_path: Path
) -> None:
    await _send(engine, tmp_path, _file(tmp_path, 500_000, "a.bin"), FIXED)
    await _send(engine, tmp_path, _file(tmp_path, 500_000, "b.bin"), RTT)


# ============================================================
# 9b-5: adaptive timeout catches a blackhole fast
# ============================================================


async def _blackhole_timeouts(
    tmp_path: Path, *, health_interval: float | None, config: TransferConfig
) -> tuple[list[tuple[int, float]], SendResult]:
    """(attempt, seconds from send to its "no ACK" retry) per timeout."""
    from mpos.events import ShardRetried, ShardSent

    eng = Engine(health_interval=health_interval, seed=31)
    eng.faults.reseed(31)
    await eng.start_local_cluster(
        tmp_path / "cluster", relays=5, base_port=free_port_block(7)
    )
    try:
        eng.faults.blackhole("r2")
        sub = eng.bus.subscribe(maxsize=200_000)
        source = _file(tmp_path, 1_000_000)
        result = await _send(eng, tmp_path, source, config)
    finally:
        await eng.shutdown()

    sent_at: dict[tuple[int, int], float] = {}
    waits = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        key = getattr(event, "segment", None), getattr(event, "shard", None)
        if isinstance(event, ShardSent):
            sent_at[key] = event.ts  # type: ignore[index]
        elif isinstance(event, ShardRetried) and event.reason.startswith("no ACK"):
            waits.append((event.attempt, event.ts - sent_at[key]))  # type: ignore[index]
    return waits, result


async def test_blackhole_detected_by_rtt_timeout_well_before_fixed(
    tmp_path: Path,
) -> None:
    """Health probes disabled, so only the RTT timeout can notice r2."""
    defaults = dataclasses.replace(RTT, ack_timeout=TransferConfig().ack_timeout)
    defaults = dataclasses.replace(defaults, congestion=CongestionConfig())

    waits, result = await _blackhole_timeouts(
        tmp_path, health_interval=None, config=defaults
    )

    assert waits, "some shard must have gone through the blackhole"
    fixed_timeout = TransferConfig().ack_timeout  # 10 s (D2)
    # Every timeout is under the old fixed 10 s
    assert max(w for _, w in waits) < fixed_timeout
    # A shard's first timeout fires at the rto: ~1 s once samples exist
    # (5 s before the very first sample); retries back off per shard
    first = sorted(w for attempt, w in waits if attempt == 1)
    assert first and first[len(first) // 2] < 2.0
    assert result.retries >= len(waits)


async def test_blackhole_with_default_health(tmp_path: Path) -> None:
    defaults = dataclasses.replace(
        RTT, ack_timeout=TransferConfig().ack_timeout, congestion=CongestionConfig()
    )
    await _blackhole_timeouts(tmp_path, health_interval=2.0, config=defaults)


# ============================================================
# 9b-6: CwndChanged events and the --trace-cwnd CSV
# ============================================================


async def test_cwnd_events_rate_limited_and_within_the_cap(
    engine: Engine, tmp_path: Path
) -> None:
    import time

    from mpos.events import CwndChanged

    cap = 80_000
    capped = dataclasses.replace(
        RTT, congestion=CongestionConfig(min_rto=0.3, max_inflight_bytes=cap)
    )
    engine.faults.set_global(LinkFaults(delay_ms=20))  # long enough to sample
    sub = engine.bus.subscribe(maxsize=200_000)
    started = time.monotonic()
    result = await _send(engine, tmp_path, _file(tmp_path, 1_500_000), capped)
    elapsed = time.monotonic() - started

    events = []
    while not sub.queue.empty():
        event = sub.queue.get_nowait()
        if isinstance(event, CwndChanged):
            events.append(event)
    assert events and {e.transfer_id for e in events} == {result.transfer_id}
    assert len(events) <= elapsed * 4 + 3  # <= 4 per second, plus the final one
    assert max(e.inflight_bytes for e in events) <= cap
    assert all(e.cwnd >= 2 and e.mode in ("slow_start", "avoidance") for e in events)


async def test_fixed_mode_publishes_no_cwnd_events(
    engine: Engine, tmp_path: Path
) -> None:
    from mpos.events import CwndChanged

    sub = engine.bus.subscribe(maxsize=200_000)
    await _send(engine, tmp_path, _file(tmp_path, 300_000), FIXED)

    while not sub.queue.empty():
        assert not isinstance(sub.queue.get_nowait(), CwndChanged)


@pytest.mark.timeout(120)
def test_trace_cwnd_csv_from_the_cli(tmp_path: Path) -> None:
    import csv
    import signal
    import socket
    import subprocess
    import sys
    import time

    mpos = [sys.executable, "-m", "mpos"]
    base = free_port_block(7)
    cluster_dir = tmp_path / "cluster"
    source = _file(tmp_path, 1_500_000)
    trace = tmp_path / "results" / "cwnd.csv"
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
        sent = subprocess.run(
            [
                *mpos,
                "send",
                "--id",
                "alice",
                "--directory",
                str(cluster_dir / "nodes.json"),
                "--data-dir",
                str(cluster_dir),
                "--bind",
                "127.0.0.1",
                "--file",
                str(source),
                "--to",
                "bob",
                "--segment-kib",
                "64",
                "--window",
                "rtt",
                "--trace-cwnd",
                str(trace),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert sent.returncode == 0, sent.stdout + sent.stderr
        assert "window samples" in sent.stdout
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=15)

    with trace.open() as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert list(rows[0]) == [
        "t_s",
        "cwnd",
        "inflight",
        "srtt_ms",
        "base_rtt_ms",
        "queue_est",
        "rto_ms",
        "mode",
    ]
    assert float(rows[0]["t_s"]) == 0.0


# ============================================================
# 9b-7: rtt vs fixed on the same seeded network
# ============================================================


async def _compare(
    tmp_path: Path,
    mode: str,
    setup: object,
    *,
    size: int = 2_000_000,
    during: object = None,
) -> tuple[SendResult, int]:
    """One transfer on a fresh seeded cluster; returns (result, duplicates)."""
    eng = Engine(health_interval=2.0, seed=41)
    eng.faults.reseed(41)
    folder = tmp_path / mode
    await eng.start_local_cluster(
        folder / "cluster", relays=5, base_port=free_port_block(7)
    )
    try:
        setup(eng)  # type: ignore[operator]
        config = dataclasses.replace(
            RTT,
            window_mode=mode,  # type: ignore[arg-type]
            ack_timeout=TransferConfig().ack_timeout,
            congestion=CongestionConfig(),
        )
        source = folder / "payload.bin"
        source.write_bytes(os.urandom(size))
        task = asyncio.create_task(eng.send_file("alice", source, "bob", config))
        if during is not None:
            await during(eng)  # type: ignore[operator]
        result = await task
        received = folder / "cluster" / "bob" / "inbox" / source.name
        assert hashlib.sha256(received.read_bytes()).hexdigest() == result.sha256
        return result, eng.service("bob").stats.duplicate_shards
    finally:
        await eng.shutdown()


async def test_heterogeneous_relays_rtt_no_worse_than_fixed(tmp_path: Path) -> None:
    def setup(eng: Engine) -> None:
        for relay, delay in (("r0", 10), ("r1", 60), ("r2", 120)):
            eng.faults.set_node(relay, LinkFaults(delay_ms=delay))

    fixed, _ = await _compare(tmp_path, "fixed", setup)
    rtt, _ = await _compare(tmp_path, "rtt", setup)

    assert rtt.retries <= fixed.retries + 2  # no spurious retransmit storm


async def test_slow_relay_mid_transfer_no_extra_spurious_retransmits(
    tmp_path: Path,
) -> None:
    """r1 gets +500 ms for a few seconds mid-transfer, then recovers."""

    async def slow_spell(eng: Engine) -> None:
        await asyncio.sleep(0.3)
        eng.faults.set_node("r1", LinkFaults(delay_ms=500))
        await asyncio.sleep(2.0)
        eng.faults.set_node("r1", None)

    def nothing(eng: Engine) -> None:
        eng.faults.set_global(LinkFaults(delay_ms=20))  # keep it running a while

    fixed, fixed_dups = await _compare(tmp_path, "fixed", nothing, during=slow_spell)
    rtt, rtt_dups = await _compare(tmp_path, "rtt", nothing, during=slow_spell)

    assert rtt_dups <= fixed_dups
