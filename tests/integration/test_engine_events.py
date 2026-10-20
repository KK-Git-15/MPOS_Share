"""The engine publishes a complete, consistent event stream for a transfer."""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from mpos.engine import Engine
from mpos.events import (
    CircuitBuilt,
    Event,
    HopForwarded,
    LinkUp,
    LogRecord,
    NodeStatus,
    SegmentDecoded,
    ShardAcked,
    ShardSent,
    TransferComplete,
    TransferProgress,
    TransferStarted,
)
from mpos.net.transfer import TransferConfig
from tests.helpers import free_port_block

CONFIG = TransferConfig(segment_size=64 * 1024, ack_timeout=2.0)


def _drain(queue_events: list[Event], sub: object) -> None:
    queue = sub.queue  # type: ignore[attr-defined]
    while not queue.empty():
        queue_events.append(queue.get_nowait())


async def test_transfer_event_stream(tmp_path: Path) -> None:
    engine = Engine()
    sub = engine.bus.subscribe(maxsize=100_000)
    engine.attach_logging()
    source = tmp_path / "data.bin"
    source.write_bytes(os.urandom(300_000))  # 5 segments of 64 KiB

    try:
        await engine.start_local_cluster(
            tmp_path / "cluster", relays=5, base_port=free_port_block(7), config=CONFIG
        )
        result = await engine.send_file("alice", source, "bob", CONFIG)
    finally:
        await engine.shutdown()

    events: list[Event] = []
    _drain(events, sub)
    kinds = Counter(type(e).__name__ for e in events)

    # Lifecycle: 7 nodes up and down
    statuses = [e for e in events if isinstance(e, NodeStatus)]
    assert sum(e.running for e in statuses) == 7
    assert sum(not e.running for e in statuses) == 7
    assert kinds[LinkUp.__name__] > 0

    # Both ends announce and finish the same transfer
    started = {e.role: e for e in events if isinstance(e, TransferStarted)}
    assert set(started) == {"send", "receive"}
    assert started["send"].transfer_id == started["receive"].transfer_id
    assert started["send"].segments == result.segments == 5
    done = {e.role: e for e in events if isinstance(e, TransferComplete)}
    assert done["send"].ok and done["receive"].ok
    assert done["send"].sha256 == done["receive"].sha256 == result.sha256

    # Every segment completes on both sides, progress ends at 5/5
    for node in ("alice", "bob"):
        decoded = {
            e.segment
            for e in events
            if isinstance(e, SegmentDecoded) and e.node_id == node
        }
        assert decoded == set(range(5))
        progress = [
            e for e in events if isinstance(e, TransferProgress) and e.node_id == node
        ]
        assert progress[-1].segments_done == progress[-1].segments_total == 5

    # Shards: 6 per segment sent, at least K=4 per segment acked
    assert kinds[ShardSent.__name__] >= 30
    acked = Counter(e.segment for e in events if isinstance(e, ShardAcked))
    assert all(acked[s] >= 4 for s in range(5))

    # Every shard path has 3 distinct relays; relays report the hops
    shard_paths = [e.path for e in events if isinstance(e, ShardSent)]
    assert all(len(set(p)) == 3 and p[0].startswith("r") for p in shard_paths)
    circuits = {e.kind for e in events if isinstance(e, CircuitBuilt)}
    assert {"MANIFEST", "SHARD", "MANIFEST_ACK", "SHARD_ACK", "COMPLETE"} <= circuits
    relayed = [e for e in events if isinstance(e, HopForwarded)]
    # Endpoints relay only other nodes' health probes (X -> endpoint -> X),
    # never transfer traffic
    endpoint_hops = [e for e in relayed if e.node_id in ("alice", "bob")]
    assert all(e.prev_hop == e.next_hop for e in endpoint_hops)
    assert kinds[HopForwarded.__name__] >= 3 * kinds[CircuitBuilt.__name__]

    # Log records flow through the bus too
    assert any(isinstance(e, LogRecord) and "sha256 OK" in e.message for e in events)


def test_engine_and_cli_do_not_import_qt() -> None:
    """Headless use must work without PySide6 (CLAUDE.md §3.2.3)."""
    code = (
        "import sys, mpos.cli, mpos.engine, mpos.events, mpos.net.transfer; "
        "assert not any(m.startswith('PySide6') for m in sys.modules), "
        "[m for m in sys.modules if m.startswith('PySide6')]"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0, result.stderr
